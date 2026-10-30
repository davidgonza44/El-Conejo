"""Sales Core aislado: SQLite temporal, nunca la base operativa.

Prueba dinero, autoridad de precio, vendedor autenticado, atomicidad y rollback.
NO demuestra SELECT FOR UPDATE (ver scripts/test_sales_concurrency.py en MariaDB).

Uso: .\\.venv\\Scripts\\python.exe -B scripts/test_sales_core.py
"""
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from test_products_core import create_via_api, setup_app
from app.extensions import db
from app.models import Customer, Product, Sale, SaleLine, StockMovement
from app.models.customer import WALK_IN_NAME
from app.services import inventory_service


def run():
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1

    with tempfile.TemporaryDirectory(prefix="sales-core-") as folder:
        app, admin, inv, seller, ids = setup_app(Path(folder))
        app.logger.disabled = True
        with app.app_context():
            walk_in = Customer(name=WALK_IN_NAME, is_walk_in=True)
            customer = Customer(name="Constructora del Norte S.A.", tax_id="J-12345678-9")
            inactive = Customer(name="Inactivo", is_active=False)
            db.session.add_all([walk_in, customer, inactive])
            db.session.commit()
            walk_in_id, customer_id, inactive_id = walk_in.id, customer.id, inactive.id
        taladro = create_via_api(admin, code="TAL-750W", name="Taladro Percutor 1/2\" 750W", category_id=ids["construccion"], current_stock=24, sale_price="120.00").json["id"]
        disco = create_via_api(admin, code="DIS-4", name="Disco de Corte", category_id=ids["construccion"], current_stock=156, sale_price="2.50").json["id"]
        cemento = create_via_api(admin, code="CEM-50", name="Cemento Portland 50kg", category_id=ids["construccion"], current_stock=8, sale_price="8.90").json["id"]
        raro = create_via_api(admin, code="RAR-1", name="Precio raro", category_id=ids["construccion"], current_stock=100, sale_price="0.35").json["id"]
        with app.app_context():
            movements_before = StockMovement.query.count()
        api = "/api/sales"

        def body(items, **extra):
            return {"customer_id": customer_id, "payment_method": "transfer", "payment_condition": "cash",
                    "idempotency_key": str(uuid.uuid4()), "items": items, **extra}

        # Venta multilínea con descuentos: totales autoritativos.
        payload = body([{"product_id": cemento, "quantity": 5, "discount_percent": "10"},
                        {"product_id": taladro, "quantity": 2},
                        {"product_id": disco, "quantity": 10, "discount_percent": 0}])
        response = seller.post(api, json=payload)
        check(response.status_code == 201, "venta creada " + str(response.json))
        sale = response.json
        check(sale["sale_number"] == f"VTA-{sale['id']:06d}", "número VTA-{id:06d}")
        check(sale["subtotal"] == "309.50" and sale["discount_total"] == "4.45" and sale["total"] == "305.05", "totales Decimal 2 decimales")
        check([i["product_id"] for i in sale["items"]] == sorted([cemento, taladro, disco]), "renglones en orden de product_id")
        cem = next(i for i in sale["items"] if i["product_id"] == cemento)
        check(cem["unit_price"] == "8.90" and cem["line_subtotal"] == "44.50" and cem["discount_amount"] == "4.45" and cem["line_total"] == "40.05", "renglón con descuento")
        check(cem["product_code"] == "CEM-50" and cem["product_name"] == "Cemento Portland 50kg", "snapshot de código/nombre")
        check(sale["customer"]["id"] == customer_id and sale["seller"]["name"] == "Vendedor", "cliente real + vendedor autenticado")
        check(sale["payment_method_label"] == "Transferencia" and sale["payment_condition_label"] == "Contado" and sale["status"] == "completed", "etiquetas")
        check(sale["message"].startswith(f"Venta {sale['sale_number']} registrada"), "mensaje de éxito")
        with app.app_context():
            stocks = {p.id: p.current_stock for p in Product.query.all()}
            check(stocks[cemento] == 3 and stocks[taladro] == 22 and stocks[disco] == 146, "stock descontado exacto")
            exits = StockMovement.query.filter(StockMovement.movement_type == "salida").all()
            check(len(exits) == 3 and all(m.reason == f"Venta {sale['sale_number']}" for m in exits), "una salida por renglón con motivo")
            by_product = {m.product_id: m for m in exits}
            check(by_product[cemento].previous_stock == 8 and by_product[cemento].new_stock == 3, "previous/new stock")
            lines = SaleLine.query.order_by(SaleLine.id).all()
            check({l.stock_movement_id for l in lines} == {m.id for m in exits}, "SaleLine -> StockMovement uno a uno")
            check(all(m.user_id == lines[0].sale.seller_id for m in exits), "movimiento a nombre del vendedor")
            db.session.get(Product, taladro).sale_price = Decimal("999.00")
            db.session.commit()
        detail = admin.get(f"{api}/{sale['sale_number']}").json
        check(next(i for i in detail["items"] if i["product_id"] == taladro)["unit_price"] == "120.00", "histórico inmutable ante cambio de precio")
        check(admin.get(f"{api}/{sale['sale_number']}").json["items"][0]["image_url"] is None, "imagen actual del producto")

        # Idempotencia: misma clave -> 200, misma venta, sin nuevo stock.
        replay = seller.post(api, json=payload)
        check(replay.status_code == 200 and replay.json["sale_number"] == sale["sale_number"], "replay idempotente 200")
        with app.app_context():
            check(Sale.query.count() == 1 and StockMovement.query.count() == movements_before + 3, "replay sin efectos")

        # Redondeo ROUND_HALF_UP: 3 * 0.35 = 1.05; 50% = 0.525 -> 0.53 (banker's daría 0.52).
        rounding = seller.post(api, json=body([{"product_id": raro, "quantity": 3, "discount_percent": "50.004"}])).json
        check(rounding["subtotal"] == "1.05" and rounding["items"][0]["discount_percent"] == "50.00" and rounding["discount_total"] == "0.53" and rounding["total"] == "0.52", "ROUND_HALF_UP " + str(rounding))

        # Público en general.
        public = seller.post(api, json=body([{"product_id": disco, "quantity": 1}], customer_id=walk_in_id))
        check(public.status_code == 201 and public.json["customer"]["is_walk_in"], "venta a Público en general")
        check(admin.delete(f"/api/customers/{customer_id}").json["action"] == "deactivated", "cliente con ventas se desactiva")
        with app.app_context():
            db.session.get(Customer, customer_id).is_active = True
            db.session.commit()

        with app.app_context():
            snapshot = ({p.id: p.current_stock for p in Product.query.all()}, Sale.query.count(), StockMovement.query.count(), SaleLine.query.count())

        def unchanged(label):
            with app.app_context():
                check(({p.id: p.current_stock for p in Product.query.all()}, Sale.query.count(), StockMovement.query.count(), SaleLine.query.count()) == snapshot, "sin efectos: " + label)

        # Rechazos: campos autoritativos del cliente.
        for extra in [{"seller_id": ids["admin_id"]}, {"subtotal": "1"}, {"total": "1"}, {"sale_number": "VTA-000001"}, {"status": "completed"}, {"sold_at": "2020-01-01"}]:
            check(seller.post(api, json=body([{"product_id": disco, "quantity": 1}], **extra)).status_code == 400, f"rechaza {extra}")
        for field in ["unit_price", "line_total", "previous_stock", "new_stock", "product_name"]:
            check(seller.post(api, json=body([{"product_id": disco, "quantity": 1, field: "1"}])).status_code == 400, f"rechaza item.{field}")
        # Validaciones de negocio.
        check(seller.post(api, json=body([{"product_id": disco, "quantity": 1}, {"product_id": disco, "quantity": 1}])).status_code == 400, "producto repetido")
        for qty in [0, -1, 1.5, "2.0", "x", None, True]:
            check(seller.post(api, json=body([{"product_id": disco, "quantity": qty}])).status_code == 400, f"cantidad inválida {qty!r}")
        for disc in [-1, 101, "abc", 1.5, True, "1e400"]:
            check(seller.post(api, json=body([{"product_id": disco, "quantity": 1, "discount_percent": disc}])).status_code == 400, f"descuento inválido {disc!r}")
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 4}])).status_code == 409, "stock insuficiente")
        check(seller.post(api, json=body([{"product_id": 999999, "quantity": 1}])).status_code == 404, "producto inexistente")
        check(seller.post(api, json=body([{"product_id": disco, "quantity": 1}], customer_id=999999)).status_code == 404, "cliente inexistente")
        check(seller.post(api, json=body([{"product_id": disco, "quantity": 1}], customer_id=inactive_id)).status_code == 409, "cliente inactivo")
        check(seller.post(api, json=body([{"product_id": disco, "quantity": 1}], payment_method="bitcoin")).status_code == 400, "método fuera de lista")
        check(seller.post(api, json=body([{"product_id": disco, "quantity": 1}], payment_condition="net_45")).status_code == 400, "condición fuera de lista")
        check(seller.post(api, json=body([])).status_code == 400, "sin items")
        check(seller.post(api, json=body([{"product_id": disco, "quantity": 1}], idempotency_key="corta")).status_code == 400, "clave corta")
        check(seller.post(api, json={"customer_id": customer_id}).status_code == 400, "sin clave")
        unchanged("validaciones")

        # Rollback si falla el segundo renglón (stock insuficiente en cemento): nada del primero queda.
        failing = body([{"product_id": disco, "quantity": 5}, {"product_id": cemento, "quantity": 99}])
        response = seller.post(api, json=failing)
        check(response.status_code == 409 and "Cemento" in response.json["error"] and "disponible 3" in response.json["error"], "409 identifica producto y disponible")
        unchanged("rollback segundo renglón")
        # Producto inactivo en la segunda posición.
        with app.app_context():
            db.session.get(Product, taladro).is_active = False
            db.session.commit()
        check(seller.post(api, json=body([{"product_id": disco, "quantity": 1}, {"product_id": taladro, "quantity": 1}])).status_code == 409, "producto inactivo")
        unchanged("rollback inactivo")
        # Rollback si falla la creación del movimiento o el commit.
        with patch.object(inventory_service, "_create_movement", side_effect=RuntimeError("synthetic")):
            check(seller.post(api, json=body([{"product_id": disco, "quantity": 1}])).status_code == 500, "fallo de movimiento -> 500")
        unchanged("rollback movimiento")
        with patch.object(db.session, "commit", side_effect=RuntimeError("synthetic")):
            check(seller.post(api, json=body([{"product_id": disco, "quantity": 1}])).status_code == 500, "fallo de commit -> 500")
        unchanged("rollback commit")
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 3}])).status_code == 201, "vende las últimas unidades")
        check(seller.post(api, json=body([{"product_id": cemento, "quantity": 1}])).status_code == 409, "sin stock -> 409")
        with app.app_context():
            check(db.session.get(Product, cemento).current_stock == 0 and Product.query.filter(Product.current_stock < 0).count() == 0, "nunca negativo")

        # Lecturas.
        listing = seller.get(api).json
        check(listing["total"] == 4 and listing["items"][0]["sale_number"] > listing["items"][1]["sale_number"], "lista reciente primero")
        check(seller.get(api, query_string={"search": "Constructora"}).json["total"] == 3, "búsqueda por cliente")
        check(seller.get(api, query_string={"search": sale["sale_number"]}).json["total"] == 1, "búsqueda por número")
        check(seller.get(api, query_string={"customer_id": walk_in_id}).json["total"] == 1, "filtro cliente")
        check(seller.get(api, query_string={"page": 2, "per_page": 3}).json["count"] == 1, "paginación")
        check(seller.get(api + "?status=cancelled").status_code == 400, "estado desconocido")
        check(seller.get(api + "/VTA-999999").status_code == 404, "detalle inexistente")
        products = seller.get(api + "/products", query_string={"search": "cem"}).json
        check(products["count"] == 1 and set(products["items"][0]) == {"id", "code", "name", "image_url", "sale_price", "current_stock", "minimum_stock", "stock_status", "category_id", "category"}, "búsqueda segura sin purchase_price")
        by_status = {s: seller.get(api + "/products", query_string={"stock_status": s}).json["items"] for s in ("disponible", "bajo_stock", "agotado")}
        check(all({p["stock_status"] for p in items} <= {s} for s, items in by_status.items()) and len(by_status["agotado"]) >= 1
              and sum(len(items) for items in by_status.values()) == seller.get(api + "/products", query_string={"limit": 50}).json["count"], "filtro de estado de stock exclusivo del selector")
        check(seller.get(api + "/products", query_string={"stock_status": "x"}).status_code == 400 and seller.get(api + "/products", query_string={"category_id": "abc"}).status_code == 400, "filtros inválidos del selector")
        check(seller.get(api + "/products", query_string={"category_id": products["items"][0]["category_id"]}).json["count"] >= 1 and seller.get(api + "/products", query_string={"category_id": 999999}).json["count"] == 0, "filtro por categoría real")
        check([c["name"] for c in seller.get(api + "/options").json["categories"]] == sorted(c["name"] for c in seller.get(api + "/options").json["categories"]), "categorías activas ordenadas en opciones")
        check(products["items"][0]["sale_price"] == "8.90" and products["items"][0]["stock_status"] == "agotado", "campos del catálogo de venta")
        check(seller.get(api + "/products").json["count"] == 3 and all(i["code"] != "TAL-750W" for i in seller.get(api + "/products").json["items"]), "solo activos")
        check(seller.get(api + "/products?limit=51").status_code == 400, "límite acotado")
        options = seller.get(api + "/options").json
        check([o["value"] for o in options["payment_methods"]] == ["transfer", "cash", "credit_card", "debit_card", "check", "receivable"], "métodos aprobados")
        check([o["value"] for o in options["payment_conditions"]] == ["cash", "net_15", "net_30", "net_60", "net_90", "custom"], "condiciones aprobadas")

        # Permisos.
        check(app.test_client().post(api, json=body([])).status_code == 401, "anónimo")
        check(inv.get(api).status_code == 200 and inv.get(api + "/products").status_code == 200, "inventario lee")
        check(inv.post(api, json=body([{"product_id": disco, "quantity": 1}])).status_code == 403, "inventario no vende")
        check(admin.post(api, json=body([{"product_id": disco, "quantity": 1}])).status_code == 403, "admin no vende")
        check(admin.get("/sales/new").status_code == 403, "admin no abre cobro")
        inv_new = inv.get("/sales/new")
        check(seller.get("/sales/new").status_code == 200 and inv_new.status_code == 403, "páginas live")
        html = seller.get("/sales/new").get_data(as_text=True)
        check("ix_sales_live.js" in html and "spl-fixtures" not in html and "ix_sale_controls.js" not in html and "Constructora del Norte" not in html, "live sin fixtures ni JS de referencia")
        check('id="spl-drawer"' in html and "ix_sales_shared.js" in html and 'id="spl-scanner"' not in html and 'value="bajo_stock"' in html, "selector de productos compartido en live, sin escáner simulado")
        check("Vendedor" in html and 'data-can-sell="1"' in html, "vendedor autenticado en la página")
        check("No tiene permisos para acceder a esta sección." in inv_new.get_data(as_text=True), "inventario no abre el alta")
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    print(f"PASS: {checks} sale checks (SQLite aislado). SQLite no demuestra bloqueo de filas.")


if __name__ == "__main__":
    run()
