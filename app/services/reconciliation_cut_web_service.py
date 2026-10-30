"""DEMO snapshots and browser CSV intake reuse the immutable cut ledger."""
import csv
import hashlib
import io
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from flask import current_app
from flask_login import current_user
from sqlalchemy import select, text

from app.extensions import db
from app.models import Product, User
from app.models.reconciliation import ReconciliationCut, ReconciliationCutStock, ReconciliationRun
from app.services import reconciliation_service as service, reconciliation_automation_service as automation
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.permissions import role_has_permission

SIMULATION = re.compile(r"^demo_sim:(0800|1200|1600|1830):([0-9]{1,10}):([a-f0-9]{24})$")


def is_simulation(cut):
    return SIMULATION.fullmatch(cut.source) is not None


def demo_enabled():
    return (current_app.config.get("APP_ENV") == "demo"
            and current_app.config.get("APP_BASE_URL") == "http://localhost:5001"
            and os.getenv("DOCKER_ENV_KIND") == "demo" and os.getenv("DEMO_MODE") == "true"
            and db.engine.url.database == "conejo_demo_8b867d4b"
            and db.engine.url.host == "db")


def can_simulate():
    return (demo_enabled() and current_user.is_authenticated and current_user.role == "admin"
            and role_has_permission(current_user.role, "reconciliation:create"))


def describe(cut):
    data = cut.to_dict()
    match = SIMULATION.fullmatch(cut.source)
    local = cut.scheduled_for.replace(tzinfo=timezone.utc).astimezone(automation.CARACAS)
    data.update(execution_type="simulation" if match else "real",
                reference_slot=match[1][:2] + ":" + match[1][2:] if match else local.strftime("%H:%M"),
                product_count=ReconciliationCutStock.query.filter_by(cut_id=cut.id).count(),
                processed_at=cut.last_attempt_at.isoformat() if cut.status == "completed" and cut.last_attempt_at else None)
    if match:
        user = db.session.get(User, int(match[2]))
        data["created_by"] = user.name if user else "Usuario histórico"
    return data


def simulate(slot, request_key):
    service.require("reconciliation:create")
    if not can_simulate():
        raise ValidationError("La simulación está disponible solo para el administrador autorizado de DEMO 5001.", status_code=403)
    if slot not in automation.TIMES or not re.fullmatch(r"[a-f0-9]{32}", str(request_key or "")):
        raise ValidationError("Seleccione un horario válido e identifique la ejecución.")
    # Actor + request identity are durable; a retry cannot silently switch its slot.
    actor_id = current_user.id
    key = hashlib.sha256(f"{actor_id}:{request_key}".encode()).hexdigest()[:24]
    prefix = f"demo_sim:%:{actor_id}:{key}"
    lock_key = "demo-cut:" + key
    db.session.rollback()
    with db.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        automation._verify_mariadb(connection)
        held = connection.execute(text("SELECT GET_LOCK(:key, 10)"), {"key": lock_key}).scalar()
        connection.commit()
        if held != 1:
            raise ConflictError("La ejecución está en curso; reintente con la misma solicitud.")
        try:
            cut_id = _capture(connection, prefix, slot, key, actor_id)
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:key)"), {"key": lock_key})
            connection.commit()
    db.session.rollback()
    return db.session.get(ReconciliationCut, cut_id)


def _capture(connection, prefix, slot, key, actor_id):
    with connection.begin():
        connection.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        existing = connection.execute(select(ReconciliationCut.id, ReconciliationCut.source)
                                      .where(ReconciliationCut.source.like(prefix))).first()
        if existing:
            if SIMULATION.fullmatch(existing.source)[1] != slot:
                raise ConflictError("La solicitud ya pertenece a otro horario de referencia.")
            return existing.id
        now = datetime.now(timezone.utc).replace(tzinfo=None).replace(microsecond=0)
        source = f"demo_sim:{slot}:{actor_id}:{key}"
        result = connection.execute(ReconciliationCut.__table__.insert().values(
            source=source, scheduled_for=now, local_snapshot_at=now,
            snapshot_status="captured", status="waiting_source", attempt_count=0))
        cut_id = result.inserted_primary_key[0]
        rows = connection.execute(select(Product.id, Product.code, Product.current_stock).order_by(Product.id)).all()
        if not rows:
            raise ValidationError("No hay productos disponibles para crear una fotografía de prueba.")
        connection.execute(ReconciliationCutStock.__table__.insert(), [
            dict(cut_id=cut_id, product_id=row.id, product_code_snapshot=row.code, quantity=row.current_stock)
            for row in rows])
        return cut_id


def get_cut(cut_id, lock=False):
    query = ReconciliationCut.query.filter_by(id=cut_id)
    if lock:
        query = query.populate_existing().with_for_update()
    cut = query.first()
    if cut is None:
        raise NotFoundError("Corte no encontrado.")
    return cut


def _archive_path(cut_id, digest):
    folder = Path(current_app.instance_path).resolve() / "reconciliation_web_csv"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"cut-{cut_id}-{digest}.csv"
    if target.resolve().parent != folder.resolve():
        raise ConflictError("No se puede acceder a la evidencia del corte.")
    return target


