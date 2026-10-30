"""Customers Core aislado: SQLite temporal, nunca la base operativa.

Uso: .\\.venv\\Scripts\\python.exe -B scripts/test_customers_core.py
"""
import tempfile
from pathlib import Path

from test_products_core import setup_app
from app.extensions import db
from app.models import Customer
from app.models.customer import WALK_IN_NAME


def run():
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1

    with tempfile.TemporaryDirectory(prefix="customers-core-") as folder:
        app, admin, inv, seller, ids = setup_app(Path(folder))
        with app.app_context():
            db.session.add(Customer(name=WALK_IN_NAME, is_walk_in=True))
            db.session.commit()
        api = "/api/customers"

        response = seller.post(api, json={"name": "  Constructora del Norte S.A. ", "tax_id": "j123456789",
                                          "email": "Contacto@Constructora.com", "phone": "0412-555-1234"})
        check(response.status_code == 201, "vendedor crea cliente")
        first = response.json
        check(first["name"] == "Constructora del Norte S.A." and first["is_active"] and not first["is_walk_in"], "trim + defaults")
        check(first["tax_id"] == "J-12345678-9", "RIF normalizado compacto")
        check(first["email"] == "contacto@constructora.com", "correo en minúsculas")
        for raw in ["J-12345678-9", "j 12345678 9", "J.12345678.9", "J12345678-9"]:
            check(admin.post(api, json={"name": "Dup", "tax_id": raw}).status_code == 409, "RIF duplicado " + raw)
        for bad in ["X-12345678-9", "J-1234-5", "12345678", "J-12345678-99", 12345]:
            check(admin.post(api, json={"name": "Malo", "tax_id": bad}).status_code == 400, f"RIF inválido {bad}")
        check(admin.post(api, json={"name": "Solo nombre"}).status_code == 201, "campos opcionales vacíos")
        check(admin.post(api, json={"name": "Otro", "tax_id": "", "email": " ", "phone": None}).status_code == 201, "vacíos -> NULL")
        check(admin.post(api, json={"name": "Otro correo", "email": "contacto@constructora.com"}).status_code == 409, "correo duplicado")
        check(admin.post(api, json={"name": "Correo malo", "email": "no-es-correo"}).status_code == 400, "correo inválido")
        check(admin.post(api, json={"name": ""}).status_code == 400, "nombre obligatorio")
        check(admin.post(api, json={"name": "x" * 151}).status_code == 400, "nombre largo")
        check(admin.post(api, json={"name": "Público en General"}).status_code == 409, "no duplica genérico por nombre")
        check(admin.post(api, json={"name": "Genérico 2", "is_walk_in": True}).status_code == 400, "no duplica genérico por flag")

        listing = admin.get(api).json
        check(listing["total"] == 4 and listing["items"][0]["is_walk_in"], "genérico encabeza la lista")
        check(set(listing) >= {"count", "items", "total", "page", "per_page", "pages"}, "forma de paginación")
        for term, expected in [("norte", 1), ("J-12345678", 1), ("0412", 1), ("constructora.com", 1), ("%", 0), ("zzz", 0)]:
            check(admin.get(api, query_string={"search": term}).json["total"] == expected, "búsqueda " + term)
        page = admin.get(api, query_string={"page": 2, "per_page": 2}).json
        check(page["pages"] == 2 and page["count"] == 2 and page["page"] == 2, "paginación")
        check(admin.get(api, query_string={"per_page": 10}).json["count"] == 4, "autocompletado per_page=10")
        for query in ["page=0", "per_page=101", "sort=name;drop", "status=raro", "page=x"]:
            check(admin.get(api + "?" + query).status_code == 400, "parámetro inválido " + query)
        ordered = admin.get(api, query_string={"sort": "-name", "status": "active"}).json["items"]
        check([c["name"] for c in ordered[1:]] == sorted((c["name"] for c in ordered[1:]), reverse=True), "orden descendente tras el genérico")

        cid = first["id"]
        check(admin.get(f"{api}/{cid}").json["tax_id"] == "J-12345678-9", "detalle")
        check(admin.get(f"{api}/999999").status_code == 404, "detalle inexistente")
        updated = seller.put(f"{api}/{cid}", json={"phone": "0414-000-0000", "tax_id": "v-123456-7"})
        check(updated.status_code == 200 and updated.json["phone"] == "0414-000-0000" and updated.json["tax_id"] == "V-123456-7", "edición parcial")
        check(admin.put(f"{api}/{cid}", json={"name": "Público en general"}).status_code == 409, "no renombrar a genérico")
        check(admin.put(f"{api}/{cid}", json={"is_active": False}).json["is_active"] is False, "desactivar")
        check(admin.get(api, query_string={"status": "inactive"}).json["total"] == 1, "filtro inactivos")
        check(admin.put(f"{api}/{cid}", json={"is_active": "no"}).status_code == 400, "is_active no booleano")

        walk_in = listing["items"][0]["id"]
        for body in [{"name": "Otro"}, {"is_active": False}, {"tax_id": "J-11111111-1"}]:
            check(admin.put(f"{api}/{walk_in}", json=body).status_code == 409, f"genérico protegido {body}")
        check(admin.put(f"{api}/{walk_in}", json={"phone": "0212-000-0000"}).status_code == 200, "genérico admite teléfono")
        check(admin.delete(f"{api}/{walk_in}").status_code == 409, "genérico no se borra")

        deleted = admin.delete(f"{api}/{cid}")
        check(deleted.status_code == 200 and deleted.json["action"] == "deleted", "sin uso -> borrado")
        check(admin.get(f"{api}/{cid}").status_code == 404, "borrado efectivo")
        # La relación con ventas (desactivar en lugar de borrar) se cubre en test_sales_core.

        check(app.test_client().get(api).status_code == 401, "anónimo")
        check(inv.get(api).status_code == 200, "inventario lee")
        check(inv.post(api, json={"name": "No"}).status_code == 403, "inventario no escribe")
        check(inv.delete(f"{api}/{walk_in}").status_code == 403, "inventario no borra")
        check(admin.get("/sales/customers").status_code == 200, "página live clientes")
        html = admin.get("/sales/customers").get_data(as_text=True)
        check("ix_customers_live.js" in html and "Constructora del Norte" not in html, "live sin fixtures")
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    print(f"PASS: {checks} customer checks (SQLite aislado).")


if __name__ == "__main__":
    run()
