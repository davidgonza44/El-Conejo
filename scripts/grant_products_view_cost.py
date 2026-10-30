#!/usr/bin/env python3
"""Concede solo products:view_cost en ferreteria_conejo_tesis.

No llama a seed_rbac() ni altera esquema. Aborta sin escribir si faltan
--database ferreteria_conejo_tesis, --apply y --authorize-thesis.
El destino sale de esos argumentos, no de DB_NAME.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

THESIS = "ferreteria_conejo_tesis"
DUMP_PROGRAM = Path(r"C:\xampp\mysql\bin\mysqldump.exe")
BACKUP_DIR = ROOT / "instance" / "backups"
SYSTEM_ROLES = ("admin", "inventario", "vendedor", "soporte_ti")
INVARIANT_TABLES = (
    "products", "stock_movements", "sales", "purchase_orders",
    "purchase_receptions", "reconciliation_runs", "reconciliation_lines",
)
IDEMPOTENT_WARNING = 1062


def abort(message: str) -> None:
    print(f"ABORT: {message}")
    raise SystemExit(2)


def parse_args():
    parser = argparse.ArgumentParser(description="Concede únicamente products:view_cost.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--authorize-thesis", action="store_true")
    return parser.parse_args()


def authorize(args) -> None:
    if args.database != THESIS or not args.apply or not args.authorize_thesis:
        abort("Se requieren a la vez --database ferreteria_conejo_tesis --apply --authorize-thesis.")
    os.environ["DB_NAME"] = THESIS


def connect(write: bool):
    import pymysql
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    host = os.environ.get("DB_HOST", "127.0.0.1")
    if host not in ("localhost", "127.0.0.1", "::1"):
        abort("El host de la base no es local; no se continúa.")
    conn = pymysql.connect(
        host=host,
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", ""),
        database=THESIS,
        charset="utf8mb4",
        autocommit=False,
    )
    cur = conn.cursor()
    if not write:
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        cur.execute("START TRANSACTION READ ONLY")
    else:
        cur.execute("START TRANSACTION")
    cur.execute("SELECT DATABASE()")
    current = cur.fetchone()[0]
    if current != THESIS:
        conn.rollback()
        conn.close()
        abort(f"SELECT DATABASE() devolvió {current!r}, no {THESIS}.")
    return conn, cur


def fetch(cur, sql, args=None):
    cur.execute(sql, args or ())
    return cur.fetchall()


def role_sets(cur) -> dict[str, set[str]]:
    rows = fetch(cur, """
        SELECT r.slug, p.code
        FROM roles r
        LEFT JOIN role_permissions rp ON rp.role_id = r.id
        LEFT JOIN permissions p ON p.id = rp.permission_id
        ORDER BY r.slug, p.code
    """)
    found: dict[str, set[str]] = {}
    for slug, code in rows:
        found.setdefault(slug, set())
        if code:
            found[slug].add(code)
    return found


def expected_pre():
    from app.utils.permissions import PRODUCTS_VIEW_COST, ROLE_PERMISSIONS
    return {
        "admin": set(ROLE_PERMISSIONS["admin"]) - {PRODUCTS_VIEW_COST},
        "inventario": set(ROLE_PERMISSIONS["inventario"]) - {PRODUCTS_VIEW_COST},
        "vendedor": set(ROLE_PERMISSIONS["vendedor"]),
        "soporte_ti": set(ROLE_PERMISSIONS["soporte_ti"]),
    }


def counts(cur) -> dict[str, int]:
    out = {}
    for table in ("permissions", "role_permissions", "roles"):
        out[table] = fetch(cur, f"SELECT COUNT(*) FROM {table}")[0][0]
    return out


def invariants(cur) -> dict[str, str]:
    present = {row[0] for row in fetch(cur, """
        SELECT TABLE_NAME FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN %s
    """, (INVARIANT_TABLES,))}
    missing = [name for name in INVARIANT_TABLES if name not in present]
    if missing:
        abort("Faltan tablas para los invariantes: " + ", ".join(missing))
    out = {}
    for table in INVARIANT_TABLES:
        if table == "products":
            total, stock = fetch(cur, "SELECT COUNT(*), COALESCE(SUM(current_stock), 0) FROM products")[0]
            out["products"] = str(total)
            out["products.current_stock_sum"] = str(stock)
        else:
            out[table] = str(fetch(cur, f"SELECT COUNT(*) FROM {table}")[0][0])
    return out


def baseline(cur) -> tuple[dict, dict[str, set[str]], dict]:
    from app.utils.permissions import PRODUCTS_VIEW_COST
    current_counts = counts(cur)
    roles = role_sets(cur)
    view_rows = fetch(cur, "SELECT id FROM permissions WHERE code=%s", (PRODUCTS_VIEW_COST,))
    custom = [slug for slug in roles if slug not in SYSTEM_ROLES]
    if current_counts != {"permissions": 37, "role_permissions": 63, "roles": 4}:
        abort(f"El conteo PRE cambió respecto al preflight: {current_counts}.")
    if view_rows:
        abort("products:view_cost ya existe; no se concede de nuevo.")
    if custom or set(roles) != set(SYSTEM_ROLES):
        abort(f"Los roles ya no son exactamente los cuatro de sistema: {sorted(roles)}.")
    expected = expected_pre()
    for slug, codes in expected.items():
        if roles.get(slug) != codes:
            missing = sorted(codes - roles.get(slug, set()))
            extra = sorted(roles.get(slug, set()) - codes)
            abort(f"Permisos PRE de {slug} distintos al preflight. faltan={missing} sobran={extra}.")
    return current_counts, roles, invariants(cur)


def dump_database() -> dict:
    if not DUMP_PROGRAM.is_file():
        abort(f"No está mysqldump en {DUMP_PROGRAM}.")
    version = subprocess.run([str(DUMP_PROGRAM), "--version"], capture_output=True, text=True, check=False)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = BACKUP_DIR / f"{THESIS}_view_cost_{stamp}.sql"
    if target.exists():
        abort(f"El respaldo ya existe y no se sobrescribe: {target}")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    host = os.environ.get("DB_HOST", "127.0.0.1")
    env = os.environ.copy()
    env["MYSQL_PWD"] = os.environ.get("DB_PASSWORD", "")
    env["DB_NAME"] = THESIS
    args = [str(DUMP_PROGRAM), "--no-defaults", "--protocol=TCP",
            f"--host={host}", f"--port={os.environ.get('DB_PORT', '3306')}",
            f"--user={os.environ.get('DB_USER', 'root')}",
            "--single-transaction", "--quick", "--routines", "--triggers", "--events",
            "--hex-blob", "--default-character-set=utf8mb4", "--databases", THESIS]
    with target.open("xb") as output:
        result = subprocess.run(args, stdout=output, stderr=subprocess.PIPE, env=env, check=False, timeout=600)
    size = target.stat().st_size if target.exists() else 0
    if result.returncode != 0 or size == 0:
        target.unlink(missing_ok=True)
        abort(f"El respaldo falló o quedó vacío (exit {result.returncode}).")
    digest = hashlib.sha256()
    has_permissions = False
    with target.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            if b"CREATE TABLE `permissions`" in chunk:
                has_permissions = True
    if not has_permissions:
        abort("El respaldo no contiene la tabla permissions; no se continúa.")
    info = {"path": str(target), "size": size, "sha256": digest.hexdigest(),
            "tool": str(DUMP_PROGRAM), "version": (version.stdout or version.stderr).strip(),
            "exit_code": result.returncode}
    print("BACKUP", info["path"])
    print("BACKUP_SIZE", info["size"])
    print("BACKUP_SHA256", info["sha256"])
    print("BACKUP_TOOL", info["tool"])
    print("BACKUP_VERSION", info["version"])
    print("BACKUP_EXIT", info["exit_code"])
    return info


def warnings_of(cur):
    cur.execute("SHOW WARNINGS")
    return list(cur.fetchall())


def apply_grant(cur) -> list[dict]:
    from migrate_settings_full import view_cost_statements
    results = []
    for name, sql, params in view_cost_statements():
        cur.execute(sql, params)
        rowcount = cur.rowcount
        warnings = warnings_of(cur)
        codes = [item[1] for item in warnings]
        results.append({"name": name, "rowcount": rowcount, "warnings": warnings, "sql": sql})
        unexpected = [code for code in codes if code != IDEMPOTENT_WARNING]
        if unexpected or (codes and rowcount != 0) or (not codes and rowcount != 1):
            return results
    return results


def grant_ok(results) -> bool:
    if len(results) != 3:
        return False
    return all(item["rowcount"] == 1 and not item["warnings"] for item in results)


def print_sets(title: str, sets: dict[str, set[str]]) -> None:
    print(title)
    for slug in SYSTEM_ROLES:
        print(f"  {slug} ({len(sets.get(slug, ()))})")
        for code in sorted(sets.get(slug, ())):
            print(f"    {code}")


def smoke(pre_sets: dict[str, set[str]]) -> None:
    from urllib.parse import quote_plus
    from flask_login import login_user
    from app.config import Config
    from app.services.exceptions import NotFoundError

    class ThesisConfig(Config):
        DB_NAME = THESIS
        SQLALCHEMY_DATABASE_URI = (
            f"mysql+pymysql://{os.environ.get('DB_USER', 'root')}:{quote_plus(os.environ.get('DB_PASSWORD', ''))}"
            f"@{os.environ.get('DB_HOST', '127.0.0.1')}:{os.environ.get('DB_PORT', '3306')}"
            f"/{THESIS}?charset=utf8mb4"
        )

    from app import create_app
    from app.extensions import db
    from app.models import User
    app = create_app(ThesisConfig)

    def user_for(role):
        return User.query.filter_by(role=role, is_active=True).order_by(User.id).first()

    def invoke(role, endpoint, path, *args):
        user = user_for(role)
        if user is None:
            abort(f"No hay usuario activo para el rol {role}.")
        with app.test_request_context(path):
            login_user(user)
            try:
                result = app.view_functions[endpoint](*args)
            except NotFoundError as exc:
                db.session.rollback()
                return exc.status_code, {"error": exc.message}, exc.message
            if isinstance(result, tuple):
                response, status = result
            else:
                response, status = result, getattr(result, "status_code", 200)
            if isinstance(response, str):
                body, data = response, None
            elif getattr(response, "direct_passthrough", False):
                body = b"".join(response.response).decode("utf-8", "replace")
                data = None
            else:
                body = response.get_data(as_text=True)
                data = response.get_json(silent=True)
            db.session.rollback()
            return status, data, body

    with app.app_context():
        if db.engine.url.database != THESIS:
            abort(f"La aplicación no apunta a {THESIS}.")
        product_id = db.session.execute(db.text(
            "SELECT p.id FROM products p JOIN product_suppliers ps ON ps.product_id = p.id "
            "WHERE p.is_active = 1 ORDER BY p.id LIMIT 1")).scalar()
        if product_id is None:
            product_id = db.session.execute(db.text(
                "SELECT id FROM products WHERE is_active = 1 ORDER BY id LIMIT 1")).scalar()
        sale_price = db.session.execute(db.text(
            "SELECT sale_price FROM products WHERE id = :id"), {"id": product_id}).scalar() if product_id else None
        vendor = User.query.filter_by(role="vendedor", is_active=True).order_by(User.id).first()
        foreign_sale = db.session.execute(db.text(
            "SELECT sale_number FROM sales WHERE seller_id <> :seller ORDER BY id LIMIT 1"),
            {"seller": vendor.id}).scalar() if vendor else None
        own_sale = db.session.execute(db.text(
            "SELECT sale_number FROM sales WHERE seller_id = :seller ORDER BY id LIMIT 1"),
            {"seller": vendor.id}).scalar() if vendor else None
        category_id = db.session.execute(db.text("SELECT id FROM categories ORDER BY id LIMIT 1")).scalar()

        status, detail, _ = invoke("vendedor", "products.get_product", f"/api/products/{product_id}", product_id)
        listed = invoke("vendedor", "products.list_products", "/api/products")
        suppliers = invoke("vendedor", "products.list_product_suppliers", f"/api/products/{product_id}/suppliers", product_id)
        categories = invoke("vendedor", "categories.list_categories", "/api/categories?sort=value-desc")
        by_name = invoke("vendedor", "categories.list_categories", "/api/categories?sort=name-asc")
        one = invoke("vendedor", "categories.get_category", f"/api/categories/{category_id}", category_id)
        summary = invoke("vendedor", "categories.categories_summary", "/api/categories/summary")
        exported = invoke("vendedor", "categories.export_categories", "/api/categories/export?format=csv&include_value=1&sort=value-desc")
        purchases = invoke("vendedor", "purchases.listing", "/api/purchases")
        page = invoke("vendedor", "pages.products", "/products")
        cat_page = invoke("vendedor", "pages.categories", "/categories")
        admin = invoke("admin", "products.get_product", f"/api/products/{product_id}", product_id)
        inv_product = invoke("inventario", "products.get_product", f"/api/products/{product_id}", product_id)
        inv_suppliers = invoke("inventario", "products.list_product_suppliers", f"/api/products/{product_id}/suppliers", product_id)
        inv_summary = invoke("inventario", "categories.categories_summary", "/api/categories/summary")
        soporte_user = user_for("soporte_ti")
        if soporte_user is not None:
            soporte = invoke("soporte_ti", "products.list_products", "/api/products")
            soporte_buy = invoke("soporte_ti", "purchases.listing", "/api/purchases")
            soporte_sales = invoke("soporte_ti", "sales.list_sales", "/api/sales")
            soporte_http = soporte[0] == 403 and soporte_buy[0] == 403 and soporte_sales[0] == 403
        else:
            from app.utils.permissions import role_has_permission
            soporte_http = not any(role_has_permission("soporte_ti", code) for code in (
                "products:read", "products:view_cost", "purchases:read", "sales:read"))
            print("SMOKE soporte sin usuario activo; se verificó el permiso del rol")

        own_status = None
        if own_sale:
            own_status, own_body, _ = invoke("vendedor", "sales.get_sale", f"/api/sales/{own_sale}", own_sale)
        foreign_status = None
        if foreign_sale:
            foreign_status, _, _ = invoke("vendedor", "sales.get_sale", f"/api/sales/{foreign_sale}", foreign_sale)

        vendor_names = [item["name"] for item in categories[1]["items"]]
        name_names = [item["name"] for item in by_name[1]["items"]]
        hidden = ("purchase_price", "inventory_value", "inventory_share", "unit_cost")
        vendor_blobs = [detail, listed[1], suppliers[1], categories[1], one[1], summary[1]]
        leaked = [key for blob in vendor_blobs for key in hidden if key in str(blob)]
        supplier_costs = []
        if suppliers[1] and suppliers[1].get("items"):
            supplier_costs = [row for row in suppliers[1]["items"] if "purchase_price" in row]
        checks = {
            "vendedor detalle con precio y sin costo": status == 200 and detail.get("sale_price") == float(sale_price) and "purchase_price" not in detail,
            "vendedor proveedores sin costo": not supplier_costs,
            "vendedor categorias sin valor": "inventory_value" not in str(categories[1]) and "inventory_share" not in str(one[1]) and "inventory_value" not in str(summary[1]),
            "vendedor orden por valor igual a nombre": vendor_names == name_names,
            "vendedor sin claves de costo": not leaked,
            "vendedor export sin valor": "Valor en inventario" not in exported[2],
            "vendedor compras 403": purchases[0] == 403,
            "vendedor html sin costo": "Costo de referencia" not in page[2] and "Valor en inventario" not in cat_page[2] and "Precio de venta" in page[2],
            "admin ve costo de referencia": admin[0] == 200 and "purchase_price" in admin[1],
            "inventario ve costo de referencia": inv_product[0] == 200 and "purchase_price" in inv_product[1],
            "inventario ve costo de proveedor": (not inv_suppliers[1].get("items")) or all("purchase_price" in row for row in inv_suppliers[1]["items"]),
            "inventario ve valor de inventario": "inventory_value" in inv_summary[1],
            "soporte sin products ni compras ni ventas": soporte_http,
            "soporte sin el permiso nuevo": "products:view_cost" not in pre_sets["soporte_ti"],
        }
        if own_status is not None:
            checks["vendedor venta propia con unit_price"] = own_status == 200 and "unit_price" in str(own_body) and "purchase_price" not in str(own_body)
        if foreign_status is not None:
            checks["vendedor venta ajena 404"] = foreign_status == 404
        for label, ok in checks.items():
            print("SMOKE", "OK" if ok else "FALLO", label)
        if not all(checks.values()):
            abort("El smoke test falló.")
        db.session.rollback()


def main() -> int:
    args = parse_args()
    authorize(args)
    dump_database()
    read_conn, read_cur = connect(False)
    try:
        pre_counts, pre_sets, pre_invariants = baseline(read_cur)
    finally:
        read_conn.rollback()
        read_conn.close()
    print("PRE", pre_counts)
    print_sets("PRE_ROLES", pre_sets)
    print("PRE_INVARIANTS", pre_invariants)

    from migrate_settings_full import view_cost_statements
    from app.utils.permissions import PRODUCTS_VIEW_COST, permission_label
    write_conn, write_cur = connect(True)
    results = []
    try:
        again, _, _ = baseline(write_cur)
        if again != pre_counts:
            abort(f"El baseline cambió dentro de la transacción: {again}.")
        results = apply_grant(write_cur)
        for item in results:
            print("EXEC", item["name"], "rowcount", item["rowcount"], "warnings", item["warnings"])
        if not grant_ok(results):
            write_conn.rollback()
            print("ROLLBACK")
            abort("La concesión no tuvo el rowcount 1 sin warnings esperado.")
        module, label = fetch(write_cur, "SELECT module, label FROM permissions WHERE code=%s", (PRODUCTS_VIEW_COST,))[0]
        if module != "products" or label != permission_label(PRODUCTS_VIEW_COST):
            write_conn.rollback()
            print("ROLLBACK")
            abort(f"El permiso quedó como {module!r} / {label!r}.")
        write_conn.commit()
        print("COMMIT")
    except SystemExit:
        raise
    except Exception:
        write_conn.rollback()
        print("ROLLBACK")
        raise
    finally:
        write_conn.close()

    post_conn, post_cur = connect(False)
    try:
        post_counts = counts(post_cur)
        post_sets = role_sets(post_cur)
        post_invariants = invariants(post_cur)
        view = fetch(post_cur, "SELECT module, label FROM permissions WHERE code=%s", (PRODUCTS_VIEW_COST,))
        assigned = [row[0] for row in fetch(post_cur, """
            SELECT r.slug FROM role_permissions rp
            JOIN roles r ON r.id = rp.role_id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE p.code = %s ORDER BY r.slug
        """, (PRODUCTS_VIEW_COST,))]
    finally:
        post_conn.rollback()
        post_conn.close()
    print("POST", post_counts)
    print("POST_PERMISSION", view)
    print("POST_ASSIGNED", assigned)
    print_sets("POST_ROLES", post_sets)
    print("POST_INVARIANTS", post_invariants)
    print("STATEMENTS", len(view_cost_statements()))
    if post_counts != {"permissions": 38, "role_permissions": 65, "roles": 4}:
        abort(f"Conteos POST inesperados: {post_counts}.")
    if view != (("products", permission_label(PRODUCTS_VIEW_COST)),) or assigned != ["admin", "inventario"]:
        abort("La asignación POST no es exactamente admin e inventario.")
    for slug in ("vendedor", "soporte_ti"):
        if post_sets[slug] != pre_sets[slug]:
            abort(f"{slug} cambió.")
    for slug in ("admin", "inventario"):
        if post_sets[slug] != pre_sets[slug] | {PRODUCTS_VIEW_COST}:
            abort(f"{slug} no quedó como PRE más products:view_cost.")
    if post_invariants != pre_invariants:
        abort(f"Los invariantes cambiaron: PRE {pre_invariants} POST {post_invariants}.")
    smoke(pre_sets)
    print("READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