def validate_cut_filename(cut, filename):
    if is_simulation(cut):
        return
    expected = automation.filename_for(cut)
    if filename != expected:
        raise ValidationError(f"El CSV debe corresponder a este corte: {expected}. No se admite otra fecha u horario.")


def upload(cut_id, content, filename):
    service.require("reconciliation:create")
    filename = PureWindowsPath(str(filename or "")).name
    service.text_value(filename, "Archivo", 255, required=True)
    if not filename.lower().endswith(".csv"):
        raise ValidationError("Seleccione un archivo con extensión .csv.")
    lines = service.parse_csv(content)
    invalid = next((line for line in lines if line.result == "invalid"), None)
    if invalid:
        raise ValidationError(f"Fila {invalid.row_number}: {invalid.error} Corrija el CSV y vuelva a cargarlo.")
    digest = hashlib.sha256(content).hexdigest()
    try:
        cut = get_cut(cut_id, lock=True)
        if is_simulation(cut) and not can_simulate():
            raise ValidationError("No tiene permiso para procesar una simulación de DEMO.", status_code=403)
        validate_cut_filename(cut, filename)
        if cut.run_id:
            if cut.run.sha256 == digest:
                return cut.run
            raise ConflictError("Este corte ya tiene un CSV asociado; no se reemplaza su evidencia.")
        if cut.snapshot_status != "captured" or cut.status != "waiting_source":
            raise ConflictError("El corte no tiene una fotografía disponible o requiere recuperación operativa.")
        stock = ReconciliationCutStock.query.filter_by(cut_id=cut.id).all()
        service.compare_frozen(lines, stock)
        run = ReconciliationRun(filename=filename, sha256=digest, cutoff_at=cut.local_snapshot_at,
            created_by_user_id=current_user.id, status="preview",
            source_type="simulation" if is_simulation(cut) else "automatic")
        service.persist_new_lines(run, lines)
        target = _archive_path(cut.id, digest)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ConflictError("La evidencia archivada no coincide. Requiere revisión.")
        else:
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(content)
            os.replace(temporary, target)
        cut.run = run
        cut.source_received_at = datetime.now(timezone.utc).replace(tzinfo=None)
        cut.last_source_hash, cut.archived_at = digest, datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()
        return run
    except Exception:
        db.session.rollback()
        raise


def _uploaded_content(cut):
    if not cut.run_id:
        raise NotFoundError("Todavía no hay un CSV asociado.")
    target = _archive_path(cut.id, cut.run.sha256)
    if not target.is_file() and not is_simulation(cut):
        archive = automation._root("RECONCILIATION_ARCHIVE_PATH")
        day = cut.scheduled_for.replace(tzinfo=timezone.utc).astimezone(automation.CARACAS).date().isoformat()
        target = archive / "processed" / day / automation.filename_for(cut)
        if target.resolve() != target or not target.is_relative_to(archive):
            raise ConflictError("La evidencia del CSV requiere revisión de integridad.")
    if not target.is_file():
        raise NotFoundError("El CSV asociado todavía no está disponible en el archivo del corte.")
    content = automation._read_file(target)
    if hashlib.sha256(content).hexdigest() != cut.run.sha256:
        raise ConflictError("La evidencia del CSV requiere revisión de integridad.")
    return content


def _scenario_entries(cut, rows):
    if not can_simulate() or not is_simulation(cut):
        raise ValidationError("El escenario sintético pertenece exclusivamente a una simulación de DEMO.", status_code=403)
    positive = next((row for row in rows if row.quantity > 0), None)
    if len(rows) < 3 or positive is None:
        raise ValidationError("Se requieren tres productos y uno con stock positivo para demostrar faltantes.")
    others = [row for row in rows if row.product_id != positive.product_id]
    entries = [(others[0].product_code_snapshot, others[0].quantity),
               (positive.product_code_snapshot, positive.quantity - 1),
               (others[1].product_code_snapshot, others[1].quantity + 2)]
    codes = {row.product_code_snapshot.upper() for row in rows}
    unknown = "DEMO-SKU-DESCONOCIDO"
    while unknown.upper() in codes:
        unknown += "X"
    entries.append((unknown, 3))
    return entries


def csv_content(cut_id, scenario=False, uploaded=False):
    cut = get_cut(cut_id)
    if uploaded:
        return _uploaded_content(cut)
    if not is_simulation(cut):
        raise ValidationError("Los CSV de demostración solo se generan para simulaciones; cargue la medición externa del corte.", status_code=403)
    if cut.snapshot_status != "captured":
        raise ConflictError("No hay fotografía disponible para generar el CSV.")
    rows = ReconciliationCutStock.query.filter_by(cut_id=cut.id).order_by(ReconciliationCutStock.product_id).all()
    entries = _scenario_entries(cut, rows) if scenario else [(row.product_code_snapshot, row.quantity) for row in rows]
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(["product_code", "quantity"])
    writer.writerows(entries)
    return output.getvalue().encode("utf-8-sig")
