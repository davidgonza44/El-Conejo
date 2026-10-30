"""Controlador de la cáscara compartida: búsqueda global y campana (GET, solo lectura)."""
from flask import jsonify, request, url_for
from flask_login import current_user

from app.services import notification_service, shell_service

_ALERT_LABELS = {"agotado": "Agotado", "bajo_stock": "Bajo stock"}


def _hrefs(groups: dict) -> dict:
    """Destinos reales existentes: catálogo filtrado, clientes filtrados y detalle de venta."""
    for item in groups.get("products", ()):
        item["href"] = url_for("pages.products", search=item["code"])
    for item in groups.get("customers", ()):
        item["href"] = url_for("pages.sales_customers", search=item["name"])
    for item in groups.get("sales", ()):
        item["href"] = url_for("pages.sale_detail", sale_number=item["sale_number"])
    return groups


def search():
    data = shell_service.search(request.args.get("q"), current_user.role)
    data["groups"] = _hrefs(data["groups"])
    return jsonify(data)


def alerts():
    data = shell_service.alerts()
    for item in data["items"]:
        item["status_label"] = _ALERT_LABELS.get(item["status"], item["status"])
    data["href"] = url_for("pages.products", status="bajo_stock")
    return jsonify(data)


def notifications():
    """Campana: notificaciones persistentes del usuario (no leídas primero) + contador."""
    data = notification_service.bell(current_user)
    data["href"] = url_for("pages.settings", tab="notifications")
    return jsonify(data)


def mark_notification_read(notification_id: int):
    row = notification_service.mark_read(current_user, notification_id)
    return jsonify({"notification": row.to_dict(), "unread": notification_service.unread_count(current_user)})


def mark_all_notifications_read():
    count = notification_service.mark_all_read(current_user)
    return jsonify({"marked": count, "unread": 0})
