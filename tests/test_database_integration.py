"""Real PostgreSQL tests. TEST_DATABASE_URL must permit temporary schemas."""

import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from getter import app

PROJECT_DIR = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class DatabaseIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_url = os.environ["TEST_DATABASE_URL"]
        cls.schema = "antifraud_test_" + uuid4().hex
        with psycopg.connect(cls.base_url) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
        cls.addClassCleanup(cls.drop_schema)
        cls.url = make_conninfo(cls.base_url, options=f"-csearch_path={cls.schema}")
        with psycopg.connect(cls.url) as connection:
            connection.execute((PROJECT_DIR / "mainDB.sql").read_text())

    @classmethod
    def drop_schema(cls):
        with psycopg.connect(cls.base_url) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)))

    def setUp(self):
        self.environment = patch.dict(os.environ, {"DATABASE_URL": self.url})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(app)
        with psycopg.connect(self.url) as connection:
            connection.execute("TRUNCATE users RESTART IDENTITY CASCADE")
            for _ in range(2):
                user_id = connection.execute("INSERT INTO users DEFAULT VALUES RETURNING id").fetchone()[0]
                connection.execute("INSERT INTO accounts (user_id) VALUES (%s)", (user_id,))
        self.payload = {
            "sender_account_id": 1, "receiver_account_id": 2,
            "amount": 15000, "currency": "RUB",
        }

    def transaction_count(self):
        with psycopg.connect(self.url) as connection:
            return connection.execute("SELECT count(*) FROM transactions").fetchone()[0]

    def test_post_commits_to_real_postgres(self):
        response = self.client.post("/assess-transfer", json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        saved = response.json()["transfer"]
        # A second connection must see the committed record after HTTP 201.
        with psycopg.connect(self.url) as connection:
            record = connection.execute(
                "SELECT sender_account_id, receiver_account_id, amount, currency, status "
                "FROM transactions WHERE id = %s", (saved["id"],),
            ).fetchone()
            assessments = connection.execute("SELECT count(*) FROM risk_assessments").fetchone()[0]
        self.assertEqual(record, (1, 2, 15000, "RUB", "pending"))
        self.assertEqual(assessments, 0)

    def test_unknown_account_rolls_back_and_next_request_works(self):
        invalid = {**self.payload, "receiver_account_id": 999999}
        response = self.client.post("/assess-transfer", json=invalid)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.transaction_count(), 0)
        response = self.client.post("/assess-transfer", json=self.payload)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.transaction_count(), 1)

    def test_deferred_commit_failure_never_returns_success(self):
        with psycopg.connect(self.url) as connection:
            connection.execute(
                "ALTER TABLE transactions ALTER CONSTRAINT "
                "transactions_receiver_account_id_fkey DEFERRABLE INITIALLY DEFERRED"
            )
        try:
            response = self.client.post(
                "/assess-transfer", json={**self.payload, "receiver_account_id": 999999},
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual(self.transaction_count(), 0)
        finally:
            with psycopg.connect(self.url) as connection:
                connection.execute(
                    "ALTER TABLE transactions ALTER CONSTRAINT "
                    "transactions_receiver_account_id_fkey NOT DEFERRABLE"
                )

    def test_readiness_checks_real_schema_without_inserting(self):
        response = self.client.get("/health/db")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(self.transaction_count(), 0)

    def test_wrong_schema_returns_503_instead_of_unhandled_500(self):
        wrong_url = make_conninfo(self.base_url, options="-csearch_path=missing_antifraud_schema")
        with patch.dict(os.environ, {"DATABASE_URL": wrong_url}):
            self.assertEqual(self.client.get("/health/db").status_code, 503)
            self.assertEqual(self.client.post("/assess-transfer", json=self.payload).status_code, 503)
        self.assertEqual(self.transaction_count(), 0)

    def test_cli_uses_the_same_database_configuration(self):
        result = subprocess.run(
            [sys.executable, str(PROJECT_DIR / "check_db.py")],
            cwd=PROJECT_DIR, env=os.environ.copy(), capture_output=True, text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(self.schema, result.stdout)
        self.assertEqual(self.transaction_count(), 0)


if __name__ == "__main__":
    unittest.main()
