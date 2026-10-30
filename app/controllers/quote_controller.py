"""Controlador de cotizaciones: traduce HTTP <-> servicio (sin efectos en stock)."""
from flask import jsonify, request

from app.controllers.sale_controller import _json_body
from app.services import quote_service


def create_quote():
    quote = quote_service.create_quote(_json_body())
    payload = quote_service.serialize(quote, detail=True)
    payload["message"] = f"Cotización {quote.quote_number} guardada correctamente."
    return jsonify(payload), 201


def list_quotes():
    return jsonify(quote_service.list_quotes(request.args.to_dict()))


def get_quote(quote_number: str):
    return jsonify(quote_service.serialize(quote_service.get_quote(quote_number), detail=True))


def change_status(quote_number: str):
    quote = quote_service.change_status(quote_number, _json_body())
    return jsonify(quote_service.serialize(quote, detail=True))


def quote_options():
    return jsonify(quote_service.quote_options())
