"""Bounded CSV comparison and explicit, atomic inventory reconciliation."""
import csv
import hashlib
import io
from datetime import datetime, timezone
from pathlib import PureWindowsPath

from flask_login import current_user
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Product
from app.models.reconciliation import ReconciliationRun, ReconciliationLine
from app.services import inventory_adjustment_service
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.purchase_service import integer, only, text_value
from app.utils.permissions import role_has_permission

# 50k rows with maximum-length four-byte UTF-8 codes/names measured 40.65 MB.
MAX_BYTES = 48 * 1024 * 1024
MAX_ROWS = 50000
LINE_BATCH_SIZE = 2500  # Measured on MariaDB; bounded mappings, no per-row RETURNING.


def persist_new_lines(run, lines):
    """Insert new comparisons in the caller's transaction; IDs are read after commit.

    Keep parsed lines transient: assigning run.lines would schedule ORM INSERTs too.
    Core insert through Session is SQLAlchemy 2.x executemany, including NULL fields.
    """
    _counts(run, lines)
    db.session.add(run)
    db.session.flush()  # Only the header needs its generated ID now.
    fields = ("row_number", "product_code", "product_name", "product_id", "external_quantity",
              "system_quantity", "difference", "result", "error")
    for start in range(0, len(lines), LINE_BATCH_SIZE):
        mappings = [dict({name: getattr(line, name) for name in fields}, run_id=run.id, review_status="pending")
                    for line in lines[start:start + LINE_BATCH_SIZE]]
        db.session.execute(ReconciliationLine.__table__.insert(), mappings)
    run.number = f"CONC-{run.id:06d}"


def require(permission):
    if not current_user.is_authenticated or not role_has_permission(current_user.role, permission):
        raise ValidationError("No tiene permiso para esta operación.", status_code=403)


def cutoff(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or not 1900 <= result.year <= 9998:
            raise ValueError
        return result.astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        raise ValidationError("Indique la fecha y hora del corte con zona horaria.")


def parse_csv(content):
    if not isinstance(content, bytes) or not content or len(content) > MAX_BYTES:
        raise ValidationError(f"El CSV debe contener datos y no superar {MAX_BYTES // 2**20} MiB.")
    try:
        text = content.decode("utf-8-sig")
        if "\x00" in text:
            raise ValidationError("El CSV contiene caracteres no permitidos.")
        reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=";", strict=True)
        headers = reader.fieldnames or []
        if (not {"product_code", "quantity"} <= set(headers)
                or set(headers) - {"product_code", "quantity", "product_name"}
                or len(headers) != len(set(headers))):
            raise ValidationError("Columnas: product_code;quantity;product_name (nombre opcional).")
        rows = []
        seen = set()
        for number, row in enumerate(reader, 2):
            if len(rows) >= MAX_ROWS or None in row or any(value is None for value in row.values()):
                raise ValidationError(f"Estructura inválida o más de {MAX_ROWS} filas; no se guardó el archivo.")
            rows.append(_csv_line(number, row, seen))
        if not rows:
            raise ValidationError("El CSV no contiene filas de datos.")
        return rows
    except (UnicodeDecodeError, csv.Error):
        raise ValidationError("El archivo debe ser CSV UTF-8 válido separado por punto y coma.")


def _csv_line(number, row, seen):
    code = row["product_code"].strip()
    name = row.get("product_name", "").strip()
    line = ReconciliationLine(row_number=number, product_code=code[:50], product_name=name[:150], result="invalid")
    try:
        text_value(code, "Código", 50, required=True)
        text_value(name, "Nombre", 150)
        if code.upper() in seen:
            raise ValidationError("Código duplicado en el archivo.")
        seen.add(code.upper())
        line.external_quantity = integer(row["quantity"].strip(), "Cantidad", minimum=0)
        line.result = "unmatched"
    except ValidationError as error:
        line.error = error.message
    return line


def _compare(lines):
    codes = sorted({line.product_code.upper() for line in lines if line.result != "invalid"})
    products = {}
    # Batches stay below SQLite/MySQL parameter limits. Lock current rows in stable order.
    for start in range(0, len(codes), 400):
        rows = db.session.execute(select(Product).where(func.upper(Product.code).in_(codes[start:start + 400]))
                                  .order_by(Product.id).with_for_update()
                                  .execution_options(populate_existing=True)).scalars()
        for product in rows:
            products.setdefault(product.code.upper(), []).append(product)
    _apply_matches(lines, {code: [(p.id, p.current_stock) for p in matches] for code, matches in products.items()})


