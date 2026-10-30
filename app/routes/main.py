"""Rutas básicas para verificar que el backend y la base de datos funcionan."""
from flask import Blueprint, jsonify
from sqlalchemy import text

from app.extensions import db
from app.models import Category, Product
from app.utils.auth_decorators import permission_required
from app.utils.permissions import PRODUCTS_READ

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def index():
    return jsonify(
        {
            "system": (
                "Sistema inteligente de apoyo al control de inventario — "
                "Ferretería y Construcciones El Conejo C.A."
            ),
            "status": "ok",
            "version": "0.1.0 (incremento 1: base del backend)",
        }
    )


@main_bp.get("/health/db")
def health_db():
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify({"database": "ok", "message": "Conexión a MySQL exitosa"})
    except Exception:  # Connection details must never be exposed by a health probe.
        return (
            jsonify({"database": "error", "message": "Base de datos no disponible."}),
            500,
        )


def test_categories():
    categories = Category.query.order_by(Category.name).all()
    return jsonify({"count": len(categories), "items": [c.to_dict() for c in categories]})


def test_products():
    products = Product.query.order_by(Product.code).all()
    return jsonify({"count": len(products), "items": [p.to_dict() for p in products]})


@main_bp.record_once
def register_test_diagnostics(state):
    """Opt in through Flask TESTING, never DEBUG or the visual reference mode.

    Even in tests, use the canonical catalog read permission. No public bypass.
    """
    if state.app.config.get("TESTING") is not True:
        return
    for path, view in (("categories", test_categories), ("products", test_products)):
        state.add_url_rule(
            "/api/test/" + path,
            view_func=permission_required(PRODUCTS_READ)(view),
            methods=["GET"],
        )
