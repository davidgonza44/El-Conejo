"""HTTP boundary for bounded CSV uploads and JSON-only review actions."""
from urllib.parse import parse_qs, urlsplit

from flask import current_app, jsonify, render_template, request, Response
from flask_wtf.csrf import validate_csrf
from wtforms.validators import ValidationError as CSRFError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from app.controllers.sale_controller import _json_body
from app.extensions import db
from app.models.reconciliation import ReconciliationCut, ReconciliationRun
from app.services import reconciliation_automation_service as automation, reconciliation_service as service
from app.services.exceptions import ValidationError
from app.utils.schema import table_ready
from app.services import reconciliation_cut_web_service as web_cuts


def available():
    return all(table_ready(name) for name in ("reconciliation_runs", "reconciliation_lines",
                                           "reconciliation_cuts", "reconciliation_cut_stock"))


def page():
    return render_template("reconciliation/page.html", pro_shell=True, movements_live=True,
                           available=available(), reference_mode=False,
                           csv_max_bytes=service.MAX_BYTES, csv_max_rows=service.MAX_ROWS,
                           cut_times=[value.strftime("%H:%M") for value in automation.TIMES.values()],
                           cut_timezone=str(automation.CARACAS),
                           can_simulate=web_cuts.can_simulate(),
                           manual_cut_csv=not current_app.config.get("RECONCILIATION_AUTO_INGEST_ENABLED", True),
                           automatic_enabled=bool(current_app.config.get("RECONCILIATION_ACTIVATED_ON")))