def compare_frozen(lines, stock_rows):
    """Automatic quantities come only from CutStock; live IDs only preserve the line FK."""
    products = {}
    for row in stock_rows:
        products.setdefault(row.product_code_snapshot.upper(), []).append((row.product_id, row.quantity))
    _apply_matches(lines, products)
    # Line.product_id has a live FK. Keep historical quantities even if that row was deleted.
    ids = {line.product_id for line in lines if line.product_id is not None}
    existing = set(db.session.execute(select(Product.id).where(Product.id.in_(ids))).scalars()) if ids else set()
    for line in lines:
        if line.product_id is not None and line.product_id not in existing:
            line.product_id = None


def _apply_matches(lines, products):
    """Shared CSV classification; the caller selects live or frozen local quantities."""
    for line in lines:
        if line.result == "invalid":
            continue
        matches = products.get(line.product_code.upper(), [])
        line.product_id = line.system_quantity = line.difference = None
        line.error = None
        if len(matches) != 1:
            line.result = "unmatched"
            line.error = "Código inexistente o ambiguo en el catálogo."
            continue
        line.product_id, line.system_quantity = matches[0]
        line.difference = line.external_quantity - line.system_quantity
        line.result = "discrepancy" if line.difference else "match"


def _counts(run, lines=None):
    lines = run.lines if lines is None else lines
    run.received_count = len(lines)
    for result in ("invalid", "unmatched", "match", "discrepancy"):
        setattr(run, result + "_count", sum(line.result == result for line in lines))
    run.valid_count = run.received_count - run.invalid_count


def create_preview(content, filename, cutoff_at):
    require("reconciliation:create")
    filename = PureWindowsPath(str(filename or "")).name
    if not filename.lower().endswith(".csv"):
        raise ValidationError("Solo se admite un archivo CSV UTF-8.")
    text_value(filename, "Archivo", 255, required=True)
    timestamp = cutoff(cutoff_at)
    lines = parse_csv(content)
    try:
        _compare(lines)
        run = ReconciliationRun(filename=filename, sha256=hashlib.sha256(content).hexdigest(), cutoff_at=timestamp,
                                created_by_user_id=current_user.id, status="preview", source_type="manual")
        persist_new_lines(run, lines)
        db.session.commit()
        return run
    except Exception:
        db.session.rollback()
        raise


def get_run(run_id, lock=False):
    query = ReconciliationRun.query.filter_by(id=run_id)
    if lock:
        query = query.populate_existing().with_for_update()
    run = query.first()
    if run is None:
        raise NotFoundError("Conciliación no encontrada.")
    if lock:
        db.session.expire(run, ["lines"])
    return run


def detail_options(args):
    page = integer(args.get("page", 1), "Página")
    per_page = integer(args.get("per_page", 50), "Filas por página")
    result, review = args.get("result", "all"), args.get("review_status", "all")
    if (per_page not in (5, 25, 50, 100) or result not in ("all", "match", "discrepancy", "unmatched", "invalid")
            or review not in ("all", "pending", "reviewed", "resolved")):
        raise ValidationError("Paginación o filtro de líneas desconocido.")
    return dict(page=page, per_page=per_page, result=result, review_status=review)


def paged_detail(run_id, *, page=1, per_page=50, result="all", review_status="all"):
    """Counters describe the whole run; total describes the filtered lines."""
    run = (ReconciliationRun.query.options(joinedload(ReconciliationRun.creator), joinedload(ReconciliationRun.cut))
           .filter_by(id=run_id).first())
    if run is None:
        raise NotFoundError("Conciliación no encontrada.")
    query = ReconciliationLine.query.filter_by(run_id=run_id)
    if result != "all":
        query = query.filter_by(result=result)
    if review_status != "all":
        query = query.filter_by(review_status=review_status)
    total = query.count()
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    rows = (query.options(joinedload(ReconciliationLine.reviewer), joinedload(ReconciliationLine.resolver))
            .order_by(ReconciliationLine.row_number).offset((page - 1) * per_page).limit(per_page).all())
    data = run.to_dict(False)
    data.update(items=[line.to_dict() for line in rows], page=page, per_page=per_page, total=total, pages=pages,
                result=result, review_status=review_status)
    return data


