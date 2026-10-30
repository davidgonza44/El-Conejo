"""HTTP adapter for the separate historical 2026 workflow."""
from flask import jsonify, render_template, request
import re
from flask_login import current_user
from sqlalchemy import inspect

from app.extensions import db
from app.services import real_2026_analysis_service as analysis_service
from app.services import real_2026_import_service as imports
from app.services.exceptions import ApiError, ValidationError
from app.utils.permissions import role_has_permission


TABLES = ("historical_2026_imports", "historical_2026_products", "historical_2026_daily")
MAX_BYTES = 64 * 1024 * 1024


def ready():
    inspector = inspect(db.engine)
    return all(inspector.has_table(table) for table in TABLES)


def _available():
    if not ready():
        raise ApiError("El histórico real 2026 todavía no está disponible en este entorno.", 503)


def page():
    return render_template(
        "real_2026.html", reference_mode=False, pro_shell=True, active_page="demand_forecast",
        available=ready(),
        can_upload=all(role_has_permission(current_user.role, p) for p in
                       ("historical_imports:upload", "historical_imports:review")),
        can_confirm=role_has_permission(current_user.role, "historical_imports:confirm"),
        can_revert=role_has_permission(current_user.role, "historical_imports:revert"),
        can_analyze=role_has_permission(current_user.role, "predictions:read"),
    )


def listing():
    _available()
    return jsonify(imports.listing())


def _body(allowed):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - set(allowed):
        raise ValidationError("Los campos de la solicitud no son válidos.")
    return data


def preview():
    _available()
    request.max_content_length = MAX_BYTES
    files, size = {}, 0
    for upload in request.files.getlist("files"):
        name = upload.filename or ""
        if name in files:
            raise ValidationError("El paquete contiene un nombre de archivo repetido.")
        content = upload.stream.read(MAX_BYTES + 1)
        size += len(content)
        if size > MAX_BYTES:
            raise ApiError("El paquete supera el límite de 64 MiB.", 413)
        files[name] = content
    batch, token, created = imports.preview(
        files, actor_id=current_user.id,
        supersedes_public_id=request.form.get("supersedes_public_id") or None,
        revision_reason=request.form.get("revision_reason") or None,
    )
    return jsonify(batch=imports.serialize(batch), confirmation_token=token,
                   created=created), 201 if created else 200


def detail(public_id):
    _available()
    return jsonify(imports.serialize(imports.get_batch(public_id)))


def confirm(public_id):
    _available()
    data = _body(("token",))
    batch, changed = imports.confirm(public_id, data.get("token"), actor_id=current_user.id)
    return jsonify(batch=imports.serialize(batch), changed=changed)


def revert(public_id):
    _available()
    data = _body(("reason",))
    batch, changed = imports.revert(public_id, data.get("reason"), actor_id=current_user.id)
    return jsonify(batch=imports.serialize(batch), changed=changed)


def products(public_id):
    _available()
    return jsonify(analysis_service.products(public_id, search=request.args.get("search", ""),
                                            page=_integer_query("page", "1")))


def history(public_id, product_id):
    _available()
    return jsonify(analysis_service.product_history(public_id, product_id))


def analysis(public_id, product_id):
    _available()
    return jsonify(analysis_service.analyze(public_id, product_id,
        model=request.args.get("model", "moving_average"), horizon=_integer_query("horizon", "7")))


def _integer_query(name, default):
    value = request.args.get(name, default)
    if not re.fullmatch(r"[1-9]\d{0,5}", value):
        raise ValidationError("La página y el horizonte deben ser enteros positivos válidos.")
    return int(value)