def listing():
    if not available():
        return jsonify(items=[], available=False, page=1, pages=1, total=0)
    page_number = service.integer(request.args.get("page", 1), "Página")
    source = request.args.get("source", "all")
    result = request.args.get("result", "all")
    if source not in ("all", "manual", "automatic", "simulation", "unknown") or result not in (
            "all", "differences", "clean", "observations"):
        raise ValidationError("Filtro de conciliación desconocido.")
    query = ReconciliationRun.query
    if source != "all":
        query = query.filter(ReconciliationRun.source_type == source) if source != "unknown" else query.filter(
            ReconciliationRun.source_type.is_(None))
    if result == "differences":
        query = query.filter(ReconciliationRun.discrepancy_count > 0)
    elif result == "clean":
        query = query.filter(ReconciliationRun.discrepancy_count == 0, ReconciliationRun.unmatched_count == 0,
                             ReconciliationRun.invalid_count == 0)
    elif result == "observations":
        query = query.filter((ReconciliationRun.discrepancy_count > 0) |
                             (ReconciliationRun.unmatched_count > 0) | (ReconciliationRun.invalid_count > 0))
    total = query.count()
    rows = (query.options(joinedload(ReconciliationRun.cut), joinedload(ReconciliationRun.creator))
            .order_by(ReconciliationRun.id.desc()).offset((page_number - 1) * 20).limit(20).all())
    return jsonify(items=[row.to_dict(False) for row in rows], available=True, total=total,
                   page=page_number, pages=max(1, (total + 19) // 20))


def cuts():
    if not available():
        return jsonify(items=[], available=False, page=1, pages=1, total=0)
    page_number = service.integer(request.args.get("page", 1), "Página")
    kind = request.args.get("type", "all")
    if kind not in ("all", "real", "simulation"):
        raise ValidationError("Tipo de corte desconocido.")
    query = ReconciliationCut.query
    if kind == "simulation":
        query = query.filter(ReconciliationCut.source.like("demo_sim:%"))
    elif kind == "real":
        query = query.filter(~ReconciliationCut.source.like("demo_sim:%"))
    total = query.count()
    pages = max(1, (total + 19) // 20)
    page_number = min(page_number, pages)
    rows = (query.options(joinedload(ReconciliationCut.run))
            .order_by(ReconciliationCut.id.desc()).offset((page_number - 1) * 20).limit(20).all())
    return jsonify(items=[web_cuts.describe(row) for row in rows], available=True,
                   total=total, page=page_number, pages=pages)


def cut_detail(cut_id):
    if not available():
        raise ValidationError("Conciliación pendiente de migración.", status_code=503)
    cut = db.session.get(ReconciliationCut, cut_id)
    if cut is None:
        raise ValidationError("Corte no encontrado.", status_code=404)
    page_number = service.integer(request.args.get("snapshot_page", 1), "Página de fotografía")
    data = web_cuts.describe(cut)
    expected = f"simulacion-corte-{cut.id}.csv" if web_cuts.is_simulation(cut) else automation.filename_for(cut)
    data.update(snapshot=automation.snapshot_detail(cut, page_number), expected_filename=expected)
    return jsonify(data)


def retry_cut(cut_id):
    _guard()
    service.only(_json_body(), ())
    try:
        return jsonify(automation.retry_failed(cut_id).to_dict())
    except SQLAlchemyError:
        db.session.rollback()
        raise ValidationError("No se pudo reintentar el corte.", status_code=409)


def detail(run_id):
    if not available():
        raise ValidationError("Conciliación pendiente de migración.", status_code=503)
    return jsonify(service.paged_detail(run_id, **service.detail_options(request.args)))


def _guard(upload=False):
    referer = parse_qs(urlsplit(request.headers.get("Referer", "")).query)
    if request.args.get("ref") == "1" or referer.get("ref") == ["1"]:
        raise ValidationError("La vista de referencia no admite escrituras.", status_code=409)
    if request.headers.get("Origin") and request.headers["Origin"] != request.host_url.rstrip("/"):
        raise ValidationError("Origen de solicitud no permitido.", status_code=403)
    if not available():
        raise ValidationError("Conciliación pendiente de migración.", status_code=503)
    expected = "multipart/form-data" if upload else "application/json"
    if request.mimetype != expected:
        raise ValidationError("Formato de solicitud no permitido.", status_code=415)


def _mutate(fn, *, upload=False, status=200):
    _guard(upload)
    # Validate before the service can commit an action.
    options = service.detail_options(request.args)
    try:
        run = fn()
        return jsonify(service.paged_detail(run.id, **options)), status
    except SQLAlchemyError:
        db.session.rollback()
        raise ValidationError("No se pudo guardar la operación. No se aplicaron cambios.", status_code=409)


def create():
    # Flask enforces request size before parsing multipart; content itself is bounded too.
    request.max_content_length = service.MAX_BYTES + 65536
    return _mutate(_upload, upload=True, status=201)


def _upload():
    service.only(request.form.to_dict(), ("cutoff_at",))
    if set(request.files) != {"file"} or len(request.files.getlist("file")) != 1:
        raise ValidationError("Seleccione un único archivo CSV.")
    file = request.files["file"]
    return service.create_preview(file.stream.read(service.MAX_BYTES + 1), file.filename, request.form.get("cutoff_at"))


def _transition(run_id, confirm):
    service.only(_json_body(), ())
    if service.get_run(run_id).cut:
        _web_guard()
    return service.transition(run_id, confirm=confirm)


def confirm(run_id):
    return _mutate(lambda: _transition(run_id, True))


def discard(run_id):
    return _mutate(lambda: _transition(run_id, False))


def review(run_id, line_id):
    return _mutate(lambda: service.process_line(run_id, line_id, _json_body(), resolve=False))


def resolve(run_id, line_id):
    return _mutate(lambda: service.process_line(run_id, line_id, _json_body(), resolve=True))


def _web_guard(upload=False):
    _guard(upload)
    try:
        validate_csrf(request.headers.get("X-CSRFToken"))
    except CSRFError:
        raise ValidationError("La sesión del formulario venció; recargue la página.", status_code=400)


def simulate():
    _web_guard()
    data = _json_body()
    service.only(data, ("slot", "request_key"))
    try:
        return jsonify(web_cuts.describe(web_cuts.simulate(data.get("slot"), data.get("request_key")))), 201
    except SQLAlchemyError:
        db.session.rollback()
        raise ValidationError("No se pudo crear la simulación. Reintente con la misma solicitud.", status_code=409)


def cut_upload(cut_id):
    request.max_content_length = service.MAX_BYTES + 65536
    _web_guard(upload=True)
    service.only(request.form.to_dict(), ())
    if set(request.files) != {"file"} or len(request.files.getlist("file")) != 1:
        raise ValidationError("Seleccione un único archivo CSV.")
    file = request.files["file"]
    try:
        run = web_cuts.upload(cut_id, file.stream.read(service.MAX_BYTES + 1), file.filename)
        return jsonify(service.paged_detail(run.id)), 201
    except (OSError, SQLAlchemyError):
        db.session.rollback()
        raise ValidationError("No se pudo asociar el CSV. No se modificaron existencias.", status_code=409)


def cut_csv(cut_id):
    variant = request.args.get("variant", "example")
    if variant not in ("example", "scenario", "uploaded"):
        raise ValidationError("Seleccione un CSV disponible.")
    content = web_cuts.csv_content(cut_id, scenario=variant == "scenario", uploaded=variant == "uploaded")
    prefix = "DEMO-simulacion-" if web_cuts.is_simulation(web_cuts.get_cut(cut_id)) else ""
    return Response(content, mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{prefix}corte-{cut_id}-{variant}.csv"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