def transition(run_id, *, confirm):
    require("reconciliation:create")
    try:
        run = get_run(run_id, lock=True)
        if run.cut and run.status == "confirmed" and confirm:
            return run
        if run.cut and not confirm:
            raise ConflictError("El CSV de un corte se conserva como evidencia; no se descarta.")
        if run.status != "preview":
            raise ConflictError("Solo se puede confirmar o descartar una previsualización.")
        if confirm and not run.cut:
            _compare(run.lines)
            _counts(run)
        if run.cut:
            run.cut.status = "completed"
            run.cut.last_attempt_at = datetime.utcnow()
            run.cut.attempt_count += 1
        run.status = "confirmed" if confirm else "discarded"
        if run.cut:
            from app.services import notification_service
            label = "SIMULACIÓN" if run.source_type == "simulation" else "Corte operativo"
            notification_service.upsert_reconciliation_cut(run.cut, f"{label} #{run.cut.id} conciliado",
                f"{run.match_count} coincidencias, {run.discrepancy_count} diferencias y {run.unmatched_count} SKU desconocidos. Existencias intactas.",
                severity="warning" if run.discrepancy_count or run.unmatched_count else "success")
        db.session.commit()
        return run
    except Exception:
        db.session.rollback()
        raise


def process_line(run_id, line_id, data, *, resolve):
    require("reconciliation:resolve")
    only(data, ("note", "action", "new_stock") if resolve else ("note",))
    note = text_value(data.get("note"), "Justificación", 1000, required=True)
    try:
        run = get_run(run_id, lock=True)
        line = ReconciliationLine.query.filter_by(id=line_id, run_id=run.id).populate_existing().with_for_update().first()
        if line is None:
            raise NotFoundError("Discrepancia no encontrada.")
        if run.status != "confirmed" or line.result != "discrepancy" or line.review_status == "resolved":
            raise ConflictError("La línea no admite esta operación.")
        if resolve and any(request.status == "pending" for request in line.adjustment_requests):
            raise ConflictError("La discrepancia tiene una solicitud de ajuste pendiente de aprobación.")
        if resolve:
            _resolve(line, data, note)
        else:
            if line.review_status != "pending":
                raise ConflictError("La discrepancia ya fue revisada.")
            line.review_status = "reviewed"
            line.reviewed_by_user_id, line.reviewed_at, line.review_note = current_user.id, datetime.utcnow(), note
        db.session.commit()
        return run
    except Exception:
        db.session.rollback()
        raise


def _resolve(line, data, note):
    action = data.get("action")
    if action not in ("acknowledge", "adjustment"):
        raise ValidationError("Seleccione reconocer sin ajuste o ajustar inventario.")
    if action == "adjustment" and (line.run.source_type == "simulation"
            or line.run.cut and line.run.cut.source.startswith("demo_sim:")):
        raise ValidationError("Las simulaciones no admiten ajustes físicos de stock.", status_code=403)
    if action == "adjustment":
        require("inventory:move")
        if line.product_id is None:
            raise ConflictError("El producto histórico ya no existe; no se puede ajustar automáticamente su identidad.")
        if line.run.source_type == "automatic":
            product = db.session.get(Product, line.product_id)
            if product is None or product.code.upper() != line.product_code.upper():
                raise ConflictError("El código del producto cambió desde el corte; revise el catálogo antes de ajustar.")
        if "new_stock" in data:
            # El objetivo lo calcula el servidor: stock vigente + diferencia del corte.
            raise ValidationError("El ajuste aplica la diferencia del corte; no indique un stock absoluto.")
        # Inventory's reason limit is 255; never silently truncate the audit justification.
        reason = text_value(note, "Motivo del ajuste", 255, required=True)
        # No mueve stock: crea una solicitud que aprueba otra persona. Al aprobarse,
        # la línea queda resuelta con el movimiento aplicado.
        inventory_adjustment_service.create_from_reconciliation(line, reason)
        return
    if "new_stock" in data:
        raise ValidationError("Reconocer sin ajuste no admite un nuevo stock.")
    line.review_status = "resolved"
    line.resolved_by_user_id, line.resolved_at = current_user.id, datetime.utcnow()
    line.resolution_action, line.resolution_note = action, note
