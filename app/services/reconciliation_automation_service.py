"""Server-owned cut capture and delayed CSV ingestion; never changes operational stock."""
import hashlib
import os
import re
import shutil
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.models import Product
from app.models.reconciliation import ReconciliationCut, ReconciliationCutStock, ReconciliationRun
from app.services import notification_service, reconciliation_service
from app.services import reconciliation_intake_service as intake
from app.services.exceptions import ValidationError

CARACAS = ZoneInfo("America/Caracas")
TIMES = {"0800": time(8), "1200": time(12), "1600": time(16), "1830": time(18, 30)}
SOURCE = "legacy_inventory"
PUNCTUALITY = timedelta(minutes=2)
_NAME = re.compile(r"^inventario_\d{4}-\d{2}-\d{2}_(0800|1200|1600|1830)\.csv$")


def scheduled_at(day: date, slot: str) -> datetime:
    if slot not in TIMES:
        raise ValueError("Invalid inventory cut slot")
    return datetime.combine(day, TIMES[slot], CARACAS).astimezone(timezone.utc).replace(tzinfo=None)


def filename_for(cut: ReconciliationCut) -> str:
    local = cut.scheduled_for.replace(tzinfo=timezone.utc).astimezone(CARACAS)
    slot = local.strftime("%H%M")
    name = f"inventario_{local:%Y-%m-%d}_{slot}.csv"
    if not _NAME.fullmatch(name):
        raise ValueError("Invalid cut filename")
    return name


def _root(key: str) -> Path:
    return intake.paths()[key.removeprefix('RECONCILIATION_').removesuffix('_PATH')]


def _inbox_file(cut: ReconciliationCut) -> Path:
    root = _root("RECONCILIATION_INBOX_PATH")
    path = root / filename_for(cut)
    # Resolve even when absent; reject symlinks/reparse points escaping the server-owned directory.
    if path.resolve().parent != root:
        raise RuntimeError("Archivo fuera de la carpeta de intercambio.")
    return path


def ensure_cut(day: date, slot: str) -> ReconciliationCut:
    when = scheduled_at(day, slot)
    cut = ReconciliationCut.query.filter_by(source=SOURCE, scheduled_for=when).first()
    if cut is not None:
        return cut
    try:
        cut = ReconciliationCut(source=SOURCE, scheduled_for=when)
        db.session.add(cut)
        db.session.commit()
        return cut
    except IntegrityError:
        db.session.rollback()
        return ReconciliationCut.query.filter_by(source=SOURCE, scheduled_for=when).one()


def _verify_mariadb(connection) -> None:
    """Fail closed; SQLite's isolation behavior cannot certify production capture."""
    if connection.dialect.name != "mysql" or not connection.dialect.is_mariadb:
        raise RuntimeError("La captura requiere MariaDB/InnoDB.")
    version = connection.exec_driver_sql("SELECT VERSION()").scalar()
    isolation = connection.exec_driver_sql("SELECT @@session.tx_isolation").scalar()
    autocommit = connection.exec_driver_sql("SELECT @@session.autocommit").scalar()
    engines = dict(connection.execute(text(
        "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN "
        "('products','reconciliation_cuts','reconciliation_cut_stock')")).all())
    if "MariaDB" not in version or isolation.upper().replace("_", "-") != "REPEATABLE-READ":
        raise RuntimeError("La captura requiere MariaDB con aislamiento REPEATABLE READ.")
    if autocommit != 0:
        raise RuntimeError("La captura requiere autocommit desactivado.")
    if any(engines.get(name, "").upper() != "INNODB" for name in
           ("products", "reconciliation_cuts", "reconciliation_cut_stock")):
        raise RuntimeError("La captura requiere tablas InnoDB.")
    connection.commit()  # All preflight reads are outside the actual snapshot transaction.


