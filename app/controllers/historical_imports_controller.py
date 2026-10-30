"""Adaptador HTTP del módulo de importaciones históricas."""
from flask import Response, jsonify, request, stream_with_context
from flask_login import current_user

from app.models.user import ROLE_ADMIN
from app.services import historical_import_service
from app.services.exceptions import ApiError, ValidationError
from app.services.historical_validation_service import MAX_MULTIPART_BYTES


def _json_body(*, optional: bool = False) -> dict:
    if optional and not request.get_data(cache=True):
        return {}
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("El cuerpo de la petición debe ser un objeto JSON válido.")
    return data


def _is_admin() -> bool:
    return current_user.role == ROLE_ADMIN


def list_imports():
    result = historical_import_service.list_imports(
        page_value=request.args.get("page"),
        per_page_value=request.args.get("per_page"),
        status=request.args.get("status"),
        is_admin=_is_admin(),
    )
    return jsonify(result)


def upload_import():
    # Límite local del endpoint: no afecta autenticación ni APIs operativas y
    # también protege el parser multipart cuando el cuerpo usa streaming.
    request.max_content_length = MAX_MULTIPART_BYTES
    if (
        request.content_length is not None
        and request.content_length > MAX_MULTIPART_BYTES
    ):
        raise ApiError(
            "El multipart excede el máximo permitido de 12 MiB.",
            status_code=413,
        )
    file_storage = request.files.get("file")
    if file_storage is None:
        raise ValidationError("El campo multipart 'file' es obligatorio.")
    historical_import = historical_import_service.upload_import(
        file_storage,
        source_system=request.form.get("source_system"),
        document_type=request.form.get("document_type"),
        actor_user_id=current_user.id,
    )
    return (
        jsonify(
            {
                "message": "Archivo histórico guardado en almacenamiento privado.",
                "historical_import": historical_import_service.serialize_import(
                    historical_import, is_admin=True
                ),
            }
        ),
        201,
    )


def get_import(import_id: str):
    return jsonify(
        historical_import_service.get_import_detail(
            import_id, is_admin=_is_admin()
        )
    )


def list_records(import_id: str):
    result = historical_import_service.list_records(
        import_id,
        page_value=request.args.get("page"),
        per_page_value=request.args.get("per_page"),
        match_status=request.args.get("match_status"),
        is_admin=_is_admin(),
    )
    return jsonify(result)


def list_errors(import_id: str):
    result = historical_import_service.list_errors(
        import_id,
        page_value=request.args.get("page"),
        per_page_value=request.args.get("per_page"),
        severity=request.args.get("severity"),
        resolution_status=request.args.get("resolution_status"),
        category=request.args.get("category"),
        is_admin=_is_admin(),
    )
    return jsonify(result)


def preview_import(import_id: str):
    data = _json_body(optional=True)
    unknown = set(data) - {"mapping"}
    if unknown:
        raise ValidationError("El preview contiene campos no permitidos.")
    historical_import = historical_import_service.preview_import(
        import_id,
        data,
        actor_user_id=current_user.id,
    )
    return jsonify(
        {
            "message": "Preview regenerado; ninguna fila activó demanda.",
            "historical_import": historical_import_service.serialize_import(
                historical_import, is_admin=True
            ),
        }
    )


def dry_run_import(import_id: str):
    historical_import, token, summary = (
        historical_import_service.dry_run_import(
            import_id, actor_user_id=current_user.id
        )
    )
    return jsonify(
        {
            "message": "Dry run válido. El token expira en 15 minutos y es de un uso.",
            "confirmation_token": token,
            "summary": summary,
            "historical_import": historical_import_service.serialize_import(
                historical_import, is_admin=True
            ),
        }
    )


def confirm_import(import_id: str):
    data = _json_body()
    unknown = set(data) - {"confirmation_token"}
    if unknown:
        raise ValidationError("La confirmación contiene campos no permitidos.")
    historical_import, replayed = historical_import_service.confirm_import(
        import_id,
        confirmation_token=data.get("confirmation_token"),
        actor_user_id=current_user.id,
    )
    return jsonify(
        {
            "message": (
                "El lote ya estaba confirmado; no se aplicó un segundo efecto."
                if replayed
                else "Lote histórico confirmado de forma atómica."
            ),
            "idempotent_replay": replayed,
            "historical_import": historical_import_service.serialize_import(
                historical_import, is_admin=True
            ),
        }
    )


def revert_import(import_id: str):
    data = _json_body()
    unknown = set(data) - {"reason"}
    if unknown:
        raise ValidationError("La reversión contiene campos no permitidos.")
    historical_import, replayed = historical_import_service.revert_import(
        import_id,
        reason=data.get("reason"),
        actor_user_id=current_user.id,
    )
    return jsonify(
        {
            "message": (
                "El lote ya estaba revertido; no se modificaron sus registros."
                if replayed
                else "Lote revertido; no se tocaron registros ni tablas operativas."
            ),
            "idempotent_replay": replayed,
            "historical_import": historical_import_service.serialize_import(
                historical_import, is_admin=True
            ),
        }
    )


def list_relationship_candidates(import_id: str, record_id: int):
    result = historical_import_service.list_relationship_candidates(
        import_id,
        record_id,
        page_value=request.args.get("page"),
        per_page_value=request.args.get("per_page"),
    )
    return jsonify(result)


def review_record(import_id: str, record_id: int):
    record = historical_import_service.review_record(
        import_id,
        record_id,
        _json_body(),
        actor_user_id=current_user.id,
    )
    return jsonify(
        {
            "message": "Revisión administrativa registrada; ejecute dry run nuevamente.",
            "record": historical_import_service.serialize_record(
                record, is_admin=True
            ),
        }
    )


def export_errors_csv(import_id: str):
    filename, iterator = historical_import_service.errors_csv_stream(import_id)
    return Response(
        stream_with_context(iterator),
        status=200,
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def download_template():
    return Response(
        historical_import_service.template_csv_bytes(),
        status=200,
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": (
                'attachment; filename="historical-import-template-v1.csv"'
            ),
            "X-Content-Type-Options": "nosniff",
            "X-Historical-CSV-Schema": "historical-csv-v1",
        },
    )
