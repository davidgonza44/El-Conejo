"""Migration + API regression. Writes ONLY synthetic ferreteria_conejo_e2e.

No dotenv, uploads, cleanup, table recreation or operational database access.
First run seeds known names before migration; subsequent runs verify the marker.
Synthetic rows are retained in the disposable DB for browser inspection.
"""
import os
import secrets
import sys
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql
from sqlalchemy import text

from migrate_category_icon import INITIAL_VISUALS, migrate
from serve_inventory_e2e import E2EConfig
from app import create_app
from app.extensions import db
from app.models import Category, User
from app.models.category import CATEGORY_ICONS

DATABASE = "ferreteria_conejo_e2e"


def migration_checks():
    connection = pymysql.connect(host="127.0.0.1", user="root", password="", database=DATABASE, charset="utf8mb4")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            assert cursor.fetchone()[0] == DATABASE
            cursor.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                           "AND TABLE_NAME='categories' AND COLUMN_NAME='icon'")
            first = cursor.fetchone()[0] == 0
            if first:
                for name, _, _ in INITIAL_VISUALS:
                    cursor.execute("INSERT IGNORE INTO categories (name,color,is_active,created_at) "
                                   "VALUES (%s,'blue',1,NOW())", (name,))
                # Existing customized color must survive the first seed.
                cursor.execute("UPDATE categories SET color='red' WHERE name=%s", ("Pinturas",))
                connection.commit()
            cursor.execute("SELECT id,category_id,current_stock FROM products ORDER BY id")
            products_before = cursor.fetchall()
        initialized = migrate(connection, DATABASE)
        with connection.cursor() as cursor:
            cursor.execute("SELECT COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT FROM information_schema.COLUMNS "
                           "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='categories' AND COLUMN_NAME='icon'")
            kind, nullable, default = cursor.fetchone()
            assert (kind, nullable, default.strip("'")) == ("varchar(30)", "NO", "tag")
            if first:
                assert initialized
                for name, color, icon in INITIAL_VISUALS:
                    cursor.execute("SELECT color,icon FROM categories WHERE name=%s", (name,))
                    assert cursor.fetchone() == (("red", "tag") if name == "Pinturas" else (color, icon))
                print("PASS: real first migration, schema/default, mapping, prior customized color preserved")
            # Simulate later user choices, including returning exactly to defaults.
            custom = (("Construcción", "pink", "shield"), ("Electricidad", "blue", "tag"),
                      ("Ferretería en general", "blue", "hammer"))
            for name, color, icon in custom:
                cursor.execute("UPDATE categories SET color=%s,icon=%s WHERE name=%s", (color, icon, name))
            connection.commit()
            cursor.execute("SELECT id,name,color,icon,is_active FROM categories ORDER BY id")
            before = cursor.fetchall()
        assert migrate(connection, DATABASE) is False
        with connection.cursor() as cursor:
            cursor.execute("SELECT id,name,color,icon,is_active FROM categories ORDER BY id")
            assert cursor.fetchall() == before
            cursor.execute("SELECT id,category_id,current_stock FROM products ORDER BY id")
            assert cursor.fetchall() == products_before
        print("PASS: second migration is a strict no-op; custom color/icon and blue/tag preserved; products untouched")
    finally:
        connection.close()


