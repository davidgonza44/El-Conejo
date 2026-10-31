"""Controlador de notas de entrega: traduce HTTP <-> servicio."""
from flask import Response, jsonify, request

from app.services import delivery_note_pdf_service, delivery_note_service
from app.services.exceptions import ValidationError
from app.utils.permissions import response_includes_sale_price


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        raise ValidationError("El cuerpo de la petición debe ser un JSON válido.")
    return data


def create_note():
    note, created = delivery_note_service.issue_note(_json_body())
    message = (f"Nota de entrega {note.note_number} emitida correctamente." if created
               else f"La nota {note.note_number} ya había sido emitida; no se registró otra.")
    return jsonify({"message": message, "delivery_note": note.to_dict()}), 201 if created else 200


def list_notes():
    filters = {field: request.args.get(field)
               for field in ("status", "customer_name", "date_from", "date_to", "customer_id")}
    if "page" not in request.args and "per_page" not in request.args:
        notes = delivery_note_service.list_notes(**filters)
        return jsonify({"count": len(notes), "items": [n.to_summary_dict() for n in notes]})
    result = delivery_note_service.page_notes(filters, request.args.get("page"), request.args.get("per_page"))
    issued, amount = result["issued"], result["issued_amount"]
    visible = response_includes_sale_price()
    return jsonify({
        "count": result["total"], "page": result["page"], "per_page": result["per_page"], "pages": result["pages"],
        "items": [n.to_summary_dict() for n in result["items"]],
        "summary": {"issued": issued, "cancelled": result["cancelled"],
                    "issued_amount": float(amount) if visible else None,
                    "average_amount": float(amount / issued) if visible and issued else (0.0 if visible else None)},
    })


def get_note(note_id: int):
    note = delivery_note_service.get_note_or_404(note_id)
    return jsonify(note.to_dict())


def cancel_note(note_id: int):
    note = delivery_note_service.cancel_note(note_id, _json_body().get("reason"))
    return jsonify(
        {
            "message": (
                f"Nota {note.note_number} cancelada; el stock fue devuelto al inventario."
            ),
            "delivery_note": note.to_dict(),
        }
    )


def download_pdf(note_id: int):
    """Descarga el PDF interno de una nota existente (emitida o cancelada)."""
    pdf_bytes, filename = delivery_note_pdf_service.build_pdf_response(note_id)
    return Response(
        pdf_bytes,
        status=200,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "application/pdf",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
