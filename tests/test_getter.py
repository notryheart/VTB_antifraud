import os
import unittest
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

from getter import app


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.payload = {
            "sender_account_id": 1,
            "receiver_account_id": 2,
            "amount": 15000,
            "currency": "RUB",
        }
        self.environment = patch.dict(
            os.environ, {"DATABASE_URL": "postgresql://localhost/antifraud"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.connect_patch = patch("getter.psycopg.connect")
        self.connect = self.connect_patch.start()
        self.addCleanup(self.connect_patch.stop)
        self.connection = self.connect.return_value.__enter__.return_value
        self.cursor = self.connection.cursor.return_value.__enter__.return_value

    def test_persists_transfer_and_returns_database_result(self):
        saved = {"id": 7, **self.payload, "status": "pending"}
        self.cursor.fetchone.return_value = saved

        response = self.client.post("/assess-transfer", json=self.payload)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["transfer"], saved)
        query, parameters = self.cursor.execute.call_args.args
        self.assertIn("INSERT INTO transactions", query)
        self.assertIn("RETURNING", query)
        self.assertEqual(parameters, (1, 2, 15000, "RUB"))
        self.connect.return_value.__exit__.assert_called_once_with(None, None, None)

    def test_invalid_payloads_do_not_connect(self):
        invalid_payloads = [
            {**self.payload, "amount": 0},
            {**self.payload, "amount": -1},
            {**self.payload, "amount": 2**63},
            {**self.payload, "amount": True},
            {**self.payload, "amount": "15000"},
            {**self.payload, "sender_account_id": False},
            {**self.payload, "sender_account_id": 0},
            {**self.payload, "receiver_account_id": 2**63},
            {**self.payload, "currency": "rub"},
            {**self.payload, "currency": "RUBB"},
            {
                key: value
                for key, value in self.payload.items()
                if key != "receiver_account_id"
            },
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post("/assess-transfer", json=payload)
                self.assertEqual(response.status_code, 422)
        self.connect.assert_not_called()

    def test_unknown_account_is_rejected(self):
        self.cursor.execute.side_effect = psycopg.errors.ForeignKeyViolation()

        response = self.client.post("/assess-transfer", json=self.payload)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"], "Sender or receiver account does not exist"
        )
        self.assertIsNotNone(self.connect.return_value.__exit__.call_args.args[0])

    def test_missing_configuration_does_not_connect(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/assess-transfer", json=self.payload)

        self.assertEqual(response.status_code, 503)
        self.connect.assert_not_called()

    def test_database_error_does_not_expose_connection_details(self):
        self.connect.side_effect = psycopg.OperationalError("private connection data")

        response = self.client.post("/assess-transfer", json=self.payload)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Database is unavailable")

    def test_commit_failure_closes_connection_and_does_not_return_201(self):
        self.cursor.fetchone.return_value = {"id": 7, **self.payload, "status": "pending"}
        self.connect.return_value.__exit__.side_effect = psycopg.OperationalError("commit failed")

        response = self.client.post("/assess-transfer", json=self.payload)

        self.assertEqual(response.status_code, 503)
        self.connect.return_value.close.assert_called_once()

    def test_wrong_schema_is_a_service_error(self):
        self.cursor.execute.side_effect = psycopg.errors.UndefinedTable()

        response = self.client.post("/assess-transfer", json=self.payload)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Database schema or permissions are not ready")


if __name__ == "__main__":
    unittest.main()