def api_checks():
    app = create_app(E2EConfig)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    run = secrets.token_hex(5)
    with app.app_context():
        assert db.engine.url.database == DATABASE
        assert db.session.execute(text("SELECT DATABASE()")).scalar() == DATABASE
        user = User(name="Iconos E2E", username="icons_" + run,
                    email="icons_" + run + "@example.invalid", role="admin")
        user.set_password("DisposableInventoryOnly123!")
        db.session.add(user)
        db.session.commit()
        username = user.username
        assert Category(icon="bad").to_dict()["icon"] == "tag"
    client = app.test_client()
    assert client.post("/api/auth/login", json={"identifier": username, "password": "DisposableInventoryOnly123!"}).status_code == 200
    default = client.post("/api/categories", json={"name": "Default icon E2E " + run})
    assert default.status_code == 201 and default.json["icon"] == "tag"
    assert client.delete(f"/api/categories/{default.json['id']}").status_code == 200
    for index, icon in enumerate(CATEGORY_ICONS):
        result = client.post("/api/categories", json={"name": f"Icon E2E {run} {index}", "color": "teal", "icon": icon})
        assert result.status_code == 201 and result.json["icon"] == icon and result.json["color"] == "teal"
    cid = result.json["id"]
    url = f"/api/categories/{cid}"
    unchanged = client.get(url).json
    for value in (None, "", "<svg/>", "ti ti-tag", "url(x)", "unknown", [], {}, 1):
        assert client.put(url, json={"icon": value}).status_code == 400
        assert client.post("/api/categories", json={"name": "Invalid " + run, "icon": value}).status_code == 400
    assert client.put(url, json={"color": "red", "icon": "<svg/>"}).status_code == 400
    assert client.get(url).json == unchanged
    assert client.put(url, json={"color": "bad"}).status_code == 400
    assert client.put(url, json={"color": "purple"}).json["color"] == "purple"
    assert client.put(url, json={"icon": "paintbrush"}).json["icon"] == "paintbrush"
    assert client.put(url, json={"is_active": False}).status_code == 200
    persisted = client.get(url).json
    assert (persisted["color"], persisted["icon"], persisted["is_active"]) == ("purple", "paintbrush", False)
    copied = client.post(url + "/duplicate").json
    for key in ("description", "color", "icon", "is_active"):
        assert copied[key] == persisted[key]
    assert client.get(f"/api/categories/{copied['id']}").json["products_count"] == 0
    assert client.delete(f"/api/categories/{copied['id']}").status_code == 200
    assert client.post("/api/products", json={"code": "IC-" + run, "name": "Synthetic", "category_id": cid}).status_code == 400
    assert client.put(url, json={"is_active": True}).status_code == 200
    product = client.post("/api/products", json={"code": "IC-" + run, "name": "Synthetic", "category_id": cid})
    assert product.status_code == 201
    assert client.put(url, json={"is_active": False}).status_code == 200
    assert client.put(f"/api/products/{product.json['id']}", json={"name": "Synthetic edited", "category_id": cid}).status_code == 200
    assert client.delete(url).status_code == 409
    summary = client.get("/api/categories/summary").json
    assert next(c for c in summary["distribution"] if c["id"] == cid)["color"] == "purple"
    before = client.get("/api/categories").json
    ref = client.get("/categories?ref=1").get_data(as_text=True)
    assert "cl-fixture" in ref and "ix_categories_live.js" not in ref and "data-cl-icon=" not in ref
    assert client.get("/api/categories").json == before
    # Explicit synthetic fixture preparation, separate from migration/runtime mapping.
    for name, color, icon in INITIAL_VISUALS:
        items = client.get("/api/categories", query_string={"search": name}).json["items"]
        known = next(c for c in items if c["name"] == name)
        assert client.put(f"/api/categories/{known['id']}", json={"color": color, "icon": icon}).status_code == 200
    print("PASS: whitelist, invalid API 400, create/edit/reload/status/duplicate, safe delete, products, chart, reference")


def source_checks():
    root = Path(__file__).resolve().parents[1]
    script = (root / "app/static/js/ix_categories_live.js").read_text(encoding="utf-8")
    # The semantic seed names must never become runtime presentation rules.
    for name, _, _ in INITIAL_VISUALS:
        assert name not in script
        assert name not in (root / "app/services/category_service.py").read_text(encoding="utf-8")
    assert "categoryIcon(category.icon)" in script and "tone(item.color)" in script
    app = create_app(E2EConfig)
    with app.app_context():
        module = app.jinja_env.get_template("inventory/_category_icons.html").module
        assert set(module.icons) == set(CATEGORY_ICONS)
        assert len(set(value[1] for value in module.icons.values())) == len(CATEGORY_ICONS)
    print("PASS: local Lucide palette matches API whitelist; no runtime name mapping")


if __name__ == "__main__":
    migration_checks()
    api_checks()
    source_checks()
