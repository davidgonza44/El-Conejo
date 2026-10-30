"""Central inventory UI regression checks; synthetic in-memory SQLite only.

Optional --serve exposes this disposable fixture on loopback for browser checks.
No dotenv, operational DB, uploads, factory session hooks or external providers.
"""
import os
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import re
import sys
from flask import Flask
from flask_login import current_user
from flask_wtf.csrf import generate_csrf
from sqlalchemy import event

from test_movement_detail import ROOT, db, login_manager, fixtures, snapshot, reset_cache
from app.routes.inventory import inventory_bp
from app.routes.pages import pages_bp
from app.routes.products import products_bp
from app.routes.reconciliation import reconciliation_bp
from app.services.exceptions import ApiError
from app.services.movement_export_service import movement_values
from app.models import StockMovement
from app.utils.navigation import home_path, nav_allowed, settings_tab_allowed, visible_links
from app.utils.permissions import role_has_permission


def make_app():
    app = Flask(__name__, template_folder=str(ROOT / "app/templates"), static_folder=str(ROOT / "app/static"))
    app.config.update(TESTING=True, DEBUG=True, SECRET_KEY="isolated-movement-ui",
                      SQLALCHEMY_DATABASE_URI="sqlite://", SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    login_manager.init_app(app)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(reconciliation_bp)
    # Only navigation URL resolution; unrelated modules are not run in this harness.
    app.add_url_rule("/purchases", endpoint="purchases.page", view_func=lambda: ("Out of test scope", 404))
    app.add_url_rule("/purchases/receptions", endpoint="purchases.receptions", view_func=lambda: ("Out of test scope", 404))
    app.add_template_global(lambda: None, "get_company_logo_url")
    app.add_template_global(generate_csrf, "csrf_token")
    app.add_template_global(lambda permission: role_has_permission(current_user.role, permission), "has_permission")
    app.add_template_global(lambda key, reference=False: nav_allowed(current_user.role, key, reference is True), "nav_allowed")
    app.add_template_global(lambda children, reference=False: visible_links(current_user.role, children, reference is True), "visible_nav")
    app.add_template_global(lambda key, reference=False: settings_tab_allowed(current_user.role, key, reference is True), "settings_tab_allowed")
    app.add_template_global(lambda: home_path(current_user.role) if getattr(current_user, "role", None) else "/login", "home_path")
    app.register_error_handler(ApiError, lambda error: ({"error": error.message}, error.status_code))
    with app.app_context():
        reset_cache()
        assert db.engine.url.drivername == "sqlite" and db.engine.url.database in (None, "")
        db.create_all()
        user_id, ids = fixtures()

    @login_manager.request_loader
    def synthetic_user(_request):
        from app.models import User
        return db.session.get(User, user_id)

    return app, ids


def run(app, ids):
    client = app.test_client()
    with app.app_context():
        before = snapshot()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.lstrip().split()[0].upper())

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            for url in ("/inventory", "/inventory?ref=1"):
                response = client.get(url)
                assert response.status_code == 200
                html = response.get_data(as_text=True)
                module = html[html.index("Registro de entradas, salidas y ajustes de inventario"):]
                assert "Transferencia" not in module and "Sucursal" not in module
                assert "Movimientos registrados" in module and "Historial total" in module
                assert module.count('class="ix-card ix-kpi"') == 4
                assert len(re.findall(r'<th scope="col"', module)) == 10
                assert module.count('class="iv-type-name"') == 3
                assert "mv-branch" not in module and "mv-filter-branch" not in module
                if "ref=1" not in url:
                    assert "Sucursal" not in html and "mv-location" not in html
            unfiltered = client.get("/api/inventory/movements?page=1&per_page=1").json
            assert unfiltered["total"] == 6 and unfiltered["count"] == 1
            filtered = client.get("/api/inventory/movements?page=1&movement_type=ajuste").json
            assert filtered["total"] == 1
            summary = client.get("/api/inventory/movements/summary").json
            assert summary["total"] == 6
            assert summary["distribution"] == {"entrada": 2, "salida": 3, "ajuste": 1}
            for movement_id in ids:
                row = client.get(f"/api/inventory/movements/{movement_id}").json
                assert row["presentation"]["status_label"] == "Aplicado"
            expected = dict(zip(ids, ("OC-000350 · REC-00331", "VTA-000042", *(f"MOV-{key:06d}" for key in ids[2:]))))
            for query in ("?page=1&per_page=100", ""):
                listed = client.get("/api/inventory/movements" + query).json["items"]
                for row in listed:
                    detail = client.get(f"/api/inventory/movements/{row['id']}").json
                    assert row["display_reference"] == detail["display_reference"] == expected[row["id"]]
                    assert row["display_status"] == detail["display_status"] == "Aplicado"
                    assert row["presentation"] == detail["presentation"]
            # Warm schema checks above; query count must not grow with page size.
            counts = []
            for size in (1, 6):
                start = len(statements)
                client.get(f"/api/inventory/movements?page=1&per_page={size}")
                counts.append(statements[start:].count("SELECT"))
            assert counts[0] == counts[1], counts
            from app.services.movement_export_service import movement_rows
            exported, _, _ = movement_rows({"sort": "created_at"})
            assert [row["reference"] for row in exported] == [expected[key] or "—" for key in ids]
            assert all(row["display_status"] == "Aplicado" for row in exported)
            product_id = db.session.get(StockMovement, ids[0]).product_id
            product_rows = client.get(f"/api/inventory/products/{product_id}/movements").json["items"]
            assert len(product_rows) == len(ids)
            for row in product_rows:
                assert row["display_reference"] == expected[row["id"]]
                assert row["display_status"] == "Aplicado"
            print(f"PASS: list/detail/export reference and status parity; SELECT counts for 1/6 rows: {counts}.")
            # A detached historical type is presented neutrally, never persisted or reclassified.
            from types import SimpleNamespace
            movement = db.session.get(StockMovement, ids[0])
            historical = SimpleNamespace(**{key: getattr(movement, key) for key in (
                "id", "product", "user", "created_at", "quantity", "previous_stock", "new_stock", "reason")},
                movement_type="transferencia")
            assert movement_values(historical)["movement_type"] == "Tipo no soportado"
            for format_name in (("csv", "xlsx") if "--no-pdf" in sys.argv else ("csv", "xlsx", "pdf")):
                response = client.get("/api/inventory/movements/export?format=" + format_name)
                assert response.status_code == 200
                if format_name == "csv":
                    assert "Sucursal" not in response.get_data(as_text=True)
            response = client.get("/inventory/print")
            assert response.status_code == 200 and "Sucursal" not in response.get_data(as_text=True)
            assert not ({"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"} & set(statements))
            assert snapshot() == before
            print("PASS: live/reference page, 4 KPIs, 3 chart categories, 10 columns, real totals, details, selected export formats/print, zero SQL writes and unchanged all-table snapshot.", flush=True)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)


if __name__ == "__main__":
    app, ids = make_app()
    run(app, ids)
    if "--serve" in sys.argv:
        app.run(host="127.0.0.1", port=5037, use_reloader=False, use_debugger=False)
    else:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
            reset_cache()
