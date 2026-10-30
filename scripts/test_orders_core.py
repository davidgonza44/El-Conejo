"""Orders Core aislado: SQLite temporal, nunca la base operativa.

Prueba número PED, snapshot de precio, unidades (SUM cantidad), entrega estimada,
transiciones, filtros/paginación, permisos y que un pedido NUNCA cambia stock ni crea
StockMovement (ni al crearlo ni al completarlo).

Uso: .\\.venv\\Scripts\\python.exe -B scripts/test_orders_core.py
"""
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import migrate_order_metadata
from test_products_core import create_via_api, setup_app
from app.extensions import db
from app.models import Customer, Order, OrderLine, Product, StockMovement
from app.models.customer import WALK_IN_NAME
from app.services import sales_common
from app.services.inventory_snapshot_service import commercial_today


def run():
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1

    with tempfile.TemporaryDirectory(prefix="orders-core-") as folder:
        app, admin, inv, seller, ids = setup_app(Path(folder))
        app.logger.disabled = True
        with app.app_context():
            walk_in = Customer(name=WALK_IN_NAME, is_walk_in=True)
            customer = Customer(name="Constructora del Norte S.A.", tax_id="J-12345678-9")
            other = Customer(name="Ferretería San José", tax_id="J-98765432-1")
            inactive = Customer(name="Inactivo", is_active=False)
            db.session.add_all([walk_in, customer, other, inactive])
            db.session.commit()
            walk_in_id, customer_id, other_id, inactive_id = walk_in.id, customer.id, other.id, inactive.id
        cemento = create_via_api(admin, code="CEM-001", name="Cemento Gris 42.5 kg", category_id=ids["construccion"], current_stock=8, sale_price="7.50").json["id"]
        tubo = create_via_api(admin, code="PVC-001", name='Tubo PVC 1/2" x 3m', category_id=ids["construccion"], current_stock=0, sale_price="2.30").json["id"]
        api = "/api/orders"
        today = commercial_today()
        in_week = (today + timedelta(days=7)).isoformat()

        def snapshot():
            with app.app_context():
                return ({p.id: p.current_stock for p in Product.query.all()}, StockMovement.query.count())
        stock_before = snapshot()

        def body(items, **extra):
            return {"customer_id": customer_id, "estimated_delivery_date": in_week, "items": items, **extra}

        # Creación multilínea: 10 + 5 unidades aunque el tubo esté agotado (no reserva ni descuenta).
        response = seller.post(api, json=body([{"product_id": cemento, "quantity": 10}, {"product_id": tubo, "quantity": 5}]))
        check(response.status_code == 201, "pedido creado " + str(response.json))
        order = response.json
        check(order["order_number"] == f"PED-{order['id']:06d}", "número PED-{id:06d}")
        check(order["subtotal"] == "86.50" and order["total"] == "86.50", "totales Decimal " + str(order))
        check(order["units"] == 15 and order["line_count"] == 2, "Productos = SUM(quantity) en unidades")
        check(order["estimated_delivery_date"] == in_week and order["status"] == "pending" and order["status_label"] == "Pendiente", "entrega/estado")
        check(order["seller"]["name"] == "Vendedor" and order["customer"]["id"] == customer_id, "vendedor autenticado y cliente")
        line = next(i for i in order["items"] if i["product_id"] == tubo)
        check(line["product_code"] == "PVC-001" and line["unit_price"] == "2.30" and line["line_subtotal"] == "11.50", "snapshot de renglón")
        check(snapshot() == stock_before, "crear pedido no toca stock ni StockMovement")
        with app.app_context():
            db.session.get(Product, tubo).sale_price = Decimal("9.99")
            db.session.commit()
        check(next(i for i in admin.get(f"{api}/{order['order_number']}").json["items"] if i["product_id"] == tubo)["unit_price"] == "2.30", "precio histórico inmutable")

        # Entrega estimada: fecha comercial >= hoy (Caracas).
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], estimated_delivery_date=today.isoformat())).status_code == 201, "entrega hoy permitida")
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], estimated_delivery_date=(today - timedelta(days=1)).isoformat())).status_code == 400, "entrega ayer rechazada")
        for bad in ["07/06/2024", "", None, "2024-13-01", 20240607]:
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], estimated_delivery_date=bad)).status_code == 400, f"entrega inválida {bad!r}")
        # Rechazos.
        for extra in [{"seller_id": ids["admin_id"]}, {"subtotal": "1"}, {"total": "1"}, {"order_number": "PED-000001"}, {"ordered_at": "2020-01-01"}]:
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], **extra)).status_code == 400, f"rechaza {extra}")
        for field in ["unit_price", "line_subtotal", "product_name"]:
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1, field: "1"}])).status_code == 400, f"rechaza item.{field}")
        for status in ["completed", "cancelled", "foo"]:
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], status=status)).status_code == 400, f"estado inicial inválido {status}")
        for qty in [0, -3, 2.5, "1", None, False]:
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": qty}])).status_code == 400, f"cantidad inválida {qty!r}")
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}, {"product_id": cemento, "quantity": 1}])).status_code == 400, "producto repetido")
        check(seller.post(api, json=body([])).status_code == 400, "sin items")
        check(seller.post(api, json=body([{"product_id": 999999, "quantity": 1}])).status_code == 404, "producto inexistente")
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], customer_id=999999)).status_code == 404, "cliente inexistente")
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], customer_id=inactive_id)).status_code == 409, "cliente inactivo")
        with app.app_context():
            check(Order.query.count() == 2 and OrderLine.query.count() == 3, "rechazos sin filas parciales")
        check(snapshot() == stock_before, "rechazos sin efectos de stock")

        # Transiciones (completar NO vende ni mueve stock).
        number = order["order_number"]
        check(seller.patch(f"{api}/{number}/status", json={"status": "completed"}).status_code == 409, "pending -> completed prohibido")
        check(seller.patch(f"{api}/{number}/status", json={"status": "processing"}).json["status_label"] == "En proceso", "pending -> processing")
        check(seller.patch(f"{api}/{number}/status", json={"status": "pending"}).status_code == 409, "sin retroceso")
        check(seller.patch(f"{api}/{number}/status", json={"status": "completed"}).json["status"] == "completed", "processing -> completed")
        check(seller.patch(f"{api}/{number}/status", json={"status": "cancelled"}).status_code == 409, "completed terminal")
        check(seller.patch(f"{api}/{number}/status", json={"status": "shipped"}).status_code == 400, "estado desconocido")
        check(seller.patch(f"{api}/PED-999999/status", json={"status": "processing"}).status_code == 404, "número inexistente")
        check(snapshot() == stock_before, "completar pedido no toca stock ni crea StockMovement")
        with app.app_context():
            check(StockMovement.query.filter(StockMovement.reason.like("%Pedido%")).count() == 0, "ningún movimiento por pedido")
        cancelled = seller.post(api, json=body([{"product_id": tubo, "quantity": 3}], customer_id=other_id, status="processing",
                                              estimated_delivery_date=(today + timedelta(days=20)).isoformat())).json
        check(cancelled["status"] == "processing" and cancelled["total"] == "29.97", "creado en proceso con precio actual")
        check(seller.patch(f"{api}/{cancelled['order_number']}/status", json={"status": "cancelled"}).json["status"] == "cancelled", "processing -> cancelled")
        check(seller.patch(f"{api}/{cancelled['order_number']}/status", json={"status": "processing"}).status_code == 409, "cancelled terminal")
        by_admin = admin.post(api, json=body([{"product_id": cemento, "quantity": 40}], customer_id=walk_in_id,
                                             estimated_delivery_date=(today + timedelta(days=2)).isoformat())).json
        check(by_admin["seller"]["name"] == "Admin" and by_admin["units"] == 40, "segundo vendedor")

        # Listado y filtros.
        get = lambda **q: seller.get(api, query_string=q).json  # noqa: E731
        listing = get()
        check(listing["total"] == 4 and listing["items"][0]["order_number"] == by_admin["order_number"], "lista reciente primero")
        check(listing["items"][0]["units"] == 40 and set(listing["items"][0]) >= {"order_number", "ordered_at", "customer", "units", "estimated_delivery_date", "status_label", "status_tone"}, "campos de la fila")
        check(get(search=number)["total"] == 1 and get(search="San José")["total"] == 1, "búsqueda por número/cliente")
        check(get(search="PVC-0")["total"] == 2 and get(search="Tubo PVC")["total"] == 2, "búsqueda por producto snapshot")
        check(get(status="completed")["total"] == 1 and get(status="pending")["total"] == 2 and get(status="cancelled")["total"] == 1, "filtro de estado")
        check(seller.get(api + "?status=Pendiente").status_code == 400, "estado inválido")
        yesterday = (today - timedelta(days=1)).isoformat()
        check(get(date_from=today.isoformat())["total"] == 4 and get(date_to=yesterday)["total"] == 0, "rango de fecha comercial")
        check(get(delivery_from=(today + timedelta(days=10)).isoformat())["total"] == 1 and get(delivery_to=today.isoformat())["total"] == 1
              and get(delivery_from=today.isoformat(), delivery_to=in_week)["total"] == 3, "rango de entrega")
        check(seller.get(api + "?delivery_from=x").status_code == 400, "entrega inválida en filtro")
        check(get(customer_id=other_id)["total"] == 1 and get(product_id=tubo)["total"] == 2 and get(seller_id=ids["admin_id"])["total"] == 1, "cliente/producto/vendedor")
        check(get(units_min=15)["total"] == 2 and get(units_max=3)["total"] == 2 and get(units_min=3, units_max=15)["total"] == 2, "cantidad mín/máx")
        check(seller.get(api + "?units_min=5&units_max=1").status_code == 400 and seller.get(api + "?units_min=-1").status_code == 400, "rango de cantidades inválido")
        check([o["units"] for o in get(sort="units")["items"]] == [1, 3, 15, 40], "orden por unidades")
        check(seller.get(api + "?sort=foo").status_code == 400, "orden no permitido")
        page = get(page=2, per_page=3)
        check(page["count"] == 1 and page["pages"] == 2, "paginación")
        check(seller.get(api + "?per_page=101").status_code == 400, "paginación acotada")
        options = seller.get(api + "/options").json
        check([o["value"] for o in options["create_statuses"]] == ["pending", "processing"] and [s["name"] for s in options["sellers"]] == ["Admin", "Vendedor"], "opciones reales")
        check(snapshot() == stock_before, "lecturas sin efectos")

        # Fecha comercial simulada: la entrega se valida contra el día Caracas del servidor.
        with patch.object(sales_common, "commercial_today", return_value=today + timedelta(days=30)):
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}])).status_code == 400, "entrega anterior al día comercial")

        # --- Metadatos: prioridad, estado de pago, teléfono, dirección, notas ---
        check([c for c, _ in migrate_order_metadata.COLUMNS] == ["priority", "payment_status", "contact_phone", "delivery_address", "notes"]
              and all(hasattr(Order, c) for c, _ in migrate_order_metadata.COLUMNS), "migración aditiva cubre las columnas del modelo")
        defaults = order  # creado sin metadatos
        check(defaults["priority"] == "medium" and defaults["priority_label"] == "Media" and defaults["payment_status"] == "pending"
              and defaults["payment_status_label"] == "Pendiente" and defaults["contact_phone"] is None and defaults["delivery_address"] is None
              and defaults["notes"] is None, "defaults medium/pending/NULL " + str({k: defaults[k] for k in ("priority", "payment_status")}))
        long_address, long_notes = "Av. " + "Bolívar " * 30, "Nota " * 90
        full = seller.post(api, json=body([{"product_id": cemento, "quantity": 2}], priority="urgent", payment_status="paid",
                                          contact_phone="  0414 123 4567 ", delivery_address=long_address, notes=long_notes)).json
        check(full["priority"] == "urgent" and full["priority_label"] == "Urgente" and full["priority_tone"] == "red", "prioridad urgente")
        check(full["payment_status"] == "paid" and full["payment_status_label"] == "Pagado" and full["payment_status_tone"] == "green", "pago pagado")
        check(full["contact_phone"] == "0414 123 4567" and full["delivery_address"] == long_address.strip() and full["notes"] == long_notes.strip(), "texto recortado")
        check(seller.get(f"{api}/{full['order_number']}").json["priority"] == "urgent", "metadatos persistidos")
        for extra in [{"priority": "Alta"}, {"priority": "critical"}, {"priority": 3}, {"payment_status": "partial"}, {"payment_status": "Pagado"},
                      {"contact_phone": "1" * 31}, {"delivery_address": "x" * 256}, {"notes": "n" * 501}, {"notes": ["a"]}]:
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], **extra)).status_code == 400, f"rechaza {list(extra)[0]}={str(list(extra.values())[0])[:12]!r}")
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], contact_phone="", notes=" ")).json["notes"] is None, "vacío -> NULL")
        for label, key in [("Baja", "low"), ("Alta", "high")]:
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], priority=key)).json["priority_label"] == label, f"prioridad {label}")
        check(get(priority="urgent")["total"] == 1 and get(priority="medium")["total"] == 5 and get(payment_status="paid")["total"] == 1
              and get(payment_status="pending", priority="low")["total"] == 1, "filtros prioridad / estado de pago")
        check(seller.get(api + "?priority=normal").status_code == 400 and seller.get(api + "?payment_status=partial").status_code == 400, "filtros con valores inválidos")
        check([o["value"] for o in options["priorities"]] == ["low", "medium", "high", "urgent"] and [o["value"] for o in options["payment_statuses"]] == ["pending", "paid"], "opciones de metadatos")
        check(snapshot() == stock_before, "metadatos sin efectos de stock")

        # Edición: pending todo; processing solo entrega+metadatos; completed/cancelled nada.
        edit_url = f"{api}/{full['order_number']}"
        edited = seller.patch(edit_url, json={"priority": "low", "notes": None, "contact_phone": "0212-5551234", "items": [{"product_id": tubo, "quantity": 4}],
                                              "customer_id": other_id, "estimated_delivery_date": (today + timedelta(days=3)).isoformat()}).json
        check(edited["priority"] == "low" and edited["notes"] is None and edited["contact_phone"] == "0212-5551234" and edited["delivery_address"] == long_address.strip(), "edición parcial de metadatos")
        check(edited["units"] == 4 and edited["total"] == "39.96" and edited["customer"]["id"] == other_id and edited["line_count"] == 1, "edición estructural en pending (precio vigente)")
        with app.app_context():
            check(OrderLine.query.filter(OrderLine.order_id == full["id"]).count() == 1, "renglones antiguos eliminados")
        check(seller.patch(edit_url, json={"status": "completed"}).status_code == 400 and seller.patch(edit_url, json={"total": "1"}).status_code == 400
              and seller.patch(edit_url, json={}).status_code == 400 and seller.patch(edit_url, json={"priority": "x"}).status_code == 400, "edición rechaza campos desconocidos/inválidos")
        seller.patch(f"{edit_url}/status", json={"status": "processing"})
        check(seller.patch(edit_url, json={"items": [{"product_id": cemento, "quantity": 1}]}).status_code == 409
              and seller.patch(edit_url, json={"customer_id": customer_id}).status_code == 409, "processing bloquea cliente/productos")
        check(seller.patch(edit_url, json={"priority": "high", "delivery_address": "Depósito 2"}).json["priority"] == "high", "processing edita metadatos")
        check(seller.patch(f"{api}/{number}", json={"priority": "high"}).status_code == 409, "completed sin ediciones")
        check(seller.patch(f"{api}/{cancelled['order_number']}", json={"notes": "x"}).status_code == 409, "cancelled sin ediciones")
        check(seller.patch(f"{api}/PED-999999", json={"priority": "high"}).status_code == 404, "editar inexistente")

        # Estado de pago: pending <-> paid, independiente del estado operativo.
        pay_url = f"{edit_url}/payment-status"
        check(seller.patch(pay_url, json={"payment_status": "pending"}).json["payment_status"] == "pending", "paid -> pending")
        check(seller.patch(pay_url, json={"payment_status": "paid"}).json["payment_status_label"] == "Pagado", "pending -> paid")
        for bad in ["partial", "Pagado", "", None, 1]:
            check(seller.patch(pay_url, json={"payment_status": bad}).status_code == 400, f"pago inválido {bad!r}")
        check(seller.patch(f"{api}/{number}/payment-status", json={"payment_status": "paid"}).json["payment_status"] == "paid"
              and seller.get(f"{api}/{number}").json["status"] == "completed", "completado sigue independiente del pago")
        check(seller.get(f"{api}/{cancelled['order_number']}").json["payment_status"] == "pending", "completar/cancelar no marca pagado")

        # Duplicar: nuevo PED, pending/pendiente, mismos renglones y metadatos, sin stock.
        dup = seller.post(f"{edit_url}/duplicate", json={})
        check(dup.status_code == 201 and dup.json["order_number"] != full["order_number"] and dup.json["order_number"] == f"PED-{dup.json['id']:06d}", "duplicado con número nuevo")
        source = seller.get(edit_url).json
        check(dup.json["status"] == "pending" and dup.json["payment_status"] == "pending" and dup.json["priority"] == source["priority"]
              and dup.json["contact_phone"] == source["contact_phone"] and dup.json["delivery_address"] == source["delivery_address"], "duplicado: pendiente, pago pendiente, metadatos copiados")
        check([(i["product_id"], i["quantity"], i["unit_price"]) for i in dup.json["items"]] == [(tubo, 4, "9.99")] and dup.json["customer"]["id"] == other_id, "duplicado: renglones y cliente")
        check(seller.post(f"{api}/PED-999999/duplicate", json={}).status_code == 404, "duplicar inexistente")

        # Impresión (solo lectura) y stock intacto tras todas las acciones.
        printed = seller.get(f"/sales/orders/{full['order_number']}/print").get_data(as_text=True)
        check(full["order_number"] in printed and "Depósito 2" in printed and "0212-5551234" in printed and "Tubo PVC" in printed and "Prioridad" in printed, "vista imprimible con metadatos")
        check(seller.get("/sales/orders/PED-999999/print").status_code == 404, "imprimir inexistente")
        check(snapshot() == stock_before, "editar/pagar/duplicar/imprimir no tocan stock ni StockMovement")

        # Permisos y páginas.
        check(app.test_client().get(api).status_code == 401, "anónimo")
        check(inv.patch(edit_url, json={"priority": "low"}).status_code == 403 and inv.patch(pay_url, json={"payment_status": "paid"}).status_code == 403
              and inv.post(f"{edit_url}/duplicate", json={}).status_code == 403 and inv.get(f"/sales/orders/{full['order_number']}/print").status_code == 200, "inventario: solo lectura e impresión")
        check(inv.get(api).status_code == 200 and inv.post(api, json=body([{"product_id": cemento, "quantity": 1}])).status_code == 403, "inventario lee, no crea")
        check(inv.patch(f"{api}/{number}/status", json={"status": "cancelled"}).status_code == 403, "inventario no cambia estado")
        html = seller.get("/sales/orders").get_data(as_text=True)
        check("ix_orders_live.js" in html and "ol-fixture" not in html and "ix_order_layers.js" not in html and "Constructora del Norte" not in html, "página live sin fixtures")
        check("No disponible en esta versión" not in html and 'id="ol-order-priority"' in html and 'id="ol-order-notes"' in html and "Estado de pago" in html, "drawer sin campos deshabilitados")
        check('data-can-sell="1"' in html and 'data-can-sell="0"' in inv.get("/sales/orders").get_data(as_text=True), "permiso de escritura en la página")
        with app.app_context():
            check(Order.query.filter(Order.order_number.is_(None)).count() == 0, "ningún pedido sin número")
            db.session.remove()
            db.engine.dispose()
    print(f"PASS: {checks} order checks (SQLite aislado). Stock y StockMovement intactos, incluso al completar.")


if __name__ == "__main__":
    run()
