"""Quotes Core aislado: SQLite temporal, nunca la base operativa.

Prueba número COT, snapshot de precio, Decimal/descuento, validez, vencimiento computado,
transiciones, filtros/paginación, permisos y, sobre todo, que una cotización NUNCA cambia
stock ni crea StockMovement.

Uso: .\\.venv\\Scripts\\python.exe -B scripts/test_quotes_core.py
"""
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from test_products_core import create_via_api, setup_app
from app.extensions import db
from app.models import Customer, Product, Quote, QuoteLine, StockMovement
from app.models.customer import WALK_IN_NAME
from app.services import sales_common
from app.services.inventory_snapshot_service import commercial_today


def run():
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1

    with tempfile.TemporaryDirectory(prefix="quotes-core-") as folder:
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
        cemento = create_via_api(admin, code="CEM-001", name="Cemento Gris 42.5 kg", category_id=ids["construccion"], current_stock=8, sale_price="8.50").json["id"]
        cable = create_via_api(admin, code="CAB-012", name="Cable Eléctrico THHN 12 AWG", category_id=ids["electricidad"], current_stock=0, sale_price="18.75").json["id"]
        raro = create_via_api(admin, code="RAR-1", name="Precio raro", category_id=ids["construccion"], current_stock=100, sale_price="0.35").json["id"]
        api = "/api/quotes"

        def snapshot():
            with app.app_context():
                return ({p.id: p.current_stock for p in Product.query.all()}, StockMovement.query.count())
        stock_before = snapshot()

        def body(items, **extra):
            return {"customer_id": customer_id, "validity_days": 30, "items": items, **extra}

        # Creación multilínea con descuento: totales autoritativos; producto agotado se cotiza igual.
        response = seller.post(api, json=body([{"product_id": cemento, "quantity": 50}, {"product_id": cable, "quantity": 20}],
                                              discount_percent="0", notes="  Entrega en obra  ", status="pending"))
        check(response.status_code == 201, "cotización creada " + str(response.json))
        quote = response.json
        check(quote["quote_number"] == f"COT-{quote['id']:06d}", "número COT-{id:06d}")
        check(quote["subtotal"] == "800.00" and quote["discount_total"] == "0.00" and quote["total"] == "800.00", "totales " + str(quote))
        check(quote["validity_days"] == 30 and quote["validity_label"] == "30 días", "validez")
        check(quote["valid_until"] == (commercial_today() + timedelta(days=30)).isoformat(), "valid_until derivada en servidor")
        check(quote["status"] == "pending" and quote["effective_status"] == "pending" and quote["status_label"] == "Pendiente", "estado inicial")
        check(quote["seller"]["name"] == "Vendedor" and quote["customer"]["id"] == customer_id and quote["notes"] == "Entrega en obra", "vendedor autenticado, cliente, notas")
        line = next(i for i in quote["items"] if i["product_id"] == cable)
        check(line["product_code"] == "CAB-012" and line["product_name"] == "Cable Eléctrico THHN 12 AWG" and line["unit_price"] == "18.75" and line["line_subtotal"] == "375.00", "snapshot de renglón")
        check(quote["message"].startswith(f"Cotización {quote['quote_number']} guardada"), "mensaje")
        check(snapshot() == stock_before, "crear cotización no toca stock ni StockMovement")

        # Descuento a nivel de cotización con ROUND_HALF_UP: 3 * 0.35 = 1.05; 50% = 0.525 -> 0.53.
        rounding = seller.post(api, json=body([{"product_id": raro, "quantity": 3}], discount_percent="50")).json
        check(rounding["subtotal"] == "1.05" and rounding["discount_percent"] == "50.00" and rounding["discount_total"] == "0.53" and rounding["total"] == "0.52", "ROUND_HALF_UP " + str(rounding))
        with app.app_context():
            db.session.get(Product, cable).sale_price = Decimal("99.00")
            db.session.get(Product, cable).name = "Cable renombrado"
            db.session.commit()
        detail = admin.get(f"{api}/{quote['quote_number']}").json
        cable_line = next(i for i in detail["items"] if i["product_id"] == cable)
        check(cable_line["unit_price"] == "18.75" and cable_line["product_name"] == "Cable Eléctrico THHN 12 AWG" and detail["total"] == "800.00", "histórico inmutable ante cambios del producto")

        # Rechazos.
        for extra in [{"seller_id": ids["admin_id"]}, {"subtotal": "1"}, {"total": "1"}, {"quote_number": "COT-000001"}, {"quoted_at": "2020-01-01"}, {"valid_until": "2030-01-01"}]:
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], **extra)).status_code == 400, f"rechaza {extra}")
        for field in ["unit_price", "line_subtotal", "product_name", "product_code"]:
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1, field: "1"}])).status_code == 400, f"rechaza item.{field}")
        for days in [0, 10, "x", None, True, 45]:
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], validity_days=days)).status_code == 400, f"validez inválida {days!r}")
        for disc in [-1, 101, "abc", 1.5, True]:
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], discount_percent=disc)).status_code == 400, f"descuento inválido {disc!r}")
        for status in ["expired", "Vencida", "foo", 1]:
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], status=status)).status_code == 400, f"estado inválido {status!r}")
        for qty in [0, -1, 1.5, "2", None, True]:
            check(seller.post(api, json=body([{"product_id": cemento, "quantity": qty}])).status_code == 400, f"cantidad inválida {qty!r}")
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}, {"product_id": cemento, "quantity": 2}])).status_code == 400, "producto repetido")
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], notes="x" * 501)).status_code == 400, "notas > 500")
        check(seller.post(api, json=body([])).status_code == 400, "sin items")
        check(seller.post(api, json=body([{"product_id": 999999, "quantity": 1}])).status_code == 404, "producto inexistente")
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], customer_id=999999)).status_code == 404, "cliente inexistente")
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], customer_id=inactive_id)).status_code == 409, "cliente inactivo")
        with app.app_context():
            db.session.get(Product, raro).is_active = False
            db.session.commit()
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}, {"product_id": raro, "quantity": 1}])).status_code == 409, "producto inactivo")
        with app.app_context():
            check(Quote.query.count() == 2 and QuoteLine.query.count() == 3, "rechazos sin filas parciales")
        check(snapshot() == stock_before, "rechazos sin efectos de stock")

        # Vencimiento computado: se crea "ayer -30 días" con validez 7; hoy real la muestra Vencida sin escribirla.
        past = commercial_today() - timedelta(days=30)
        with patch.object(sales_common, "commercial_today", return_value=past):
            expired = seller.post(api, json=body([{"product_id": cemento, "quantity": 1}], validity_days=7, status="sent")).json
        check(expired["valid_until"] == (past + timedelta(days=7)).isoformat(), "valid_until en el pasado")
        shown = seller.get(f"{api}/{expired['quote_number']}").json
        check(shown["status"] == "sent" and shown["effective_status"] == "expired" and shown["status_label"] == "Vencida", "Vencida computada")
        with app.app_context():
            check(db.session.get(Quote, expired["id"]).status == "sent", "GET no escribe 'expired' en la BD")
        check(seller.patch(f"{api}/{expired['quote_number']}/status", json={"status": "accepted"}).status_code == 409, "vencida no se acepta")
        check(seller.patch(f"{api}/{expired['quote_number']}/status", json={"status": "rejected"}).status_code == 200, "vencida sí se rechaza")

        # Transiciones.
        number = quote["quote_number"]
        check(seller.patch(f"{api}/{number}/status", json={"status": "expired"}).status_code == 400, "estado no persistible")
        check(seller.patch(f"{api}/{number}/status", json={"status": "sent"}).json["status"] == "sent", "pending -> sent")
        check(seller.patch(f"{api}/{number}/status", json={"status": "pending"}).status_code == 409, "sent -> pending prohibido")
        check(seller.patch(f"{api}/{number}/status", json={"status": "accepted"}).json["status_label"] == "Aceptada", "sent -> accepted")
        check(seller.patch(f"{api}/{number}/status", json={"status": "rejected"}).status_code == 409, "accepted terminal")
        check(seller.patch(f"{api}/{number}/status", json={"status": "accepted"}).status_code == 409, "accepted -> accepted no es transición")
        check(seller.patch(f"{api}/COT-999999/status", json={"status": "sent"}).status_code == 404, "número inexistente")
        rejected = seller.post(api, json=body([{"product_id": cemento, "quantity": 2}], customer_id=other_id, validity_days=15)).json
        check(seller.patch(f"{api}/{rejected['quote_number']}/status", json={"status": "rejected"}).json["status"] == "rejected", "pending -> rejected")
        check(seller.patch(f"{api}/{rejected['quote_number']}/status", json={"status": "sent"}).status_code == 409, "rejected terminal")
        check(snapshot() == stock_before, "transiciones sin efectos de stock")

        # Admin cotiza (segundo vendedor) para el filtro Vendedor.
        by_admin = admin.post(api, json=body([{"product_id": cable, "quantity": 1}], customer_id=walk_in_id, validity_days=7)).json
        check(by_admin["seller"]["name"] == "Admin" and by_admin["total"] == "99.00", "precio actual al cotizar hoy")

        # Listado y filtros (server-side).
        listing = seller.get(api).json
        check(listing["total"] == 5 and listing["items"][0]["quote_number"] == by_admin["quote_number"], "lista reciente primero")
        check(set(listing["items"][0]) >= {"quote_number", "quoted_at", "customer", "validity_label", "total", "status_label", "status_tone", "effective_status"}, "campos de la fila")
        get = lambda **q: seller.get(api, query_string=q).json  # noqa: E731
        check(get(search=number)["total"] == 1, "búsqueda por número")
        check(get(search="San José")["total"] == 1, "búsqueda por cliente")
        check(get(search="THHN")["total"] == 1 and get(search="cab-0")["total"] == 2, "búsqueda por nombre/código snapshot")
        check(get(search="%")["total"] == 0, "comodín escapado")
        check(get(status="expired")["total"] == 0 and get(status="rejected")["total"] == 2, "vencida rechazada ya no cuenta como expired")
        check(get(status="accepted")["total"] == 1 and get(status="pending")["total"] == 2 and get(status="sent")["total"] == 0, "filtro de estado")
        with patch.object(sales_common, "commercial_today", return_value=commercial_today() + timedelta(days=10)):
            check(get(status="expired")["total"] == 1 and get(status="pending")["total"] == 1, "expired computado excluye a pending")
        check(seller.get(api + "?status=Vencida").status_code == 400, "estado desconocido")
        today = commercial_today().isoformat()
        yesterday = (commercial_today() - timedelta(days=1)).isoformat()
        check(get(date_from=today)["total"] == 5 and get(date_to=yesterday)["total"] == 0 and get(date_from=today, date_to=today)["total"] == 5, "rango de fecha comercial")
        check(seller.get(api + "?date_from=31/05/2024").status_code == 400, "fecha inválida")
        check(get(customer_id=other_id)["total"] == 1 and get(customer_id=walk_in_id)["total"] == 1, "filtro cliente")
        check(get(product_id=cable)["total"] == 2 and get(product_id=raro)["total"] == 1, "filtro producto")
        check(get(seller_id=ids["admin_id"])["total"] == 1, "filtro vendedor")
        check(get(validity_days=7)["total"] == 2 and get(validity_days=15)["total"] == 1, "filtro validez")
        check(seller.get(api + "?validity_days=10").status_code == 400, "validez de filtro inválida")
        check(get(total_min="100")["total"] == 1 and get(total_max="1")["total"] == 1 and get(total_min="8", total_max="20")["total"] == 2, "total mínimo/máximo")
        check(seller.get(api + "?total_min=5&total_max=1").status_code == 400 and seller.get(api + "?total_min=abc").status_code == 400, "rango de totales inválido")
        check([q["total"] for q in get(sort="total")["items"]][:2] == ["0.52", "8.50"], "orden por total asc")
        check(seller.get(api + "?sort=stock").status_code == 400, "orden no permitido")
        page = get(page=2, per_page=2)
        check(page["count"] == 2 and page["pages"] == 3 and page["page"] == 2, "paginación")
        check(seller.get(api + "?per_page=101").status_code == 400 and seller.get(api + "?page=0").status_code == 400, "paginación acotada")
        check(seller.get(api + "/COT-999999").status_code == 404, "detalle inexistente")
        options = seller.get(api + "/options").json
        check([o["value"] for o in options["filter_statuses"]] == ["pending", "sent", "accepted", "rejected", "expired"], "estados de filtro")
        check([s["name"] for s in options["sellers"]] == ["Admin", "Vendedor"] and [v["value"] for v in options["validity_days"]] == [7, 15, 30], "opciones reales")
        check(snapshot() == stock_before, "lecturas sin efectos")

        # Permisos y páginas.
        check(app.test_client().post(api, json=body([])).status_code == 401 and app.test_client().get(api).status_code == 401, "anónimo")
        check(inv.get(api).status_code == 200 and inv.get(api + "/options").status_code == 200, "inventario lee")
        check(inv.post(api, json=body([{"product_id": cemento, "quantity": 1}])).status_code == 403, "inventario no cotiza")
        check(inv.patch(f"{api}/{number}/status", json={"status": "sent"}).status_code == 403, "inventario no cambia estado")
        html = seller.get("/sales/quotes").get_data(as_text=True)
        check("ix_quotes_live.js" in html and "ql-fixture" not in html and "ix_quote_layers.js" not in html and "Constructora del Norte" not in html, "página live sin fixtures")
        check('data-can-sell="1"' in html and 'data-can-sell="0"' in inv.get("/sales/quotes").get_data(as_text=True), "permiso de escritura en la página")
        with app.app_context():
            check(Quote.query.filter(Quote.quote_number.is_(None)).count() == 0, "ninguna cotización sin número")
            db.session.remove()
            db.engine.dispose()
    print(f"PASS: {checks} quote checks (SQLite aislado). Stock y StockMovement intactos en todo el flujo.")


if __name__ == "__main__":
    run()