def _capture_snapshot(connection, cut_id: int, scheduled_for: datetime) -> None:
    # No locking SELECT/INSERT SELECT: all catalogue reads use this one InnoDB view.
    with connection.begin():
        connection.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        started_at = datetime.utcnow()
        if started_at < scheduled_for:
            return  # An early launch must not poison the later punctual capture.
        if started_at >= scheduled_for + PUNCTUALITY:
            connection.execute(text("UPDATE reconciliation_cuts SET snapshot_status='unavailable', "
                                    "status='failed', failure_reason=:reason WHERE id=:id AND snapshot_status='pending'"),
                               {"id": cut_id, "reason": "No se capturó la fotografía local dentro del horario previsto."})
            return
        rows = connection.execute(select(Product.id, Product.code, Product.current_stock).order_by(Product.id)).all()
        if rows:
            connection.execute(ReconciliationCutStock.__table__.insert(), [
                {"cut_id": cut_id, "product_id": row.id, "product_code_snapshot": row.code,
                 "quantity": row.current_stock} for row in rows])
        connection.execute(text("UPDATE reconciliation_cuts SET snapshot_status='captured', "
                                "status='waiting_source', failure_reason=NULL, "
                                "local_snapshot_at=:at WHERE id=:id AND snapshot_status='pending'"),
                           {"id": cut_id, "at": started_at})


def capture_cut(day: date, slot: str) -> ReconciliationCut:
    """GET_LOCK serializes capture; START is first SQL in the consistent-view transaction."""
    activation = current_app.config.get("RECONCILIATION_ACTIVATED_ON", "")
    if not activation or day < date.fromisoformat(activation):
        raise RuntimeError("Configure una fecha de activación válida antes de capturar cortes.")
    if not slot_enabled(scheduled_at(day, slot)):
        raise RuntimeError("El horario es anterior al instante de activación; no se reconstruyen cortes.")
    cut = ensure_cut(day, slot)
    if cut.snapshot_status != "pending":
        return cut
    cut_id, when = cut.id, cut.scheduled_for
    db.session.rollback()  # End any ORM read transaction before the dedicated capture connection.
    key = "reconciliation:" + hashlib.sha1(f"{SOURCE}:{when.isoformat()}".encode()).hexdigest()
    if db.engine.dialect.name != "mysql":
        raise RuntimeError("La captura requiere MariaDB/InnoDB.")
    # SQLAlchemy restores the pool connection's original isolation on close.
    # Set this before autobegin; do not change the application/global engine.
    with db.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        _verify_mariadb(connection)
        held = connection.execute(text("SELECT GET_LOCK(:key, 0)"), {"key": key}).scalar()
        connection.commit()
        if held != 1:
            return cut  # A concurrent capture owns this exact cut; next read observes its result.
        try:
            # Recheck under the advisory lock, before opening the consistent-view transaction.
            state = connection.execute(text("SELECT snapshot_status FROM reconciliation_cuts WHERE id=:id"),
                                       {"id": cut_id}).scalar_one()
            connection.commit()
            if state != "pending":
                return db.session.get(ReconciliationCut, cut_id)
            try:
                _capture_snapshot(connection, cut_id, when)
            except (RuntimeError, SQLAlchemyError):
                _record_capture_failure(cut_id)
                raise
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:key)"), {"key": key})
            connection.commit()
    db.session.rollback()
    cut = db.session.get(ReconciliationCut, cut_id)
    notify_cut(cut)
    return cut


def slot_enabled(when):
    activated = current_app.config.get("RECONCILIATION_ACTIVATED_AT", "")
    if not activated:
        return True
    instant = datetime.fromisoformat(activated)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("La activación de cortes debe incluir zona horaria.")
    return when >= instant.astimezone(timezone.utc).replace(tzinfo=None)


def expire_pending_captures(now):
    """Recover only existing failed/pending captures, without discovering past slots."""
    deadline = now.astimezone(timezone.utc).replace(tzinfo=None) - PUNCTUALITY
    pending = ReconciliationCut.query.filter(
        ReconciliationCut.source == SOURCE, ReconciliationCut.snapshot_status == "pending",
        ReconciliationCut.scheduled_for <= deadline).all()
    for cut in pending:
        if slot_enabled(cut.scheduled_for):
            local = cut.scheduled_for.replace(tzinfo=timezone.utc).astimezone(CARACAS)
            capture_cut(local.date(), local.strftime("%H%M"))


