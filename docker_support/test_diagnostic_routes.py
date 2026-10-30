"""Isolated Flask/HTTP regression. No database connections, dotenv or real data."""
import os
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import unittest
from unittest.mock import MagicMock, patch

from flask import Flask
from flask_login import LoginManager, UserMixin

from app.routes.main import main_bp

PATHS = ("/api/test/products", "/api/test/categories")


class Actor(UserMixin):
    def __init__(self, role):
        self.id, self.role = role, role


def make_app(testing=False, debug=False):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="isolated-test-only", TESTING=testing, DEBUG=debug)
    manager = LoginManager(app)
    manager.user_loader(lambda role: Actor(role))
    app.register_blueprint(main_bp)
    return app


class DiagnosticRoutes(unittest.TestCase):
    def test_absent_in_normal_demo_and_reference(self):
        for debug in (False, True):
            app = make_app(debug=debug)
            self.assertFalse(any(r.rule in PATHS for r in app.url_map.iter_rules()))
            for role in (None, "admin", "inventario", "vendedor", "soporte_ti"):
                client = app.test_client()
                if role:
                    with client.session_transaction() as session:
                        session["_user_id"] = role
                for path in PATHS:
                    self.assertEqual(client.get(path).status_code, 404)
                    self.assertEqual(client.get(path + "?ref=1").status_code, 404)

    def test_testing_routes_obey_canonical_dynamic_permission(self):
        app = make_app(testing=True)
        self.assertTrue(all(any(r.rule == path for r in app.url_map.iter_rules()) for path in PATHS))
        product, category = MagicMock(), MagicMock()
        product.query.order_by.return_value.all.return_value = []
        category.query.order_by.return_value.all.return_value = []
        with patch("app.routes.main.Product", product), patch("app.routes.main.Category", category), \
                patch("app.utils.auth_decorators.role_has_permission") as permission:
            for role in (None, "admin", "inventario", "vendedor", "soporte_ti"):
                client = app.test_client()
                if role:
                    with client.session_transaction() as session:
                        session["_user_id"] = role
                permission.side_effect = lambda slug, code: code == "products:read" and slug != "soporte_ti"
                expected = 401 if role is None else 403 if role == "soporte_ti" else 200
                for path in PATHS:
                    response = client.get(path)
                    self.assertEqual(response.status_code, expected)
                    if role:
                        permission.assert_called_with(role, "products:read")
            # A persisted permission change is authoritative even for admin.
            permission.side_effect = None
            permission.return_value = False
            client = app.test_client()
            with client.session_transaction() as session:
                session["_user_id"] = "admin"
            for path in PATHS:
                self.assertEqual(client.get(path).status_code, 403)
            self.assertEqual(product.query.order_by.call_count, 3)
            self.assertEqual(category.query.order_by.call_count, 3)


if __name__ == "__main__":
    unittest.main()
