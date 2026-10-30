"""Controlador de notas de entrega: traduce HTTP <-> servicio."""
from flask import Response, jsonify, request

from app.services import delivery_note_pdf_service, delivery_note_service
from app.services.exceptions import ValidationError


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        raise ValidationError("El cuerpo de la petición debe ser un JSON válido.")
    return data


def create_note():
    note = delivery_note_service.create_note(_json_body())
    return (
        jsonify(
            {
                "message": f"Nota de entrega {note.note_number} emitida correctamente.",
                "delivery_note": note.to_dict(),
            }
        ),
        201,
    )


def list_notes():
    notes = delivery_note_service.list_notes(
        status=request.args.get("status"),
        customer_name=request.args.get("customer_name"),
        date_from=request.args.get("date_from"),
        date_to=request.args.get("date_to"),
    )
    return jsonify({"count": len(notes), "items": [n.to_summary_dict() for n in notes]})


def get_note(note_id: int):
    note = delivery_note_service.get_note_or_404(note_id)
    return jsonify(note.to_dict())


def cancel_note(note_id: int):
    note = delivery_note_service.cancel_note(note_id)
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
