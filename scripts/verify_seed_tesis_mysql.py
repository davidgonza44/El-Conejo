"""Read-only validation of the approved thesis dataset and real application pages."""
from collections import Counter
from datetime import datetime
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import seed_tesis_safety as safety
import audit_seed_tesis as audit
from seed_tesis_data import commercial_plan, plan_summary
from test_seed_tesis import SeedTests


def plain_rows(domain, name, ids):
    from sqlalchemy import select
    table = domain.db.metadata.tables[name]
    return domain.db.session.execute(select(table).where(table.c.id.in_(ids)).order_by(table.c.id)).mappings().all()


def validate(domain):
    from sqlalchemy import select, func
    record = dict(domain.db.session.execute(select(safety.manifest_table())).mappings().one())
    safety.validate_manifest(record)
    expected = plan_summary(commercial_plan())
    for name in safety.TABLES:
        table = domain.db.metadata.tables[name]
        count = domain.db.session.execute(select(func.count()).select_from(table)).scalar()
        safety.require(count == expected[name], f'Conteo diferente: {name}: {count}, esperado {expected[name]}')
        rows = plain_rows(domain, name, record['payload']['rows'][name])
        safety.require(safety.digest([dict(r) for r in rows]) == record['payload']['hashes'][name], f'Huella diferente: {name}')
        print(f'{name}: {count}')
    for table in domain.db.metadata.tables.values():
        for fk in table.foreign_keys:
            source = fk.parent
            parent = fk.column.table.alias('referenced_row')
            target = parent.c[fk.column.name]
            query = select(func.count()).select_from(table.outerjoin(parent, source == target))
            query = query.where(source.is_not(None), target.is_(None))
            safety.require(domain.db.session.execute(query).scalar() == 0, f'Referencia rota: {table.name}.{source.name}')
    case = SeedTests()
    case.domain, case.manifest = domain, record
    with patch.object(safety, 'select_rows', plain_rows):
        for name in ('test_plan_and_balances', 'test_visible_fields_and_inactive_actor', 'test_states_and_payments',
                     'test_document_links_and_totals', 'test_history_and_no_scientific_data',
                     'test_predictive_source_and_eligibility', 'test_predictive_variety_across_categories',
                     'test_independent_sales_payments_customers_and_responsibles'):
            getattr(case, name)()
            print(name + ': OK')
    rows = audit.analyses(domain)
    statuses = Counter(r['eligibility'].status for r in rows)
    safety.require(statuses == dict(ELIGIBLE=18, INTERMITTENT=60, INSUFFICIENT_HISTORY=13, INACTIVE=12, NO_DEMAND=17),
                   f'Distribución distinta: {statuses}')
    needed = sum(bool(r['recommendation'] and r['recommendation'].needed) for r in rows)
    safety.require(needed == 42, f'Reabastecimiento diferente: {needed}')
    counts, hits, records, cells = audit.scan(domain)
    safety.require(not hits, 'Etiquetas prohibidas persistidas.')
    print('Integridad referencial, huellas y cronología: OK')
    audit.print_summary(rows)
    audit.print_categories(domain, rows)
    print(f'Etiquetas: 0 coincidencias; {records} registros, {cells} campos.')
    return record


def check_pages(app, domain):
    from app.services import session_service
    principal = domain.models.User(id=1, name='Verificación local', role='admin', is_active=True,
        username='verification', email='verification@example.invalid', password_hash=None)
    app.login_manager.request_loader(lambda request: principal)
    record = SimpleNamespace(id=0, is_valid=True, last_seen_at=datetime.utcnow())
    paths = ('/dashboard', '/products', '/inventory', '/sales', '/sales/quotes', '/sales/orders', '/reports',
             '/predictive/demand', '/predictive/replenishment', '/predictive/critical', '/predictive/trends',
             '/predictive/accuracy', '/api/dashboard/summary', '/reports/sales')
    with patch.object(session_service, 'current_record', return_value=record):
        client = app.test_client()
        for path in paths:
            response = client.get(path)
            if path == '/reports':
                safety.require(response.status_code == 302 and response.location == '/dashboard',
                               'Redirección de reportes inesperada.')
                response = client.get(response.location)
                print('GET /reports: 302 -> /dashboard')
            safety.require(response.status_code == 200, f'Ruta {path}: HTTP {response.status_code}')
            safety.require(b'QueuePool' not in response.data and b'SQLAlchemyError' not in response.data,
                           f'Error en respuesta: {path}')
            print(f'GET {path}: 200')


def main():
    from sqlalchemy import event, text
    url = safety.configured_url()
    domain = safety.load_domain()
    from app import create_app
    from app.config import Config
    with tempfile.TemporaryDirectory(prefix='tesis-http-', ignore_cleanup_errors=True) as folder:
        class LocalConfig(Config):
            SQLALCHEMY_DATABASE_URI = url
            SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'init_command': 'SET SESSION TRANSACTION READ ONLY'}}
            SECRET_KEY = 'local-read-only-validation'
            TESTING = True
            DEBUG = False
            PREDICTIVE_CACHE_DIR = folder
        app = create_app(LocalConfig)
        app.instance_path = folder
        with app.app_context():
            engine = domain.db.engine
            @event.listens_for(engine, 'before_cursor_execute')
            def read_only_guard(connection, cursor, statement, parameters, context, executemany):
                command = statement.lstrip().split(None, 1)[0].upper()
                safety.require(command in ('SELECT', 'SHOW', 'DESCRIBE'), 'La verificación intentó escribir SQL.')
            safety.require(domain.db.session.execute(text('SELECT DATABASE()')).scalar() == safety.DATABASE,
                           'Base efectiva incorrecta.')
            validate(domain)
            check_pages(app, domain)
            jobs = app.extensions.get('predictive_jobs')
            if jobs:
                jobs.shutdown(timeout=5)
                if jobs.thread and jobs.thread.is_alive():
                    print('Worker operativo detenido al salir del proceso; caché temporal sin uso científico.')
            domain.db.session.remove()
            engine.dispose()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except safety.SeedError as exc:
        print(f'VALIDACIÓN DETENIDA: {exc}', file=sys.stderr)
        raise SystemExit(1)
