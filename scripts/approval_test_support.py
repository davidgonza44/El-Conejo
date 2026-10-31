"""Disposable in-memory SQLite fixture; no dotenv, MySQL, files or external delivery."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import BigInteger, event
from sqlalchemy.ext.compiler import compiles
from app import create_app
from app.extensions import db
from app.models import Category, Product, User
from app.models.purchase import ProductSupplier, Supplier
from app.services import rbac_service, session_service
from app.utils.schema import reset_cache


@compiles(BigInteger, "sqlite")
def sqlite_bigint(_type, _compiler, **_kwargs):
    return "INTEGER"


class IsolatedConfig:
    TESTING = True
    SECRET_KEY = "disposable-approval-fixture"
    APP_ENV = "testing"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    MAIL_ENABLED = False
    NOTIFICATIONS_DRY_RUN = True


class IsolatedCase(unittest.TestCase):
    def setUp(self):
        self.mysql_guard = patch("pymysql.connect", side_effect=AssertionError("MySQL forbidden in SQLite suite"))
        self.mysql_guard.start()
        self.addCleanup(self.mysql_guard.stop)
        self.delivery_guard = patch("app.services.notification_providers.send", side_effect=AssertionError("External delivery forbidden"))
        self.delivery_guard.start()
        self.addCleanup(self.delivery_guard.stop)
        self.app = create_app(IsolatedConfig)
        folder = tempfile.TemporaryDirectory(prefix="conejo-approval-fixture-")
        self.addCleanup(folder.cleanup)
        self.app.instance_path = folder.name
        self.app.logger.disabled = True
        # Tests hold an app context for ORM assertions; real requests each have their own g.
        from flask import g

        def fresh_request_cache():
            g.pop("_login_user", None)
            rbac_service.invalidate_cache()

        self.app.before_request_funcs[None].insert(0, fresh_request_cache)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.engine.dispose)
        self.addCleanup(db.session.remove)
        reset_cache()

        @event.listens_for(db.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        db.create_all()
        rbac_service.ensure_seeded()
        users = [User(name=role, username=role, email=role + "@example.invalid", role=role)
                 for role in ("admin", "inventario", "asistente", "vendedor")]
        users.append(User(name="Segundo administrador", username="admin2", email="admin2@example.invalid", role="admin"))
        category = Category(name="Pruebas aisladas")
        db.session.add_all([*users, category])
        db.session.flush()
        self.product = Product(code="TEST-01", name="Producto sintético", category_id=category.id,
                               current_stock=10, minimum_stock=0, purchase_price=2, sale_price=3,
                               purchase_price_currency="USD", sale_price_currency="USD", is_active=True)
        self.supplier = Supplier(name="Proveedor sintético", tax_id="TEST-SUPPLIER")
        db.session.add_all([self.product, self.supplier])
        db.session.commit()
        self.users = {user.username: user for user in users}
        self.clients = {name: self.client_for(user) for name, user in self.users.items()}
        rbac_service.invalidate_cache()

    def client_for(self, user):
        from flask import g, session
        from flask_wtf.csrf import generate_csrf
        client = self.app.test_client()
        with self.app.test_request_context():
            session_service.open_session(user)
            g.pop('csrf_token', None)
            csrf = generate_csrf()
            cookies = dict(session)
        with client.session_transaction() as session:
            session.update(cookies)
        client.environ_base['HTTP_X_CSRFTOKEN'] = csrf
        return client

    def stock(self):
        db.session.expire_all()
        return db.session.get(Product, self.product.id).current_stock


class LinkedSupplierCase(IsolatedCase):
    """Fixture de compras: asociaciones explícitas antes de cotizar."""
    def setUp(self):
        super().setUp()
        self.associate(self.product, self.supplier)

    def associate(self, product, supplier):
        link = ProductSupplier(product_id=product.id, supplier_id=supplier.id,
                               purchase_price=2, purchase_price_currency="USD", is_active=True, is_preferred=False)
        db.session.add(link)
        db.session.commit()
        return link


def accredit_run(app, run_id, source="legacy_inventory"):
    """Fixture sintético: convierte una conciliación confirmada en corte automático de una fuente acreditada.

    Solo para pruebas aisladas. En producción la acreditación se configura con
    RECONCILIATION_ACCREDITED_SOURCES y el corte lo captura el servidor.
    """
    from datetime import datetime, timedelta
    from app.models.reconciliation import ReconciliationCut, ReconciliationRun

    app.config["RECONCILIATION_ACCREDITED_SOURCES"] = (source,)
    run = db.session.get(ReconciliationRun, run_id)
    run.source_type = "automatic"
    snapshot = datetime.utcnow() - timedelta(hours=1)
    db.session.add(ReconciliationCut(source=source, scheduled_for=snapshot, status="completed",
                                     snapshot_status="captured", local_snapshot_at=snapshot, run_id=run.id))
    db.session.commit()
    return run
