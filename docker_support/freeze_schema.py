"""Build-time developer command: compile models to DDL WITHOUT any database."""
import json
import os
from pathlib import Path
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import CreateIndex, CreateTable
from app import models
from app.extensions import db
from scripts.migrate_historical_imports import _mysql_immutability_trigger


def main():
    target = Path("migrations/schema_v1.json")
    statements = []
    for table in db.metadata.sorted_tables:
        table.dialect_options["mysql"]["engine"] = "InnoDB"
        table.dialect_options["mysql"]["charset"] = "utf8mb4"
        table.dialect_options["mysql"]["collate"] = "utf8mb4_unicode_ci"
        statements.append(str(CreateTable(table, if_not_exists=True).compile(dialect=dialect())))
        for index in sorted(table.indexes, key=lambda item: item.name):
            statements.append(str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect())))
    for event in ("update", "delete"):
        with patch("scripts.migrate_historical_imports._quote", dialect().identifier_preparer.quote):
            statements.append(_mysql_immutability_trigger(event).replace("CREATE TRIGGER ", "CREATE TRIGGER IF NOT EXISTS ", 1))
    target.write_text(json.dumps(statements, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Frozen {len(db.metadata.tables)} model tables and historical triggers; no DB connected.")


if __name__ == "__main__":
    main()