def _record_capture_failure(cut_id):
    """Retain the rolled-back pending snapshot; a punctual retry can capture it."""
    db.session.rollback()
    cut = ReconciliationCut.query.filter_by(id=cut_id).populate_existing().with_for_update().one()
    if cut.snapshot_status == "pending":
        cut.status = "failed"
        cut.failure_reason = "Fallo de captura local. Reintento permitido solo dentro de la ventana de dos minutos."
        cut.last_attempt_at = datetime.utcnow()
        cut.attempt_count += 1
        title, message, severity = _summary(cut)
        notification_service.upsert_reconciliation_cut(cut, title, message, severity=severity)
    db.session.commit()


def snapshot_detail(cut, page=1):
    """Bounded read of historical rows, with no dependency on live stock."""
    query = ReconciliationCutStock.query.filter_by(cut_id=cut.id)
    total = query.count()
    pages = max(1, (total + 49) // 50)
    page = min(page, pages)
    rows = query.order_by(ReconciliationCutStock.product_id).offset((page - 1) * 50).limit(50).all()
    return dict(items=[dict(product_id=row.product_id, product_code=row.product_code_snapshot,
                            quantity=row.quantity) for row in rows],
                total=total, page=page, pages=pages, per_page=50)


def _summary(cut: ReconciliationCut):
    local = cut.scheduled_for.replace(tzinfo=timezone.utc).astimezone(CARACAS)
    label = f"Corte de inventario {local:%H:%M}"
    if cut.snapshot_status == "unavailable":
        reason = cut.failure_reason or "No se capturó la fotografía local dentro del horario previsto."
        return label + " no comparable", f"Corte {local:%H:%M} no comparable. {reason}", "critical"
    if cut.status == "failed":
        return label + " fallido", f"No se pudo procesar el corte de inventario {local:%H:%M}. Requiere revisión.", "critical"
    if cut.run:
        run = cut.run
        reviewed = run.match_count + run.discrepancy_count
        if run.discrepancy_count or run.unmatched_count or run.invalid_count:
            return label + " completado", (f"Corte de inventario {local:%H:%M} completado. {reviewed} productos revisados. "
                    f"{run.discrepancy_count} discrepancias, {run.unmatched_count} códigos sin identificar y "
                    f"{run.invalid_count} filas rechazadas. Requiere revisión."), "warning"
        return label + " completado", (f"Corte de inventario {local:%H:%M} completado. "
                                       f"{reviewed} productos revisados. Sin discrepancias."), "success"
    return label + " pendiente", (f"Corte de inventario {local:%H:%M} pendiente. Snapshot local capturado. "
                                    "Todavía no se recibió el archivo de inventario."), "info"


def notify_cut(cut: ReconciliationCut) -> None:
    """Persist the app summary without calling emit(), which commits and sends externally."""
    try:
        cut = ReconciliationCut.query.filter_by(id=cut.id).populate_existing().with_for_update().one()
        if cut.snapshot_status == "pending" and cut.status != "failed":
            db.session.rollback()
            return
        title, message, severity = _summary(cut)
        notification_service.upsert_reconciliation_cut(cut, title, message, severity=severity)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("No se pudo actualizar el resumen del corte %s", cut.id)


def _read_file(path: Path) -> bytes:
    before = path.stat()
    if not path.is_file() or before.st_size > reconciliation_service.MAX_BYTES:
        raise ValidationError(f"El CSV debe contener datos y no superar {reconciliation_service.MAX_BYTES // 2**20} MiB.")
    with path.open("rb") as stream:
        content = stream.read(reconciliation_service.MAX_BYTES + 1)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("El archivo cambió mientras se leía; se espera publicación completa.")
    return content


def _record_failed(cut_id: int, reason: str, digest: str | None) -> ReconciliationCut:
    db.session.rollback()
    cut = ReconciliationCut.query.filter_by(id=cut_id).populate_existing().with_for_update().one()
    if cut.status != "completed":
        cut.status, cut.failure_reason = "failed", reason[:255]
        cut.source_received_at = cut.source_received_at or datetime.utcnow()
        cut.last_source_hash, cut.last_attempt_at = digest, datetime.utcnow()
        cut.attempt_count += 1
        notification_service.upsert_reconciliation_cut(cut, *_summary(cut)[:2], severity="critical")
    db.session.commit()
    return cut


def _observe_source(cut_id: int) -> None:
    """Record first sight of the final filename, not filesystem mtime or processing completion."""
    cut = ReconciliationCut.query.filter_by(id=cut_id).populate_existing().with_for_update().one()
    if cut.source_received_at is None:
        cut.source_received_at = datetime.utcnow()
    db.session.commit()


def process_cut(cut_id: int) -> ReconciliationCut:
    """A completed cut never compares again; every auto line comes from frozen CutStock."""
    cut = db.session.get(ReconciliationCut, cut_id)
    if cut is None:
        raise ValueError("Unknown cut")
    if cut.run_id or cut.status == "completed" or cut.snapshot_status != "captured" or cut.status == "failed":
        return cut
    delivery = intake.prepare(filename_for(cut))
    if delivery['status'] != 'ready':
        if delivery['reason']:
            current_app.logger.warning('Ingreso pendiente para corte %s: %s', cut.id, delivery['reason'])
        notify_cut(cut)
        return cut
    path = _inbox_file(cut)
    if not path.exists():
        notify_cut(cut)
        return cut
    _observe_source(cut_id)
    digest = None
    try:
        content = _read_file(path)
        digest = hashlib.sha256(content).hexdigest()
        # The received bytes must still match the intake receipt, even after restart.
        if not intake.verify_private(path.name, digest):
            return cut
        lines = reconciliation_service.parse_csv(content)
    except ValueError:
        current_app.logger.warning('Integridad del inbox pendiente de revisión para corte %s', cut_id)
        return cut
    except ValidationError:
        _record_failed(cut_id, "Archivo CSV inválido; requiere revisión.", digest)
        _archive(cut_id, path, digest, failed=True)
        return db.session.get(ReconciliationCut, cut_id)
    except RuntimeError:
        # A changing file is not a finalized export; wait for the next review.
        return cut
    except OSError:
        current_app.logger.warning('Lectura temporalmente no disponible para corte %s', cut_id)
        return cut
    try:
        cut = ReconciliationCut.query.filter_by(id=cut_id).populate_existing().with_for_update().one()
        if cut.run_id or cut.status != "waiting_source" or cut.snapshot_status != "captured":
            db.session.rollback()
            return cut
        stock_rows = db.session.execute(select(ReconciliationCutStock.product_id,
            ReconciliationCutStock.product_code_snapshot, ReconciliationCutStock.quantity)
            .where(ReconciliationCutStock.cut_id == cut.id)).all()
        if not stock_rows:
            cut.snapshot_status, cut.status = "unavailable", "failed"
            cut.failure_reason = "No hay fotografía local disponible para comparar este corte."
            db.session.commit()
            notify_cut(cut)
            return cut
        reconciliation_service.compare_frozen(lines, stock_rows)
        del stock_rows, content
        run = ReconciliationRun(filename=filename_for(cut), sha256=digest, cutoff_at=cut.scheduled_for,
                                imported_at=datetime.utcnow(), source_type="automatic", status="confirmed")
        reconciliation_service.persist_new_lines(run, lines)
        del lines
        cut.run, cut.status = run, "completed"
        cut.last_source_hash, cut.last_attempt_at = digest, datetime.utcnow()
        cut.attempt_count += 1
        title, message, severity = _summary(cut)
        notification_service.upsert_reconciliation_cut(cut, title, message, severity=severity)
        db.session.commit()
    except Exception:
        db.session.rollback()
        _record_failed(cut_id, "No se pudo guardar la conciliación; requiere revisión.", digest)
        return db.session.get(ReconciliationCut, cut_id)
    _archive(cut_id, path, digest)
    return db.session.get(ReconciliationCut, cut_id)


def _archive(cut_id: int, path: Path, digest: str | None, *, failed=False) -> None:
    """Archive after database commit; reruns recover this independent file operation."""
    if digest is None:
        return
    cut = ReconciliationCut.query.filter_by(id=cut_id).populate_existing().with_for_update().one()
    if cut.archived_at and not failed:
        db.session.rollback()
        return
    day = cut.scheduled_for.replace(tzinfo=timezone.utc).astimezone(CARACAS).date().isoformat()
    target_dir = _root("RECONCILIATION_ARCHIVE_PATH") / ("failed" if failed else "processed") / day
    if failed:
        target_dir /= digest[:16]
    target = target_dir / filename_for(cut)
    try:
        if path.exists() and path.resolve().parent != _root("RECONCILIATION_INBOX_PATH"):
            raise RuntimeError("Archivo fuera de la carpeta de intercambio.")
        target_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(_read_file(target)).hexdigest() != digest:
                raise RuntimeError("Conflicto en evidencia archivada.")
        elif path.exists():
            temporary = target.with_suffix(".csv.tmp")
            shutil.copyfile(path, temporary)
            if hashlib.sha256(_read_file(temporary)).hexdigest() != digest:
                temporary.unlink(missing_ok=True)
                raise RuntimeError("La evidencia cambió al archivarse.")
            os.replace(temporary, target)
        else:
            db.session.rollback()
            return  # Neither copy exists; keep archived_at unset for operator investigation.
        if path.exists() and hashlib.sha256(_read_file(path)).hexdigest() == digest:
            path.unlink()
        if not failed:
            cut.archived_at = datetime.utcnow()
        db.session.commit()
    except (OSError, RuntimeError, ValidationError):
        db.session.rollback()
        current_app.logger.exception("Archivado pendiente para corte %s", cut_id)


def retry_failed(cut_id: int) -> ReconciliationCut:
    """Explicit retry may use a corrected CSV but must retain the same immutable snapshot."""
    cut = ReconciliationCut.query.filter_by(id=cut_id).populate_existing().with_for_update().first()
    if cut is None:
        raise ValidationError("Corte no encontrado.", status_code=404)
    if cut.snapshot_status != "captured" or cut.status != "failed" or cut.run_id:
        raise ValidationError("El corte no admite reintento.")
    cut.status, cut.failure_reason = "waiting_source", None
    db.session.commit()
    if not current_app.config.get("RECONCILIATION_AUTO_INGEST_ENABLED", True):
        return cut
    return process_cut(cut_id)


def ingest_due(now: datetime | None = None) -> None:
    """Five-minute runner: discover past cuts, never reconstruct missed stock."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("The ingestion clock must include its timezone")
    now = now.astimezone(timezone.utc)
    local_now = now.astimezone(CARACAS)
    activation = current_app.config.get("RECONCILIATION_ACTIVATED_ON", "")
    if not activation:
        raise RuntimeError("Configure RECONCILIATION_ACTIVATED_ON before enabling the runner.")
    first_day = date.fromisoformat(activation)
    if first_day > local_now.date():
        return
    day = first_day
    while day <= local_now.date():
        for slot in TIMES:
            when = scheduled_at(day, slot)
            if when > now.replace(tzinfo=None):
                continue
            cut = ensure_cut(day, slot)
            if cut.snapshot_status == "pending" and now.replace(tzinfo=None) >= when + PUNCTUALITY:
                # The same advisory lock serializes this with a still-running capture.
                # This cannot read Product after the punctuality window.
                capture_cut(day, slot)
            elif cut.status == "waiting_source" and cut.snapshot_status == "captured":
                process_cut(cut.id)
            elif cut.status == "completed" and cut.archived_at is None:
                _archive(cut.id, _inbox_file(cut), cut.run.sha256)
        day += timedelta(days=1)
