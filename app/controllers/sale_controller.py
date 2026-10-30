"""Controlador de ventas: traduce HTTP <-> servicio y adapta la lista/detalle vivos a la
geometría de referencia (misma forma que los fixtures de sales_reference)."""
from flask import jsonify, request, url_for

from app.controllers.dashboard_controller import _money, _page_items, _sale_stamp
from app.models.sale import PAYMENT_METHODS
from app.services import sale_service
from app.services.exceptions import ValidationError

LIST_PER_PAGE = 10
_STATUS_TONE = {"completed": "green"}


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        raise ValidationError("El cuerpo de la petición debe ser un JSON válido.")
    return data


def create_sale():
    sale, created = sale_service.create_sale(_json_body())
    payload = sale_service.get_sale(sale.sale_number).to_dict()
    payload["message"] = f"Venta {sale.sale_number} registrada correctamente."
    return jsonify(payload), 201 if created else 200


def list_sales():
    return jsonify(sale_service.list_sales(request.args.to_dict()))


def get_sale(sale_number: str):
    return jsonify(sale_service.get_sale(sale_number).to_dict())


def sale_products():
    return jsonify(sale_service.sale_products(request.args.to_dict()))


def sale_options():
    return jsonify(sale_service.sale_options())


def _badge(sale: dict) -> tuple:
    return sale["status_label"], _STATUS_TONE.get(sale["status"], "gray")


def _pagination(data: dict, noun: str) -> dict:
    first = (data["page"] - 1) * data["per_page"] + 1 if data["total"] else 0
    last = min(data["page"] * data["per_page"], data["total"])
    return {"summary": f"Mostrando {first} a {last} de {data['total']} {noun}" if data["total"] else f"Sin {noun} registradas",
            "page": data["page"], "pages": data["pages"], "items": _page_items(data["page"], data["pages"])}


def sales_list_view(params: dict) -> dict:
    """Lista real de ventas (GET /api/sales) con filtros del servidor: búsqueda, fechas, método."""
    query = {k: params.get(k, "") for k in ("search", "date_from", "date_to", "payment_method")}
    data = sale_service.list_sales({**{k: v for k, v in query.items() if v}, "page": params.get("page", 1), "per_page": LIST_PER_PAGE})
    return {
        "filters": {**query, "methods": list(PAYMENT_METHODS.items())},
        "columns": ["Venta", "Fecha", "Cliente", "Productos", "Método de pago", "Total", "Estado", "Acciones"],
        "rows": [{
            "number": s["sale_number"], "href": url_for("pages.sale_detail", sale_number=s["sale_number"]),
            "date": _sale_stamp(s["sold_at"]), "customer": s["customer"]["name"] if s["customer"] else "—",
            "units": s["units"], "method": s["payment_method_label"], "total": _money(s["total"]), "status": _badge(s),
        } for s in data["items"]],
        "pagination": _pagination(data, "ventas"),
        "total": data["total"],
    }


def sale_detail_view(sale_number: str) -> dict:
    """Detalle real de una venta con la forma del fixture SALE_DETAIL (solo lectura)."""
    sale = sale_service.get_sale(sale_number).to_dict()
    customer = sale["customer"] or {}
    cash_rows = []
    if sale["payment_method"] == "cash":
        cash_rows = [(label, _money(sale[field]) if sale[field] is not None else "No registrado (venta histórica)", None, None)
                     for label, field in (("Efectivo recibido", "cash_received"), ("Vuelto", "change_due"))]
    return {
        "number": sale["sale_number"], "title": f"Venta #{sale['sale_number']}", "status": _badge(sale),
        "subtitle": "Información detallada de la venta.",
        "info": {"title": "Información general", "rows": [
            ("Fecha y hora", _sale_stamp(sale["sold_at"]), None, None),
            ("Cliente", customer.get("name", "—"), None, f"RIF: {customer['tax_id']}" if customer.get("tax_id") else None),
            ("Vendedor", sale["seller"]["name"] or "—", None, None),
            ("Método de pago", sale["payment_method_label"], None, None),
            ("Condición de pago", sale["payment_condition_label"], None, None),
            ("Estado", _badge(sale), "badge", None),
        ] + cash_rows},
        "totals": {"title": "Totales", "rows": [("Subtotal", _money(sale["subtotal"])), ("Descuento", _money(sale["discount_total"]))],
                   "total": ("Total", _money(sale["total"]))},
        "tabs": [f"Productos ({sale['units']})"],
        "columns": ["#", "Producto", "Código", "Precio Unit.", "Cantidad", "Descuento", "Total"],
        "rows": [({"name": i["product_name"], "src": i["image_url"]}, i["product_code"], _money(i["unit_price"]), str(i["quantity"]),
                  f"{float(i['discount_percent']):g}%", _money(i["line_total"])) for i in sale["items"]],
        "pagination": {"summary": f"Mostrando {len(sale['items'])} renglones", "pages": [], "per_page": None},
    }
