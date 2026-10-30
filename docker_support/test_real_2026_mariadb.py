"""Focused checks on the separately identified real-2026 disposable Docker DB.

No schema resets, operational writes, dotenv, external services or existing DBs.
Only this execution's synthetic 2026 rows are removed after verification.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
from threading import Barrier
from unittest.mock import patch

from flask_login import login_user
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from app import create_app
from app.extensions import db
from app.models import User
from app.models.real_2026 import Real2026Daily, Real2026Import, Real2026Product
from app.services import real_2026_import_service as service
from app.services import real_2026_source_service as source
from app.services.exceptions import ApiError, ConflictError, ValidationError
from scripts.migrate_real_2026_history import TABLE_NAMES, apply_changes, validate_isolated_mariadb


def _package(tag):
    files = {name: ("synthetic-lab " + name).encode() for name in source.REQUIRED_FILES}
    files[source.SERIES_FILES["STRICT"]] = ("synthetic-lab " + tag).encode()
    return files


def _parsed(files, *, allow_revision=False):
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    calendar = [date(2026, 1, 5), date(2026, 1, 6)]
    product = dict(code="SYNTHETIC-LAB-0007", name="Artículo sintético de prueba MariaDB",
                   unit="unidad", cohort="STRICT", identity_status="CONTINUIDAD_PLAUSIBLE",
                   unit_status="UNIDAD_SIMPLE", is_service=False, included=True, reason="",
                   points=[(calendar[0], Decimal("1.250001")), (calendar[1], Decimal("0"))])
    return dict(package_hash=digest, source_hashes=hashes, coverage_start=calendar[0], coverage_end=calendar[-1],
                calendar=calendar, products=[product], summary=dict(products=1, total_points=2),
                provenance=dict(reference_match=not allow_revision, source="synthetic_disposable_mariadb_check"))


def _snapshot():
    result = {}
    for table in db.metadata.sorted_tables:
        if table.name in TABLE_NAMES:
            continue
        rows = [json.dumps(dict(row), sort_keys=True, default=str)
                for row in db.session.execute(table.select()).mappings()]
        result[table.name] = hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()
    db.session.rollback()
    return result


def _act(app, user_id, function, *args, **kwargs):
    with app.test_request_context():
        login_user(db.session.get(User, user_id))
        return function(*args, **kwargs)


def _reject(sql, parameters):
    try:
        with db.engine.begin() as connection:
            connection.execute(text(sql), parameters)
    except DBAPIError:
        return
    raise AssertionError("MariaDB aceptó una violación de integridad histórica.")


def _constraints(batch):
    product_id = Real2026Product.query.filter_by(batch_id=batch.id).one().id
    value = Real2026Daily.query.filter_by(product_id=product_id, event_date=date(2026, 1, 5)).one().quantity
    assert value == Decimal("1.250001"), "MariaDB alteró la precisión de la demanda."
    db.session.rollback()
    params = dict(batch=batch.id, product=product_id)
    cases = (
        "UPDATE historical_2026_imports SET status='invalid' WHERE id=:batch",
        "UPDATE historical_2026_imports SET confirmed_slot=1 WHERE id=:batch",
        "UPDATE historical_2026_imports SET coverage_start='2026-12-31' WHERE id=:batch",
        "UPDATE historical_2026_imports SET calendar='not-json' WHERE id=:batch",
        "UPDATE historical_2026_imports SET created_by_user_id=-99 WHERE id=:batch",
        "UPDATE historical_2026_daily SET quantity=-1 WHERE product_id=:product",
        "UPDATE historical_2026_daily SET quantity=1000000000000000000 WHERE product_id=:product",
        "INSERT INTO historical_2026_daily(product_id,event_date,quantity) VALUES(-99,'2026-01-05',1)",
        "INSERT INTO historical_2026_daily(product_id,event_date,quantity) VALUES(:product,'2026-01-05',1)",
        "INSERT INTO historical_2026_products(batch_id,code,name,unit,cohort,identity_status,unit_status,"
        "is_service,included,reason) SELECT batch_id,code,name,unit,cohort,identity_status,unit_status,"
        "is_service,included,reason FROM historical_2026_products WHERE id=:product",
    )
    for sql in cases:
        _reject(sql, params)
    return len(cases)


def _schema():
    ddl = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP ")):
            ddl.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        with db.engine.begin() as connection:
            validate_isolated_mariadb(connection)
            sql_mode = connection.execute(text("SELECT @@sql_mode")).scalar_one()
            assert "STRICT_TRANS_TABLES" in sql_mode or "STRICT_ALL_TABLES" in sql_mode
            apply_changes(connection)
            apply_changes(connection)
            inspector = inspect(connection)
            assert set(TABLE_NAMES) <= set(inspector.get_table_names())
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    assert not ddl, "La verificación idempotente emitió DDL sobre tablas ya preparadas."


def _rollback(app, user_id, function, *args):
    with patch.object(db.session, "commit", side_effect=SQLAlchemyError("synthetic rollback check")):
        try:
            _act(app, user_id, function, *args, actor_id=user_id)
        except ApiError as error:
            assert error.status_code == 500
        else:
            raise AssertionError("La inyección transaccional no se ejecutó.")
    db.session.expire_all()


def _lifecycle(app, user_id, created):
    def preview(tag, **kwargs):
        batch, token, fresh = _act(app, user_id, service.preview, _package(tag), actor_id=user_id, **kwargs)
        if fresh:
            created.append(batch.id)
        return batch, token

    batch, token = preview("initial")
    rejected = _constraints(batch)
    _rollback(app, user_id, service.confirm, batch.public_id, token)
    assert db.session.get(Real2026Import, batch.id).status == "preview"
    _act(app, user_id, service.confirm, batch.public_id, token, actor_id=user_id)
    assert not _act(app, user_id, service.confirm, batch.public_id, token, actor_id=user_id)[1]
    repeated, replay_token = preview("initial")
    assert repeated.id == batch.id and replay_token is None
    child, child_token = preview("revision", supersedes_public_id=batch.public_id,
                                 revision_reason="Corrección sintética MariaDB")
    _act(app, user_id, service.confirm, child.public_id, child_token, actor_id=user_id)
    assert db.session.get(Real2026Import, batch.id).status == "superseded"
    assert Real2026Import.query.filter_by(status="confirmed").count() == 1
    _rollback(app, user_id, service.revert, child.public_id, "Reversión sintética transaccional")
    assert db.session.get(Real2026Import, child.id).status == "confirmed"
    assert db.session.get(Real2026Import, batch.id).status == "superseded"
    _act(app, user_id, service.revert, child.public_id, "Reversión sintética MariaDB", actor_id=user_id)
    assert db.session.get(Real2026Import, batch.id).status == "confirmed"
    assert child.reverted_by_user_id == user_id and child.reverted_at and child.reversion_reason
    assert not _act(app, user_id, service.revert, child.public_id, "Repetición", actor_id=user_id)[1]
    try:
        _act(app, user_id, service.revert, batch.public_id, "", actor_id=user_id)
    except ValidationError:
        pass
    else:
        raise AssertionError("Se permitió revertir sin motivo.")
    _act(app, user_id, service.revert, batch.public_id, "Retiro inicial sintético", actor_id=user_id)
    assert Real2026Import.query.filter_by(status="confirmed").count() == 0
    assert Real2026Daily.query.filter(Real2026Daily.product_id.in_(
        db.session.query(Real2026Product.id).filter(Real2026Product.batch_id.in_(created)))).count() == 4
    return rejected


def _concurrency(app, user_id, created):
    batches = []
    for tag in ("concurrent-a", "concurrent-b"):
        batch, token, fresh = _act(app, user_id, service.preview, _package(tag), actor_id=user_id)
        assert fresh
        created.append(batch.id)
        batches.append((batch.public_id, token))
    barrier, original = Barrier(2), service._confirm_parent

    def synchronized(batch):
        original(batch)
        barrier.wait(timeout=20)

    def confirm(values):
        with app.app_context():
            try:
                _act(app, user_id, service.confirm, *values, actor_id=user_id)
                return "confirmed"
            except ConflictError:
                return "conflict"
            finally:
                db.session.remove()

    db.session.rollback()
    with patch.object(service, "_confirm_parent", side_effect=synchronized):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(confirm, batches))
    assert sorted(results) == ["confirmed", "conflict"], "La carrera de confirmación no terminó de forma segura."
    db.session.expire_all()
    assert Real2026Import.query.filter_by(status="confirmed").count() == 1


def _cleanup(created):
    db.session.rollback()
    if not created:
        return
    product_ids = db.session.query(Real2026Product.id).filter(Real2026Product.batch_id.in_(created))
    Real2026Daily.query.filter(Real2026Daily.product_id.in_(product_ids)).delete(synchronize_session=False)
    Real2026Product.query.filter(Real2026Product.batch_id.in_(created)).delete(synchronize_session=False)
    for batch_id in reversed(created):
        Real2026Import.query.filter_by(id=batch_id).delete(synchronize_session=False)
    db.session.commit()


def run_checks(app):
    """Called before the real import, only in the new guarded Docker environment."""
    from tempfile import TemporaryDirectory

    with app.app_context():
        _schema()
        assert Real2026Import.query.count() == 0, "Las pruebas sintéticas requieren histórico 2026 vacío."
        user = User.query.filter_by(role="admin").first()
        assert user is not None, "Falta el administrador temporal del laboratorio."
        user_id, before, created = user.id, _snapshot(), []
        with TemporaryDirectory(prefix="real2026-mariadb-check-") as folder:
            original_path, app.instance_path = app.instance_path, folder
            try:
                with patch.object(source, "parse_package", side_effect=_parsed):
                    rejected = _lifecycle(app, user_id, created)
                    _concurrency(app, user_id, created)
            finally:
                _cleanup(created)
                app.instance_path = original_path
        assert _snapshot() == before, "Una tabla operativa o histórica 2025 cambió."
        assert Real2026Import.query.count() == Real2026Product.query.count() == Real2026Daily.query.count() == 0
        return dict(result="PASS", engine="MariaDB", schema_idempotent=True,
                    integrity_violations_rejected=rejected, decimal_six_places_exact=True,
                    confirmation_replay_revision_reversion=True, transactional_rollback=True,
                    concurrent_first_confirmation="one_confirmed_one_conflict",
                    operational_and_2025_tables_unchanged=True, synthetic_rows_removed=True)


if __name__ == "__main__":
    from docker_support.real2026_lab import validate_lab

    validate_lab()
    print(json.dumps(run_checks(create_app()), sort_keys=True))
