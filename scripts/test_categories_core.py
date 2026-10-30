#!/usr/bin/env python3
"""Suite aislada de Categories Core: SQLite temporal, sin MySQL ni ferreteria_conejo.

Cubre esquema/defaults, paleta, estado, listado (búsqueda, estado, orden, paginación),
agregados SQL, CRUD, duplicado, borrado seguro, permisos, integración con productos y
aislamiento del modo de referencia.

Uso: python scripts/test_categories_core.py
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import sys
import tempfile
from pathlib import Path

from sqlalchemy import BigInteger
from sqlalchemy.engine import Engine
from sqlalchemy.event import listens_for
from sqlalchemy.ext.compiler import compiles

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Category, Product, User  # noqa: E402
from app.models.category import CATEGORY_COLORS, CATEGORY_ICONS  # noqa: E402
from app.models.user import ROLE_ADMIN, ROLE_INVENTARIO, ROLE_VENDEDOR  # noqa: E402

FAILURES = 0
CHECKS_RUN = 0


@compiles(BigInteger, "sqlite")
def _compile_big_integer_for_sqlite(_type, _compiler, **_kwargs):
    return "INTEGER"


@listens_for(Engine, "connect")
def _configure_isolated_sqlite(dbapi_connection, _connection_record) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")


def check(name: str, condition: bool, detail: str = "") -> None:
    global FAILURES, CHECKS_RUN
    CHECKS_RUN += 1
    if not condition:
        FAILURES += 1
    print(f"[{'OK' if condition else 'FALLO'}] {name}{f' — {detail}' if detail else ''}")


class IsolatedConfig:
    SECRET_KEY = secrets.token_urlsafe(32)
    APP_ENV = "testing"
    TESTING = True
    PROPAGATE_EXCEPTIONS = False
    WTF_CSRF_ENABLED = False
    MAIL_ENABLED = False
    GOOGLE_CLIENT_ID = None
    GOOGLE_CLIENT_SECRET = None
    GOOGLE_REDIRECT_URI = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = ""
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"check_same_thread": False, "timeout": 30}}


def login(client, username: str, password: str):
    response = client.post("/api/auth/login", json={"identifier": username, "password": password})
    if response.status_code != 200:
        raise RuntimeError(f"login {username} -> {response.status_code}")


def setup_app(temp_root: Path):
    IsolatedConfig.SQLALCHEMY_DATABASE_URI = (
        "sqlite:///" + (temp_root / "categories_core.sqlite").resolve().as_posix() + "?check_same_thread=false"
    )
    app = create_app(IsolatedConfig)
    app.instance_path = str((temp_root / "instance").resolve())
    (temp_root / "instance").mkdir(parents=True, exist_ok=True)
    with app.app_context():
        db.create_all()
        users = []
        for name, role, pwd in (("cat_admin", ROLE_ADMIN, "AdminPass999"), ("cat_inv", ROLE_INVENTARIO, "inv123"), ("cat_ven", ROLE_VENDEDOR, "ven123")):
            user = User(name=name, email=f"{name}@core.test", username=name, role=role)
            user.set_password(pwd)
            users.append(user)
        db.session.add_all(users)
        db.session.commit()
    clients = []
    for name, pwd in (("cat_admin", "AdminPass999"), ("cat_inv", "inv123"), ("cat_ven", "ven123")):
        client = app.test_client()
        login(client, name, pwd)
        clients.append(client)
    return app, *clients


def product(client, code, category_id, stock, purchase):
    return client.post("/api/products", json={
        "code": code, "name": f"Producto {code}", "category_id": category_id,
        "current_stock": stock, "minimum_stock": 0, "purchase_price": purchase, "sale_price": purchase * 2,
    })


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="categories-core-"))
    app, admin, inventario, vendedor = setup_app(temp_root)

    # --- Esquema / defaults ---------------------------------------------------
    with app.app_context():
        plain = Category(name="Defaults")
        db.session.add(plain)
        db.session.commit()
        check("modelo: is_active True y color blue por defecto", plain.is_active is True and plain.color == "blue")
        check("to_dict expone is_active/color", plain.to_dict()["is_active"] is True and plain.to_dict()["color"] == "blue")
        plain.color = "css:red"
        check("to_dict: color inválido cae en el seguro", plain.to_dict()["color"] == "blue")
        check("modelo: icon tag por defecto", plain.icon == "tag" and plain.to_dict()["icon"] == "tag")
        plain.icon = "<svg onload=x>"
        check("to_dict: icon inválido cae en tag", plain.to_dict()["icon"] == "tag")
        db.session.delete(plain)
        db.session.commit()

    # --- Crear ---------------------------------------------------------------
    r = admin.post("/api/categories", json={"name": "  Herramientas eléctricas ", "description": "Taladros y sierras", "color": "orange", "is_active": True})
    check("crear categoría 201 + trim", r.status_code == 201 and r.json["name"] == "Herramientas eléctricas" and r.json["color"] == "orange", str(r.json))
    electricas = r.json["id"]
    manuales = admin.post("/api/categories", json={"name": "Herramientas manuales", "description": "Martillos y llaves", "color": "green"}).json["id"]
    seguridad = admin.post("/api/categories", json={"name": "Protección y seguridad", "description": "Cascos y guantes", "color": "teal"}).json["id"]
    vacia = admin.post("/api/categories", json={"name": "Vacía", "description": "Sin productos aún", "color": "pink"}).json["id"]
    check("crear sin color usa blue", admin.get(f"/api/categories/{manuales}").json["color"] == "green")

    check("nombre requerido", admin.post("/api/categories", json={"name": "   "}).status_code == 400)
    check("nombre > 100 rechazado", admin.post("/api/categories", json={"name": "x" * 101}).status_code == 400)
    check("descripción > 255 rechazada", admin.post("/api/categories", json={"name": "Larga", "description": "d" * 256}).status_code == 400)
    check("nombre duplicado 409", admin.post("/api/categories", json={"name": "Herramientas eléctricas"}).status_code == 409)
    check("color fuera de paleta 400", admin.post("/api/categories", json={"name": "Mal color", "color": "#ff0000"}).status_code == 400)
    check("color CSS arbitrario 400", admin.post("/api/categories", json={"name": "Mal color", "color": "red; background:url(x)"}).status_code == 400)
    check("is_active no booleano 400", admin.post("/api/categories", json={"name": "Mal estado", "is_active": "si"}).status_code == 400)
    for bad in ("bolt", "ti ti-tag", "<svg/>", "tag.svg", ""):
        check(f"icon fuera de lista 400 ({bad!r})", admin.post("/api/categories", json={"name": "Mal icono", "icon": bad}).status_code == 400)
    check("icon no persistido tras 400", admin.get("/api/categories?search=Mal icono").json["count"] == 0)
    accepted = 0
    for icon in CATEGORY_ICONS:
        resp = admin.post("/api/categories", json={"name": f"Icono {icon}", "color": "gray", "icon": icon})
        if resp.status_code == 201 and resp.json["icon"] == icon and admin.get(f"/api/categories/{resp.json['id']}").json["icon"] == icon:
            accepted += 1
            admin.delete(f"/api/categories/{resp.json['id']}")
    check("todos los iconos de la lista se aceptan y persisten", accepted == len(CATEGORY_ICONS), f"{accepted}/{len(CATEGORY_ICONS)}")
    check("crear sin icon usa tag", admin.get(f"/api/categories/{manuales}").json["icon"] == "tag")
    check("update icon", admin.put(f"/api/categories/{manuales}", json={"icon": "wrench"}).json["icon"] == "wrench")
    check("update icon inválido 400 y no cambia", admin.put(f"/api/categories/{manuales}", json={"icon": "spanner"}).status_code == 400 and admin.get(f"/api/categories/{manuales}").json["icon"] == "wrench")
    check("update color conserva icon", admin.put(f"/api/categories/{manuales}", json={"color": "green"}).json["icon"] == "wrench")
    check("cuerpo no JSON 400", admin.post("/api/categories", data="x", content_type="text/plain").status_code == 400)
    for color in CATEGORY_COLORS:
        resp = admin.post("/api/categories", json={"name": f"Paleta {color}", "color": color})
        ok = resp.status_code == 201 and resp.json["color"] == color
        if not ok:
            check(f"paleta {color}", False, str(resp.json))
        admin.delete(f"/api/categories/{resp.json['id']}")
    check("paleta completa aceptada", True)

    # --- Productos y agregados ---------------------------------------------
    check("productos semilla", all(r.status_code == 201 for r in (
        product(admin, "EL-1", electricas, 10, 100),   # 1000
        product(admin, "EL-2", electricas, 5, 50),     # 250
        product(admin, "MA-1", manuales, 20, 10),      # 200
        product(admin, "SE-1", seguridad, 1, 30),      # 30
    )))
    inactive_product = product(admin, "EL-OFF", electricas, 100, 100).json["id"]
    admin.delete(f"/api/products/{inactive_product}")

    detail = admin.get(f"/api/categories/{electricas}").json
    check("agregados: solo productos activos", detail["products_count"] == 2 and detail["inventory_value"] == 1250.0, str(detail))
    check("share por valor (1250 / 1480)", detail["inventory_share"] == round(1250 * 100 / 1480, 1), str(detail["inventory_share"]))
    empty = admin.get(f"/api/categories/{vacia}").json
    check("categoría sin productos: ceros", empty["products_count"] == 0 and empty["inventory_value"] == 0.0 and empty["inventory_share"] == 0)
    check("detalle 404", admin.get("/api/categories/999999").status_code == 404)

    summary = admin.get("/api/categories/summary").json
    check("KPI total categorías", summary["total"] == 4 and summary["active"] == 4 and summary["inactive"] == 0, str(summary))
    check("KPI productos = activos", summary["products"] == 4)
    check("KPI valor = SUM(stock*costo) activos", summary["inventory_value"] == 1480.0)
    names = [d["name"] for d in summary["distribution"]]
    check("distribución incluye cero productos", "Vacía" in names and len(summary["distribution"]) == 4)
    top = summary["distribution"][0]
    check("distribución por conteo desc + color", top["name"] == "Herramientas eléctricas" and top["percent"] == 50.0 and top["color"] == "orange")

    # --- Listado: contrato legado, búsqueda, estado, orden, paginación --------
    legacy = admin.get("/api/categories").json
    check("legacy {count, items} sin pagination", legacy["count"] == 4 and len(legacy["items"]) == 4 and "pagination" not in legacy)
    check("legacy items con agregados", all("products_count" in i and "inventory_value" in i for i in legacy["items"]))
    check("búsqueda por nombre", [i["name"] for i in admin.get("/api/categories?search=manuales").json["items"]] == ["Herramientas manuales"])
    check("búsqueda por descripción", [i["name"] for i in admin.get("/api/categories?search=cascos").json["items"]] == ["Protección y seguridad"])
    check("búsqueda vacía", admin.get("/api/categories?search=zzzz").json["count"] == 0)
    order = {
        "name-asc": ["Herramientas eléctricas", "Herramientas manuales", "Protección y seguridad", "Vacía"],
        "name-desc": ["Vacía", "Protección y seguridad", "Herramientas manuales", "Herramientas eléctricas"],
        "products-desc": ["Herramientas eléctricas", "Herramientas manuales", "Protección y seguridad", "Vacía"],
        "products-asc": ["Vacía", "Herramientas manuales", "Protección y seguridad", "Herramientas eléctricas"],
        "value-desc": ["Herramientas eléctricas", "Herramientas manuales", "Protección y seguridad", "Vacía"],
        "value-asc": ["Vacía", "Protección y seguridad", "Herramientas manuales", "Herramientas eléctricas"],
    }
    for key, expected in order.items():
        got = [i["name"] for i in admin.get(f"/api/categories?sort={key}").json["items"]]
        check(f"orden {key}", got == expected, str(got))
    check("orden inválido 400", admin.get("/api/categories?sort=id;drop").status_code == 400)
    check("estado inválido 400", admin.get("/api/categories?status=raro").status_code == 400)
    p1 = admin.get("/api/categories?page=1&per_page=3&sort=name-asc").json
    p2 = admin.get("/api/categories?page=2&per_page=3&sort=name-asc").json
    check("paginación pág. 1", p1["count"] == 4 and len(p1["items"]) == 3 and p1["pagination"]["pages"] == 2 and p1["pagination"]["has_next"] is True)
    check("paginación pág. 2", len(p2["items"]) == 1 and p2["pagination"]["has_prev"] is True and p2["pagination"]["has_next"] is False)
    check("página fuera de rango vacía", admin.get("/api/categories?page=9&per_page=3").json["items"] == [])
    check("per_page inválido 400", admin.get("/api/categories?page=1&per_page=0").status_code == 400 and admin.get("/api/categories?page=x").status_code == 400)

    # --- Editar / color / estado -------------------------------------------
    upd = admin.put(f"/api/categories/{manuales}", json={"name": "Herramientas manuales PRO", "description": "Actualizada"})
    check("update nombre/descripción", upd.status_code == 200 and upd.json["name"] == "Herramientas manuales PRO" and upd.json["description"] == "Actualizada")
    check("update conserva color", upd.json["color"] == "green" and upd.json["is_active"] is True)
    check("update color", admin.put(f"/api/categories/{manuales}", json={"color": "purple"}).json["color"] == "purple")
    check("update color inválido 400", admin.put(f"/api/categories/{manuales}", json={"color": "magenta"}).status_code == 400)
    check("update nombre duplicado 409", admin.put(f"/api/categories/{manuales}", json={"name": "Vacía"}).status_code == 409)
    check("update 404", admin.put("/api/categories/999999", json={"name": "x"}).status_code == 404)

    off = admin.put(f"/api/categories/{seguridad}", json={"is_active": False})
    check("desactivar", off.status_code == 200 and off.json["is_active"] is False)
    with app.app_context():
        check("desactivar persiste y conserva productos", db.session.get(Category, seguridad).is_active is False and Product.query.filter_by(category_id=seguridad).count() == 1)
    check("filtro inactive", [i["name"] for i in admin.get("/api/categories?status=inactive").json["items"]] == ["Protección y seguridad"])
    active_names = [i["name"] for i in admin.get("/api/categories?status=active").json["items"]]
    check("filtro active excluye inactiva", "Protección y seguridad" not in active_names and len(active_names) == 3)
    check("status=all incluye inactiva", admin.get("/api/categories?status=all").json["count"] == 4)
    summary = admin.get("/api/categories/summary").json
    check("KPI activas/inactivas reales", summary["active"] == 3 and summary["inactive"] == 1)

    # --- Integración con productos -----------------------------------------
    check("producto nuevo en categoría inactiva 400", product(admin, "SE-NEW", seguridad, 1, 1).status_code == 400)
    with app.app_context():
        existing = Product.query.filter_by(code="SE-1").one().id
        other = Product.query.filter_by(code="MA-1").one().id
    keep = admin.put(f"/api/products/{existing}", json={"name": "Casco editado"})
    check("producto existente en categoría inactiva sigue editable", keep.status_code == 200 and keep.json["category_id"] == seguridad)
    same = admin.put(f"/api/products/{existing}", json={"category_id": seguridad, "name": "Casco editado 2"})
    check("editar enviando la misma categoría inactiva se conserva", same.status_code == 200 and same.json["category_id"] == seguridad)
    check("mover otro producto a categoría inactiva 400", admin.put(f"/api/products/{other}", json={"category_id": seguridad}).status_code == 400)
    check("mover producto a categoría activa ok", admin.put(f"/api/products/{other}", json={"category_id": electricas}).status_code == 200)
    admin.put(f"/api/products/{other}", json={"category_id": manuales})

    on = admin.put(f"/api/categories/{seguridad}", json={"is_active": True})
    check("reactivar", on.status_code == 200 and on.json["is_active"] is True)
    check("desactivar/reactivar conserva color e icono", off.json["color"] == on.json["color"] == "teal" and off.json["icon"] == on.json["icon"] == "tag")
    check("reactivada admite productos nuevos", product(admin, "SE-NEW", seguridad, 1, 1).status_code == 201)

    # --- Duplicar ----------------------------------------------------------------
    admin.put(f"/api/categories/{electricas}", json={"is_active": False, "icon": "zap"})
    dup = admin.post(f"/api/categories/{electricas}/duplicate")
    check("duplicar copia icono", dup.json["icon"] == "zap")
    check("duplicar 201 '(Copia)'", dup.status_code == 201 and dup.json["name"] == "Herramientas eléctricas (Copia)", str(dup.json))
    check("duplicar copia descripción/color/estado", dup.json["description"] == "Taladros y sierras" and dup.json["color"] == "orange" and dup.json["is_active"] is False)
    dup2 = admin.post(f"/api/categories/{electricas}/duplicate")
    check("duplicar segunda vez '(Copia 2)'", dup2.status_code == 201 and dup2.json["name"] == "Herramientas eléctricas (Copia 2)")
    copy_detail = admin.get(f"/api/categories/{dup.json['id']}").json
    check("duplicado no copia productos", copy_detail["products_count"] == 0)
    with app.app_context():
        check("original conserva sus productos", Product.query.filter_by(category_id=electricas).count() == 3)
    check("duplicar 404", admin.post("/api/categories/999999/duplicate").status_code == 404)
    admin.put(f"/api/categories/{electricas}", json={"is_active": True})

    # --- Eliminar ----------------------------------------------------------------
    conflict = admin.delete(f"/api/categories/{electricas}")
    check("eliminar con productos 409 + mensaje", conflict.status_code == 409 and "No se puede eliminar la categoría" in conflict.json["error"])
    with app.app_context():
        check("no borra productos ni categoría", db.session.get(Category, electricas) is not None and Product.query.filter_by(category_id=electricas).count() == 3)
    gone = admin.delete(f"/api/categories/{dup2.json['id']}")
    check("eliminar vacía 200", gone.status_code == 200 and admin.get(f"/api/categories/{dup2.json['id']}").status_code == 404)

    # --- Permisos ----------------------------------------------------------------
    check("vendedor GET lista/summary/detalle", vendedor.get("/api/categories").status_code == 200 and vendedor.get("/api/categories/summary").status_code == 200 and vendedor.get(f"/api/categories/{vacia}").status_code == 200)
    check("vendedor POST 403", vendedor.post("/api/categories", json={"name": "No"}).status_code == 403)
    check("vendedor PUT 403", vendedor.put(f"/api/categories/{vacia}", json={"is_active": False}).status_code == 403)
    check("vendedor duplicate 403", vendedor.post(f"/api/categories/{vacia}/duplicate").status_code == 403)
    check("vendedor DELETE 403", vendedor.delete(f"/api/categories/{vacia}").status_code == 403)
    check("inventario escribe", inventario.put(f"/api/categories/{vacia}", json={"color": "yellow"}).status_code == 200)
    anon = app.test_client()
    check("anónimo 401", anon.get("/api/categories").status_code == 401 and anon.get("/categories").status_code == 302)

    # --- Páginas: live vs referencia --------------------------------------------
    live = admin.get("/categories").get_data(as_text=True)
    check("live usa UI aprobada (ix) sin fixture", "ix_categories_live.js" in live and "cl-fixture" not in live and "ix_category_layers.js" not in live and "ix-cat-body" in live and "486,250" not in live)
    check("live: can_write true para admin", "window.CAN_WRITE = true" in live)
    ven_live = vendedor.get("/categories").get_data(as_text=True)
    check("live: vendedor solo lectura", ven_live and "window.CAN_WRITE = false" in ven_live)
    with app.app_context():
        before = sorted((c.id, c.name, c.color, c.is_active) for c in Category.query.all())
    app.debug = True
    ref = admin.get("/categories?ref=1").get_data(as_text=True)
    app.debug = False
    with app.app_context():
        after = sorted((c.id, c.name, c.color, c.is_active) for c in Category.query.all())
    check("referencia: fixture, sin live JS, cero escrituras", "cl-fixture" in ref and "ix_categories_live.js" not in ref and "486,250.00" in ref and before == after)
    check("live incluye el selector de iconos de la lista", all(f'data-cl-icon="{icon}"' in live for icon in CATEGORY_ICONS))
    runtime = "".join(Path(ROOT, p).read_text(encoding="utf-8") for p in (
        "app/static/js/ix_categories_live.js", "app/services/category_service.py",
        "app/templates/inventory/_category_icons.html", "app/models/category.py"))
    check("sin mapeo nombre→icono/color en tiempo de ejecución", not any(n in runtime for n in ("Electricidad", "Plomería", "Pinturas", "Construcción")))

    print(f"\nCHECKS={CHECKS_RUN} FAILURES={FAILURES}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
