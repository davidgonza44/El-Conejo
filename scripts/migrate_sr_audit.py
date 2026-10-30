"""Additive SR integrity migration. Guarded DEMO 5001 only; never repairs history.

Without --apply: verifies data and describes missing schema. Can be run twice.
"""
import argparse
import sys
from pathlib import Path

from sqlalchemy import inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

COLUMNS = {
    'cancelled_by_user_id': 'INTEGER NULL', 'cancelled_at': 'DATETIME NULL',
    'idempotency_key': 'VARCHAR(80) NULL',
}
UNIQUES = (
    ('replenishment_requests', 'uq_sr_order', 'purchase_order_id'),
    ('replenishment_requests', 'uq_sr_creator_retry', 'created_by_user_id,idempotency_key'),
    ('replenishment_request_lines', 'uq_sr_product', 'request_id,product_id'),
    ('supplier_quote_lines', 'uq_supplier_quote_product', 'quote_id,product_id'),
)
CHECKS = (
    ('replenishment_requests', 'ck_sr_status', "status IN ('open','processing','fulfilled','cancelled')"),
    ('replenishment_request_lines', 'ck_sr_quantity', 'quantity > 0'),
)
FOREIGN_KEYS = (
    ('replenishment_requests', 'created_by_user_id', 'users'),
    ('replenishment_requests', 'processed_by_user_id', 'users'),
    ('replenishment_requests', 'cancelled_by_user_id', 'users'),
    ('replenishment_requests', 'purchase_order_id', 'purchase_orders'),
    ('replenishment_request_lines', 'request_id', 'replenishment_requests'),
    ('replenishment_request_lines', 'product_id', 'products'),
    ('purchase_orders', 'replenishment_request_id', 'replenishment_requests'),
    ('purchase_orders', 'supplier_quote_id', 'supplier_quotes'),
    ('supplier_quotes', 'supplier_id', 'suppliers'),
    ('supplier_quotes', 'created_by_user_id', 'users'),
    ('supplier_quotes', 'replaces_quote_id', 'supplier_quotes'),
    ('supplier_quote_lines', 'quote_id', 'supplier_quotes'),
    ('supplier_quote_lines', 'product_id', 'products'),
)


def verify_data(connection):
    for table, _, columns in UNIQUES:
        if any(name not in {col['name'] for col in inspect(connection).get_columns(table)}
               for name in columns.split(',')):
            continue
        present = ' AND '.join(f'{col} IS NOT NULL' for col in columns.split(','))
        duplicate = connection.execute(text(f'SELECT 1 FROM {table} WHERE {present} '
            f'GROUP BY {columns} HAVING COUNT(*) > 1 LIMIT 1')).first()
        if duplicate:
            raise RuntimeError(f'Historical duplicates in {table}; migration refuses repair.')
    for table, _, predicate in CHECKS:
        if connection.execute(text(f'SELECT 1 FROM {table} WHERE NOT ({predicate}) LIMIT 1')).first():
            raise RuntimeError(f'Historical values conflict in {table}; migration refuses repair.')
    for table, column, parent in FOREIGN_KEYS:
        if column not in {col['name'] for col in inspect(connection).get_columns(table)}:
            continue
        orphan = connection.execute(text(f'SELECT 1 FROM {table} child LEFT JOIN {parent} parent '
            f'ON child.{column}=parent.id WHERE child.{column} IS NOT NULL AND parent.id IS NULL LIMIT 1')).first()
        if orphan:
            raise RuntimeError(f'Historical orphan in {table}.{column}; migration refuses repair.')


def statements(connection):
    result = []
    columns = {col['name'] for col in inspect(connection).get_columns('replenishment_requests')}
    for name, ddl in COLUMNS.items():
        if name not in columns:
            result.append(f'ALTER TABLE replenishment_requests ADD COLUMN {name} {ddl}')
    for table, name, columns in UNIQUES:
        existing = inspect(connection).get_unique_constraints(table)
        if not any(row['column_names'] == columns.split(',') for row in existing):
            result.append(f'ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({columns})')
    for table, name, predicate in CHECKS:
        if not any(row['name'] == name for row in inspect(connection).get_check_constraints(table)):
            result.append(f'ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({predicate})')
    for table, column, parent in FOREIGN_KEYS:
        if not any(row['constrained_columns'] == [column] and row['referred_table'] == parent
                   for row in inspect(connection).get_foreign_keys(table)):
            result.append(f'ALTER TABLE {table} ADD CONSTRAINT fk_audit_{table}_{column} '
                          f'FOREIGN KEY ({column}) REFERENCES {parent}(id) ON DELETE RESTRICT')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    from docker_support.guard import validate_demo
    from docker_support.bootstrap import engine
    from docker_support.demo import require_ready
    validate_demo()
    target = engine()
    try:
        with target.begin() as connection:
            require_ready(connection)
            verify_data(connection)
            ddl = statements(connection)
            if args.apply:
                for statement in ddl:
                    connection.execute(text(statement))
                assert not statements(connection), 'Incomplete additive schema'
            print(f"{'APPLIED' if args.apply else 'PLAN'}: {len(ddl)} additive statements; history unchanged.")
    finally:
        target.dispose()


if __name__ == '__main__':
    main()
