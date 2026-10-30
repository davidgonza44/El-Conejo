"""One-time, idempotent correction of the synthetic DEMO quote expiry placeholder."""
from datetime import date, timedelta
from sqlalchemy import text
from docker_support.guard import validate_demo
from docker_support.bootstrap import engine, identity


def main():
    validate_demo()
    target = engine()
    with target.begin() as connection:
        if not identity(connection).ready:
            raise RuntimeError('DEMO installation is not ready.')
        rows = connection.execute(text(
            'SELECT id, issued_on FROM supplier_quotes WHERE valid_until=:placeholder FOR UPDATE'),
            {'placeholder': date(2099, 1, 1)}).all()
        for row in rows:
            connection.execute(text('UPDATE supplier_quotes SET valid_until=:expiry WHERE id=:id'),
                               {'id': row.id, 'expiry': row.issued_on + timedelta(days=30)})
        print(f'DEMO: {len(rows)} placeholder expiries corrected to issued_on + 30 days.')
    target.dispose()


if __name__ == '__main__':
    main()
