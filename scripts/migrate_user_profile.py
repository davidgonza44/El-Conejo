"""Additive, idempotent profile columns. Manual use: python scripts/migrate_user_profile.py --database NAME.

Never invoked at application startup. No users, passwords or roles are rewritten.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pymysql
from app.config import Config

COLUMNS = {"phone": "VARCHAR(40) NULL", "job_title": "VARCHAR(120) NULL", "bio": "VARCHAR(300) NULL"}


def migrate(connection, database):
    with connection.cursor() as cursor:
        for name, definition in COLUMNS.items():
            cursor.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='users' AND COLUMN_NAME=%s", (database, name))
            if not cursor.fetchone()[0]:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
                print(f"Added users.{name}")
            else:
                print(f"Already present: users.{name}")
    connection.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, choices=("ferreteria_conejo", "ferreteria_conejo_e2e"))
    args = parser.parse_args()
    connection = pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_USER,
                                 password=Config.DB_PASSWORD, database=args.database)
    try:
        migrate(connection, args.database)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
