"""Controlador de diagnóstico predictivo: traduce HTTP <-> servicio de lectura."""
from flask import jsonify

from app.services import prediction_readiness_service


def readiness_summary():
    return jsonify(prediction_readiness_service.get_readiness_summary())


def list_products():
    return jsonify(prediction_readiness_service.list_product_readiness())


def get_product(product_id: int):
    return jsonify(prediction_readiness_service.get_product_readiness(product_id))
