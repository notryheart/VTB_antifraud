import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

PROJECT_DIR = Path(__file__).resolve().parent
PASSWORD_FILE = PROJECT_DIR / ".pgpass"
load_dotenv(PROJECT_DIR / ".env", override=False)

EXPECTED_COLUMNS = {
    "users": ("id", "created_at"),
    "accounts": ("id", "user_id", "created_at"),
    "transactions": (
        "id", "sender_account_id", "receiver_account_id", "amount", "currency",
        "created_at", "status",
    ),
    "risk_assessments": (
        "id", "transaction_id", "risk_score", "model_version", "assessed_at",
    ),
    "fraud_labels": ("transaction_id", "is_fraud", "labelled_at"),
}


class DatabaseConfigurationError(Exception):
    pass


class DatabaseSchemaError(Exception):
    pass


def get_database_url():
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise DatabaseConfigurationError("Не задан DATABASE_URL. Создай .env по образцу .env.example.")
    return value


@contextmanager
def connect_database(**overrides):
    database_url = get_database_url()
    options = {"row_factory": dict_row, "connect_timeout": 5}
    if (PASSWORD_FILE.is_file() and not os.environ.get("PGPASSFILE")
            and not conninfo_to_dict(database_url).get("passfile")):
        options["passfile"] = str(PASSWORD_FILE)
    options.update(overrides)
    connection = psycopg.connect(database_url, **options)
    try:
        with connection as active_connection:
            yield active_connection
    finally:
        connection.close()


def inspect_database(**connect_options):
    with connect_database(**connect_options) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        info = connection.execute(
            "SELECT current_user AS username, current_database() AS database, "
            "current_schema() AS schema, inet_server_port() AS port"
        ).fetchone()
        for table, columns in EXPECTED_COLUMNS.items():
            connection.execute(
                sql.SQL("SELECT {} FROM {} LIMIT 0").format(
                    sql.SQL(", ").join(map(sql.Identifier, columns)),
                    sql.Identifier(table),
                )
            )
        privileges = connection.execute(
            "SELECT has_table_privilege('transactions', 'INSERT') AS can_insert, "
            "pg_get_serial_sequence('transactions', 'id') AS sequence, "
            "(SELECT attidentity FROM pg_attribute "
            "WHERE attrelid = 'transactions'::regclass AND attname = 'id') AS identity"
        ).fetchone()
        if not privileges["can_insert"]:
            raise DatabaseSchemaError("No INSERT permission on transactions")
        # Identity columns generate IDs internally; serial defaults use nextval().
        if (privileges["sequence"] and not privileges["identity"]
                and not connection.execute(
            "SELECT has_sequence_privilege(%s, 'USAGE') "
            "OR has_sequence_privilege(%s, 'UPDATE') AS allowed",
            (privileges["sequence"], privileges["sequence"]),
        ).fetchone()["allowed"]):
            raise DatabaseSchemaError("No permission on the transaction ID sequence")
        info["accounts_available"] = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM accounts) AS available"
        ).fetchone()["available"]
        info["tables"] = list(EXPECTED_COLUMNS)
        return info


def explain_database_error(error):
    if isinstance(error, DatabaseConfigurationError):
        return str(error)
    if isinstance(error, DatabaseSchemaError):
        return str(error)
    if isinstance(error, psycopg.errors.UndefinedTable):
        return "В выбранной базе нет нужных таблиц. Проверь имя базы и mainDB.sql."
    if isinstance(error, psycopg.errors.UndefinedColumn):
        return "Структура таблиц отличается от mainDB.sql. Проверь схему базы."
    if isinstance(error, psycopg.errors.InsufficientPrivilege):
        return "Пользователю PostgreSQL не хватает прав на таблицы или последовательности."
    message = str(error).lower()
    if "no password supplied" in message:
        return "Python не получил пароль PostgreSQL. Выполни python check_db.py --setup."
    if "password authentication failed" in message:
        return "PostgreSQL отклонил имя пользователя или пароль. Выполни python check_db.py --setup."
    if "does not exist" in message and "database" in message:
        return "Указанная база не существует. Проверь имя в pgAdmin и DATABASE_URL."
    if "could not translate host name" in message:
        return "Не найден сервер PostgreSQL. Формат адреса: postgresql://USER@localhost:5432/DB."
    return "Соединение или запрос PostgreSQL завершились ошибкой. Проверь сервер, адрес и схему базы."
