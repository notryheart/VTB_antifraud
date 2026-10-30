import logging

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from database import (
    DatabaseConfigurationError,
    DatabaseSchemaError,
    connect_database,
    explain_database_error,
    inspect_database,
)

app = FastAPI()
logger = logging.getLogger(__name__)


class TransferRequest(BaseModel):
    sender_account_id: int = Field(strict=True, gt=0, le=2**63 - 1)
    receiver_account_id: int = Field(strict=True, gt=0, le=2**63 - 1)
    amount: int = Field(strict=True, gt=0, le=2**63 - 1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


@app.get("/health/db")
def database_health():
    try:
        inspect_database()
    except (DatabaseConfigurationError, DatabaseSchemaError, psycopg.Error) as exc:
        logger.error("Database readiness failed: %s", explain_database_error(exc))
        raise HTTPException(status_code=503, detail="Database is not ready") from exc
    return {"status": "ok"}


@app.post("/assess-transfer", status_code=201)
def assess_transfer(transfer: TransferRequest):
    try:
        # The connection context commits before returning, or rolls back on error.
        with connect_database() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO transactions
                        (sender_account_id, receiver_account_id, amount, currency)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, sender_account_id, receiver_account_id,
                              amount, currency, status
                    """,
                    (
                        transfer.sender_account_id,
                        transfer.receiver_account_id,
                        transfer.amount,
                        transfer.currency,
                    ),
                )
                saved_transfer = cursor.fetchone()
    except DatabaseConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Database is not configured") from exc
    except psycopg.errors.ForeignKeyViolation as exc:
        raise HTTPException(
            status_code=422, detail="Sender or receiver account does not exist"
        ) from exc
    except psycopg.OperationalError as exc:
        logger.error("Database connection failed: %s", explain_database_error(exc))
        raise HTTPException(
            status_code=503, detail="Database is unavailable"
        ) from exc
    except psycopg.Error as exc:
        logger.error("Database operation failed: %s", explain_database_error(exc))
        raise HTTPException(
            status_code=503, detail="Database schema or permissions are not ready"
        ) from exc

    return {"message": "transfer received", "transfer": saved_transfer}
