import argparse
import getpass
import os
import tempfile

import psycopg
from psycopg.conninfo import conninfo_to_dict

from database import (
    PASSWORD_FILE,
    DatabaseConfigurationError,
    DatabaseSchemaError,
    explain_database_error,
    get_database_url,
    inspect_database,
)


def save_password(parameters, password):
    fields = (
        parameters.get("host") or "localhost",
        parameters.get("port") or "5432",
        parameters.get("dbname") or parameters.get("user") or getpass.getuser(),
        parameters.get("user") or getpass.getuser(),
        password,
    )
    if any("\n" in value or "\r" in value for value in fields):
        raise ValueError("Перевод строки недопустим в файле паролей PostgreSQL.")
    if "," in fields[0] or "," in fields[1]:
        raise ValueError("Автонастройка поддерживает один сервер PostgreSQL.")
    line = ":".join(value.replace("\\", "\\\\").replace(":", "\\:") for value in fields)
    descriptor, filename = tempfile.mkstemp(prefix=".pgpass-", dir=PASSWORD_FILE.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(line + "\n")
        os.replace(filename, PASSWORD_FILE)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def main():
    parser = argparse.ArgumentParser(description="Проверка Python → PostgreSQL → таблицы проекта")
    parser.add_argument(
        "--setup", action="store_true",
        help="Ввести пароль локально и сохранить его в .pgpass этого проекта (права 600)",
    )
    arguments = parser.parse_args()
    try:
        parameters = conninfo_to_dict(get_database_url())
        print("Проверяется:", parameters.get("host", "local socket"),
              "порт", parameters.get("port", "5432"),
              "пользователь", parameters.get("user", getpass.getuser()),
              "база", parameters.get("dbname", "default"))
        options = {}
        if arguments.setup:
            if os.environ.get("PGPASSFILE"):
                raise DatabaseConfigurationError(
                    "Задан PGPASSFILE. Убери эту переменную перед настройкой пароля проекта."
                )
            if parameters.get("password") or os.environ.get("PGPASSWORD"):
                raise DatabaseConfigurationError(
                    "Перед --setup убери пароль из DATABASE_URL и переменной PGPASSWORD."
                )
            password = getpass.getpass("Пароль PostgreSQL (ввод скрыт): ")
            if not password:
                raise ValueError("Пароль не введён.")
            options["password"] = password
        info = inspect_database(**options)
        if arguments.setup:
            save_password(parameters, password)
            inspect_database()
            print("Пароль сохранён локально в .pgpass проекта (права 600).")
        print("OK: Python подключился к PostgreSQL.")
        print(f"Пользователь: {info['username']}; база: {info['database']}; схема: {info['schema']}")
        print("Проверены таблицы:", ", ".join(info["tables"]))
        print("Права на сохранение заявки проверены.")
        if not info["accounts_available"]:
            print("ВНИМАНИЕ: accounts пустая. Перевод требует существующих счетов отправителя и получателя.")
        print("ML-проверка пока не реализована: ML.py пустой, API сохраняет только pending-заявку.")
        return 0
    except (DatabaseConfigurationError, DatabaseSchemaError, psycopg.Error) as error:
        print("ОШИБКА:", explain_database_error(error))
        return 1
    except (ValueError, OSError) as error:
        print("ОШИБКА:", str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
