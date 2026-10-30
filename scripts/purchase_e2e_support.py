"""Shared disposable MariaDB setup. Refuses every SQL write outside the named E2E DB."""
import os
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask_login import login_user
from sqlalchemy import event, text, inspect
from app import create_app
from app.extensions import db
from app.models import Category, Product, User, UserSession, Role, Permission
from app.services import purchase_service, supplier_quote_service, notification_providers, session_service, rbac_service
from app.models.purchase import ProductSupplier, Supplier
from migrate_purchase_orders import engine_for, migrate
from migrate_purchase_approval_reconciliation import migrate as migrate_approval

DATABASE = "ferreteria_conejo_e2e"


def guard_engine(engine, writes=None):
    @event.listens_for(engine, "before_cursor_execute")
    def guard(connection, cursor, statement, parameters, context, executemany):
        operation = statement.lstrip().split(None, 1)[0].upper()
        if operation in ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE", "TRUNCATE"):
            cursor.execute("SELECT DATABASE()")
            if cursor.fetchone()[0] != DATABASE:
                raise RuntimeError("E2E write guard: forbidden database.")
            if writes is not None:
                writes.append(operation)


def make_app(seed_orders=False, reuse=False):
    engine = engine_for(DATABASE)
    guard_engine(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT DATABASE()")).scalar() == DATABASE
    with engine.connect() as connection:
        existing = inspect(connection).has_table("purchase_orders")
    if existing:
        migrate_approval(engine, DATABASE)
    migrate(engine, DATABASE)
    migrate_approval(engine, DATABASE)
    uri = engine.url.render_as_string(hide_password=False)
    engine.dispose()
    # The shortage link column is read by every request query since the 5 Oct 2026 corrections.
    import migrate_replenishment_source_order as source_order
    engine = engine_for(DATABASE)
    guard_engine(engine)
    with engine.connect() as connection:
        ready = inspect(connection).has_table("replenishment_requests")
    if ready:
        source_order.migrate(engine, DATABASE)
    engine.dispose()

    class E2EConfig:
        SECRET_KEY = secrets.token_urlsafe(32)
        DEBUG = True
        TESTING = True
        APP_ENV = "testing"
        MAIL_ENABLED = False
        SQLALCHEMY_DATABASE_URI = uri
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        SQLALCHEMY_ENGINE_OPTIONS = {"pool_size": 8}

    app = create_app(E2EConfig)
    writes = []
    with app.app_context():
        assert db.engine.url.database == DATABASE
        assert db.session.execute(text("SELECT DATABASE()")).scalar() == DATABASE
        assert db.session.execute(text("SELECT @@tx_isolation")).scalar() == "REPEATABLE-READ"
        guard_engine(db.engine, writes)
        # Safety boundary: no external provider may send anything from the disposable harness.
        def deny_external(*args, **kwargs):
            raise notification_providers.ProviderError("External delivery disabled in disposable purchase E2E.")
        notification_providers.send = deny_external
        if reuse:
            previous = User.query.filter(User.username.like("po_e2e_%")).order_by(User.id.desc()).first()
            if previous:
                run = previous.username.removeprefix("po_e2e_")
                reader = User.query.filter_by(username="po_reader_" + run).one()
                denied = User.query.filter_by(username="po_denied_" + run).one()
                asistente = User.query.filter_by(username="po_asistente_" + run).first()
                inventario = User.query.filter_by(username="po_inventario_" + run).first()
                if asistente is None or inventario is None:
                    previous = None
                else:
                    from app.models.purchase import PurchaseOrder
                    context = {"run": run, "database": DATABASE, "admin_id": previous.id, "reader_id": reader.id,
                    "denied_id": denied.id, "approver_id": User.query.filter_by(username="po_approver_" + run).one().id,
                    "asistente_id": asistente.id, "inventario_id": inventario.id,
                    "role_id": Role.query.filter_by(slug=reader.role).one().id,
                    "category_id": Category.query.filter_by(name="Purchase E2E " + run).one().id,
                    "product_ids": [row.id for row in Product.query.filter(Product.code.like("PO-E2E-" + run + "-%")).order_by(Product.id).all()],
                    "supplier_ids": [row.id for row in Supplier.query.filter(Supplier.tax_id.like("E2E-" + run + "-%")).order_by(Supplier.id).all()],
                    "order_ids": [row.id for row in PurchaseOrder.query.filter_by(created_by_user_id=asistente.id).order_by(PurchaseOrder.id).all()]}
                    return app, context, writes
        run = secrets.token_hex(4)
        rbac_service.ensure_seeded()
        from migrate_tutor_cycle import apply_changes
        with db.engine.begin() as connection:
            apply_changes(connection)
        source_engine = engine_for(DATABASE)
        guard_engine(source_engine)
        try:
            source_order.migrate(source_engine, DATABASE)
        finally:
            source_engine.dispose()
        rbac_service.invalidate_cache()
        # Migration preceding an empty RBAC seed must still give admin the newly catalogued permissions.
        admin_role = Role.query.filter_by(slug="admin").one()
        missing = [permission for permission in Permission.query.filter(Permission.code.like("purchases:%")).all() if permission not in admin_role.permissions]
        admin_role.permissions.extend(missing)
        role = Role(slug="po_" + run, name="PO reader " + run, is_active=True,
                    permissions=Permission.query.filter(Permission.code == "purchases:read").all())
        admin = User(name="Operador de compras E2E", username="po_e2e_" + run, email=f"po_{run}@example.invalid", role="admin", password_changed_at=datetime.utcnow())
        approver = User(name="Aprobador E2E", username="po_approver_" + run, email=f"approver_{run}@example.invalid", role="admin", password_changed_at=datetime.utcnow())
        reader = User(name="Lector de compras E2E", username="po_reader_" + run, email=f"po_reader_{run}@example.invalid", role=role.slug)
        denied = User(name="Sin compras E2E", username="po_denied_" + run, email=f"po_denied_{run}@example.invalid", role="vendedor")
        asistente = User(name="Asistente E2E", username="po_asistente_" + run, email=f"asistente_{run}@example.invalid", role="asistente", password_changed_at=datetime.utcnow())
        inventario = User(name="Inventario E2E", username="po_inventario_" + run, email=f"inventario_{run}@example.invalid", role="inventario", password_changed_at=datetime.utcnow())
        category = Category(name="Purchase E2E " + run)
        db.session.add_all([role, admin, approver, reader, denied, asistente, inventario, category])
        db.session.flush()
        products = [Product(code=f"PO-E2E-{run}-{i}", name=name, category_id=category.id, current_stock=20,
                            minimum_stock=0, purchase_price="25.00", sale_price="35.00", is_active=i != 3)
                    for i, name in enumerate(("Cemento gris E2E 42,5 kg", "Cabilla de acero E2E", "Pintura blanca E2E", "Producto inactivo E2E"))]
        suppliers = [Supplier(name=f"E2E {name} {run}", tax_id=f"E2E-{run}-{i}", contact_name="Contacto sintético", phone="0412-5550100", email="compras@example.invalid", is_active=i != 3)
                     for i, name in enumerate(("Materiales del Centro", "Ferretería Industrial", "Suministros del Norte", "Proveedor inactivo"))]
        db.session.add_all([*products, *suppliers])
        db.session.flush()
        # Quotes require an active product-supplier association; the seed quotes product i with supplier i.
        db.session.add_all([ProductSupplier(product_id=products[i].id, supplier_id=suppliers[i].id, purchase_price="25.00",
                                            purchase_price_currency="VES", is_active=True, is_preferred=False)
                            for i in range(3)])
        db.session.commit()
        context = {"run": run, "database": DATABASE, "admin_id": admin.id, "approver_id": approver.id, "reader_id": reader.id, "denied_id": denied.id,
                   "asistente_id": asistente.id, "inventario_id": inventario.id,
                   "role_id": role.id, "category_id": category.id, "product_ids": [row.id for row in products],
                   "supplier_ids": [row.id for row in suppliers], "order_ids": []}
        if seed_orders:
            # Desde el 5 oct 2026 toda orden nace de una solicitud en proceso: inventario la registra,
            # el asistente la procesa y cotiza, y la orden se prepara con ese vínculo.
            from app.services import replenishment_request_service
            with app.test_request_context():
                today = datetime.now(purchase_service.inventory_service.COMMERCIAL_ZONE).date()
                for i in range(12):
                    quantity = (i % 3 + 1) * 4
                    login_user(inventario)
                    request_row = replenishment_request_service.create(
                        {"items": [{"product_id": products[i % 3].id, "quantity": quantity}],
                         "notes": "Solicitud sintética exclusiva de ferreteria_conejo_e2e."})
                    login_user(asistente)
                    replenishment_request_service.start(request_row.id)
                    quote = supplier_quote_service.create({
                        "supplier_id": suppliers[i % 3].id, "issued_on": today.isoformat(), "currency": "VES",
                        "valid_until": "2099-01-01",
                        "items": [{"product_id": products[i % 3].id, "quantity": quantity, "unit_price": str(250 + i * 20)}]})
                    order = purchase_service.create_order({"supplier_id": suppliers[i % 3].id, "supplier_quote_id": quote.id,
                        "replenishment_request_id": request_row.id,
                        "estimated_delivery_date": (today + timedelta(days=i % 5 + 1)).isoformat(),
                        "notes": "Orden sintética exclusiva de ferreteria_conejo_e2e.",
                        "items": [{"product_id": products[i % 3].id, "quantity": quantity}]})
                    context["order_ids"].append(order.id)
                    login_user(approver)
                    purchase_service.decide_order(order.id, {}, approve=True)
                    if i % 3:
                        login_user(inventario)
                        complete = i % 3 == 2
                        receipt = {"received_at": today.isoformat(),
                                   "items": [{"line_id": order.lines[0].id,
                                              "quantity": order.lines[0].quantity_ordered if complete else 2}]}
                        if not complete:
                            receipt.update(confirm_partial_closure=True,
                                           closure_reason="Proveedor sintético sin existencia del resto.")
                        purchase_service.receive_order(order.id, receipt)
    return app, context, writes


def client_for(app, user_id):
    client = app.test_client()
    with app.test_request_context():
        user = db.session.get(User, user_id)
        session_service.open_session(user)
        from flask import session
        cookies = dict(session)
    with client.session_transaction() as session:
        session.update(cookies)
    return client


def cleanup(app, context):
    """Only this unique run's rows; no unrelated E2E or valuable rows are selected."""
    with app.app_context():
        assert db.session.execute(text("SELECT DATABASE()")).scalar() == DATABASE
        users = [context[key] for key in ("admin_id", "reader_id", "denied_id", "approver_id", "asistente_id", "inventario_id")]
        statements = [
            "DELETE FROM purchase_reception_lines WHERE reception_id IN (SELECT id FROM purchase_receptions WHERE received_by_user_id=:uid)",
            "DELETE FROM purchase_receptions WHERE received_by_user_id=:uid OR purchase_order_id IN (SELECT id FROM purchase_orders WHERE created_by_user_id=:uid)",
            "DELETE FROM purchase_closure_requests WHERE requested_by_user_id=:uid OR purchase_order_id IN (SELECT id FROM purchase_orders WHERE created_by_user_id=:uid)",
            "DELETE FROM purchase_order_lines WHERE purchase_order_id IN (SELECT id FROM purchase_orders WHERE created_by_user_id=:uid)",
            "DELETE FROM purchase_orders WHERE created_by_user_id=:uid",
            "DELETE FROM supplier_quote_lines WHERE quote_id IN (SELECT id FROM supplier_quotes WHERE created_by_user_id=:uid)",
            "DELETE FROM supplier_quotes WHERE created_by_user_id=:uid",
            "DELETE FROM replenishment_request_lines WHERE request_id IN (SELECT id FROM replenishment_requests WHERE created_by_user_id=:uid)",
            "DELETE FROM replenishment_requests WHERE created_by_user_id=:uid",
            "DELETE FROM notification_deliveries WHERE notification_id IN (SELECT id FROM notifications WHERE user_id=:uid)",
            "DELETE FROM notifications WHERE user_id=:uid",
            "DELETE FROM stock_movements WHERE user_id=:uid",
            "DELETE FROM user_sessions WHERE user_id=:uid",
        ]
        # Orders and requests reference each other; unlink this run's rows before deleting either side.
        for uid in users:
            db.session.execute(text("UPDATE replenishment_requests SET purchase_order_id=NULL, source_purchase_order_id=NULL "
                                    "WHERE created_by_user_id=:uid"), {"uid": uid})
            db.session.execute(text("UPDATE purchase_orders SET replenishment_request_id=NULL WHERE created_by_user_id=:uid"),
                               {"uid": uid})
        for uid in users:
            for statement in statements:
                db.session.execute(text(statement), {"uid": uid})
        for product_id in context["product_ids"]:
            db.session.execute(text("DELETE FROM product_suppliers WHERE product_id=:id"), {"id": product_id})
            db.session.execute(text("DELETE FROM products WHERE id=:id"), {"id": product_id})
        for supplier_id in context["supplier_ids"]:
            db.session.execute(text("DELETE FROM suppliers WHERE id=:id"), {"id": supplier_id})
        for uid in users:
            db.session.execute(text("DELETE FROM users WHERE id=:id"), {"id": uid})
        db.session.execute(text("DELETE FROM categories WHERE id=:id"), {"id": context["category_id"]})
        db.session.execute(text("DELETE FROM role_permissions WHERE role_id=:id"), {"id": context["role_id"]})
        db.session.execute(text("DELETE FROM roles WHERE id=:id"), {"id": context["role_id"]})
        db.session.commit()
