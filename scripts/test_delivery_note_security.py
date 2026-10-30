"""Monetary authorization regression: persisted RBAC, disposable SQLite, real PDF bytes.

No live server, database, credentials or historical records are used. Run with:
    python scripts/test_delivery_note_security.py
"""
import base64
import csv
import hashlib
import io
import json
import unittest
import zipfile
from decimal import Decimal

from approval_test_support import IsolatedCase
from flask_login import login_user
from sqlalchemy import func, select

from app.extensions import db
from app.models import DeliveryNote, DeliveryNoteItem, Permission, Product, Role, StockMovement, User
from app.models.purchase import ProductSupplier
from app.services import delivery_note_pdf_service, rbac_service
from test_delivery_note_pdf import extract_pdf_text, pdf_page_count

PRICE = Decimal("3817.43")
COST = Decimal("92541.19")
SUPPLIER_COST = Decimal("74632.87")
CURRENT_PRICE = Decimal("7654.29")
MONEY_KEYS = {
    "unit_price", "line_total", "total_amount", "issued_amount", "cancelled_amount",
    "total_amount_issued_delivery_notes", "average_amount_issued_delivery_notes",
}
REPORTS = (
    "dashboard-summary", "delivery-notes-by-period", "top-delivered-products",
    "delivery-notes-by-user", "delivery-notes-by-customer",
)
# Explicit expected contract, independently of the application's permission lookup.
ACCESS = {
    "admin": (True, True, True), "asistente": (False, True, True),
    "inventario": (True, True, False), "vendedor": (True, False, True),
    "soporte_ti": (False, False, False), "anonimo": (False, False, False),
}
EVIDENCE = []


def monetary_values(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in MONEY_KEYS:
                yield key, item
            yield from monetary_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from monetary_values(item)


def variants(amount):
    return (str(amount), f"{amount:.2f}".replace(".", ","),
            delivery_note_pdf_service._format_money(amount))


class DeliveryNoteSecurity(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.app.config.update(DEMO_WITHDRAW_CUSTOMER_SALES=True)
        support = User(name="Soporte sintético", username="security_support",
                       email="support@example.invalid", role="soporte_ti")
        db.session.add(support)
        self.product.sale_price = PRICE
        self.product.purchase_price = COST
        self.product.current_stock = 100
        db.session.add(ProductSupplier(product_id=self.product.id, supplier_id=self.supplier.id,
                                      purchase_price=SUPPLIER_COST, purchase_price_currency="VES"))
        db.session.commit()
        self.clients["soporte_ti"] = self.client_for(support)
        self.clients["anonimo"] = self.app.test_client()
        self.ids = []
        for index in range(12):
            actor = "admin" if index % 2 else "vendedor"
            response = self.clients[actor].post("/api/delivery-notes", json={
                "customer_name": "Cliente sintético " + str(index),
                "customer_address": "Dirección de prueba áéíóú ñ Ñ",
                "items": [{"product_id": self.product.id, "quantity": index % 3 + 1}],
            })
            self.assertEqual(response.status_code, 201, response.json)
            self.ids.append(response.json["delivery_note"]["id"])
        cancelled = self.clients["admin"].post(f"/api/delivery-notes/{self.ids[-1]}/cancel", json={})
        self.assertEqual(cancelled.status_code, 200)
        self.product.sale_price = CURRENT_PRICE  # Synthetic later catalog change, never historical data.
        db.session.commit()
        self.before = self.snapshot()

    def snapshot(self):
        tables = (DeliveryNote, DeliveryNoteItem, Product, ProductSupplier, StockMovement, Role, Permission)
        result = {model.__tablename__: [tuple(row) for row in db.session.execute(
            select(model.__table__).order_by(model.id))] for model in tables}
        links = db.metadata.tables["role_permissions"]
        result["role_permissions"] = [tuple(row) for row in db.session.execute(
            select(links).order_by(links.c.role_id, links.c.permission_id))]
        return result

    def record(self, role, path, response, **details):
        EVIDENCE.append({"role": role, "surface": path, "status": response.status_code,
                         "sha256": hashlib.sha256(response.data).hexdigest(), **details})

    def assert_cost_absent(self, text):
        for amount in (COST, SUPPLIER_COST):
            for needle in variants(amount):
                self.assertNotIn(needle, text)
        for key in ("purchase_price", "unit_cost", "supplier_cost", "password_hash"):
            self.assertNotIn(key, text)

    def assert_physical_note(self, payload):
        self.assertEqual(payload["items"][0]["product_code"], self.product.code)
        self.assertGreater(payload["items"][0]["quantity"], 0)
        self.assertEqual(payload["total_amount"], None)
        self.assertNotIn("unit_price", payload["items"][0])
        self.assertNotIn("line_total", payload["items"][0])

    def assert_price_absent(self, text):
        total = db.session.scalar(select(func.sum(DeliveryNote.total_amount)).where(
            DeliveryNote.status == "issued"))
        for amount in (PRICE, PRICE * 2, PRICE * 3, CURRENT_PRICE, total):
            for needle in variants(amount):
                self.assertNotIn(needle, text)

    def assert_pdf(self, response, monetary):
        self.assertTrue(response.data.startswith(b"%PDF-"))
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        # Extract the generated streams AND metadata, not the HTML template.
        text = extract_pdf_text(response.data)
        self.assertIn("Cantidad", text)
        self.assertIn("TEST-01", text)
        self.assert_cost_absent(text + response.data.decode("latin-1"))
        for label in ("Precio unitario", "Subtotal", "Total general"):
            self.assertEqual(label in text, monetary)
        self.assertEqual(variants(PRICE)[-1] in text, monetary)
        if not monetary:
            for factor in (1, 2, 3):
                for needle in variants(PRICE * factor):
                    self.assertNotIn(needle, text)
        for needle in variants(CURRENT_PRICE):
            self.assertNotIn(needle, text)
        self.assertNotIn("/EmbeddedFile", response.data.decode("latin-1"))
        self.assertNotIn("/JavaScript", response.data.decode("latin-1"))
        return text

    def check_note_api(self, role, path, document, monetary):
        response = self.clients[role].get(path)
        expected = 401 if role == "anonimo" else 200 if document else 403
        self.assertEqual(response.status_code, expected)
        self.assert_cost_absent(response.get_data(as_text=True))
        if document:
            values = list(monetary_values(response.json))
            self.assertTrue(values)
            self.assertTrue(all((v is not None) == monetary for _, v in values))
            if not monetary:
                self.assert_price_absent(response.get_data(as_text=True))
                if path != "/api/delivery-notes":
                    self.assert_physical_note(response.json)
        self.record(role, path, response, monetary=monetary and document)

    def check_note_pdf(self, role, note_id, document, monetary):
        path = f"/api/delivery-notes/{note_id}/pdf"
        response = self.clients[role].get(path)
        expected = 401 if role == "anonimo" else 200 if document else 403
        self.assertEqual(response.status_code, expected)
        if document:
            text = self.assert_pdf(response, monetary)
            self.assertIn(f"NE-{note_id:06d}", text)
            if note_id == self.ids[-1]:
                self.assertIn("NOTA CANCELADA", text)
        sample = {}
        if document and note_id == self.ids[0] and role in ("admin", "inventario"):
            sample = {"pdf_text": text, "pdf_base64": base64.b64encode(response.data).decode("ascii")}
        self.record(role, path, response, monetary=monetary and document,
                    restricted_values_absent=not monetary, pdf_text_checked=document, **sample)

    def check_note_html(self, role, query, document):
        response = self.clients[role].get("/delivery-notes" + query)
        expected = 302 if role == "anonimo" else 200 if document else 403
        self.assertEqual(response.status_code, expected)
        self.assert_cost_absent(response.get_data(as_text=True))
        self.assert_price_absent(response.get_data(as_text=True))
        self.record(role, "/delivery-notes" + query, response, hidden_amounts_absent=True)

    def test_role_matrix_list_detail_pdf_and_html(self):
        for role, (document, _, monetary) in ACCESS.items():
            paths = ("/api/delivery-notes", *(f"/api/delivery-notes/{i}" for i in self.ids))
            for path in paths:
                with self.subTest(role=role, path=path):
                    self.check_note_api(role, path, document, monetary)
            for note_id in self.ids:
                with self.subTest(role=role, note_id=note_id):
                    self.check_note_pdf(role, note_id, document, monetary)
            for query in ("", f"?detail={self.ids[0]}", f"?detail={self.ids[-1]}"):
                self.check_note_html(role, query, document)
        self.assertEqual(self.snapshot(), self.before)

    def check_report(self, role, path, report, monetary):
        response = self.clients[role].get(path)
        expected = 401 if role == "anonimo" else 200 if report else 403
        self.assertEqual(response.status_code, expected, response.json)
        self.assert_cost_absent(response.get_data(as_text=True))
        if report:
            amounts = list(monetary_values(response.json))
            self.assertTrue(amounts, response.json)
            self.assertTrue(all((v is not None) == monetary for _, v in amounts))
            if not monetary:
                self.assert_price_absent(response.get_data(as_text=True))
            if "items" in response.json:
                self.assertGreater(response.json["count"], 0)
            if monetary and path.endswith("dashboard-summary"):
                total = db.session.scalar(select(func.sum(DeliveryNote.total_amount)).where(
                    DeliveryNote.status == "issued"))
                self.assertEqual(response.json["total_amount_issued_delivery_notes"], float(total))
        self.record(role, path, response, monetary=report and monetary,
                    monetary_fields=dict(monetary_values(response.json)))

    def test_report_matrix_each_endpoint_and_filter(self):
        for role, (_, report, monetary) in ACCESS.items():
            for route in REPORTS:
                for query in ("", "?date_from=2000-01-01&date_to=2099-12-31&limit=1"):
                    path = "/api/reports/" + route + query
                    with self.subTest(role=role, path=path):
                        self.check_report(role, path, report, monetary)
        self.assertEqual(self.snapshot(), self.before)

    def test_no_id_query_or_format_bypass_and_missing_exports(self):
        for role, (document, _, monetary) in ACCESS.items():
            for suffix in ("", "/pdf"):
                path = "/api/delivery-notes/2147483647" + suffix
                response = self.clients[role].get(path)
                self.assertEqual(response.status_code, 401 if role == "anonimo" else 404 if document else 403)
            for format_name in ("csv", "xlsx", "pdf"):
                path = f"/api/delivery-notes/{self.ids[0]}?format={format_name}&include_prices=true&role=admin"
                response = self.clients[role].get(path)
                self.assertEqual(response.status_code, 401 if role == "anonimo" else 200 if document else 403)
                if document and not monetary:
                    self.assert_physical_note(response.json)
            for path in ("/api/delivery-notes/export?format=csv", "/api/delivery-notes/export?format=xlsx",
                         f"/delivery-notes/{self.ids[0]}/print"):
                response = self.clients[role].get(path)
                self.assertEqual(response.status_code, 404)
                self.record(role, path, response, implemented=False)

    def movement_export_text(self, response, format_name):
        if format_name == "xlsx":
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                return "\n".join(archive.read(p).decode("utf-8") for p in archive.namelist() if p.endswith(".xml"))
        if format_name == "pdf":
            return extract_pdf_text(response.data)
        text = response.data.decode("utf-8-sig")
        self.assertGreater(len(list(csv.reader(io.StringIO(text)))), 1)
        return text

    def check_movement_export(self, role, format_name):
        path = "/api/inventory/movements/export?format=" + format_name
        response = self.clients[role].get(path)
        allowed = role in ("admin", "asistente", "inventario")
        self.assertEqual(response.status_code, 401 if role == "anonimo" else 200 if allowed else 403)
        if allowed:
            text = self.movement_export_text(response, format_name)
            self.assertIn("NE-", text)
            self.assert_cost_absent(text)
            self.assert_price_absent(text)
        self.record(role, path, response, document_amounts_absent=True)

    def check_sales_export(self, role, format_name):
        # This source aggregates Sale/SaleLine, never DeliveryNote/DeliveryNoteItem.
        path = "/api/reports/sales/export?tab=summary&format=" + format_name
        response = self.clients[role].get(path)
        allowed = ACCESS[role][1]
        self.assertEqual(response.status_code, 401 if role == "anonimo" else 200 if allowed else 403)
        text = extract_pdf_text(response.data) if response.mimetype == "application/pdf" else response.get_data(as_text=True)
        self.assert_cost_absent(text)
        self.assert_price_absent(text)
        self.record(role, path, response, document_amounts_absent=True)

    def check_related_page(self, role, path):
        response = self.clients[role].get(path)
        allowed = ACCESS[role][1] if path == "/reports/sales" else role in ("admin", "asistente", "inventario")
        success = 302 if path == "/reports" else 200
        expected = 302 if role == "anonimo" else success if allowed else 403
        self.assertEqual(response.status_code, expected)
        self.assert_cost_absent(response.get_data(as_text=True))
        self.assert_price_absent(response.get_data(as_text=True))
        self.record(role, path, response, embedded_document_amounts_absent=True)

    def test_related_exports_printing_and_javascript(self):
        for role in ACCESS:
            for format_name in ("csv", "xlsx", "pdf"):
                self.check_movement_export(role, format_name)
            for format_name in ("csv", "pdf"):
                self.check_sales_export(role, format_name)
            for path in ("/inventory/print", "/dashboard", "/reports", "/reports/sales"):
                self.check_related_page(role, path)
        response = self.clients["anonimo"].get("/static/js/delivery_notes.js")
        self.assertEqual(response.status_code, 200)
        self.assert_price_absent(response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), self.before)

    def check_physical_json(self, role, path, allowed):
        response = self.clients[role].get(path)
        self.assertEqual(response.status_code, 401 if role == "anonimo" else 200 if allowed else 403)
        text = response.get_data(as_text=True)
        if role not in ("admin", "asistente"):
            self.assert_cost_absent(text)
        if role == "inventario":
            self.assert_price_absent(text)
        self.record(role, path, response, restricted_fields_checked=True)

    def test_related_inventory_and_product_json(self):
        movement_id = db.session.get(DeliveryNote, self.ids[0]).items[0].issue_stock_movement_id
        inventory_paths = ("/api/inventory/movements", f"/api/inventory/movements/{movement_id}",
                           "/api/inventory/movements/summary",
                           f"/api/inventory/products/{self.product.id}/movements")
        for role in ACCESS:
            for path in inventory_paths:
                self.check_physical_json(role, path, role in ("admin", "asistente", "inventario"))
            for path in ("/api/products", f"/api/products/{self.product.id}"):
                self.check_physical_json(role, path, role in ("admin", "asistente", "inventario", "vendedor"))
        self.assertEqual(self.snapshot(), self.before)

    def test_effective_rbac_revocation_not_role_name_or_client_flags(self):
        role = Role.query.filter_by(slug="vendedor").one()
        price = Permission.query.filter_by(code="products:view_price").one()
        role.permissions.remove(price)  # Disposable fixture only; production permissions untouched.
        db.session.commit()
        rbac_service.invalidate_cache()
        response = self.clients["vendedor"].get(f"/api/delivery-notes/{self.ids[0]}")
        self.assertEqual(response.status_code, 200)
        self.assert_physical_note(response.json)
        self.assert_pdf(self.clients["vendedor"].get(f"/api/delivery-notes/{self.ids[0]}/pdf?include_prices=1"), False)
        role.permissions.remove(Permission.query.filter_by(code="delivery_notes:read").one())
        db.session.commit()
        for note_id in (*self.ids, 2147483647):
            for suffix in ("", "/pdf"):
                self.assertEqual(self.clients["vendedor"].get(f"/api/delivery-notes/{note_id}{suffix}").status_code, 403)
        # A cost grant never substitutes a price grant; a report grant never substitutes document access.
        role = Role.query.filter_by(slug="asistente").one()
        role.permissions.remove(price)
        db.session.commit()
        for route in REPORTS:
            response = self.clients["asistente"].get("/api/reports/" + route)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(all(v is None for _, v in monetary_values(response.json)))
        self.assertEqual(self.clients["asistente"].get(f"/api/delivery-notes/{self.ids[0]}/pdf").status_code, 403)

    def test_physical_rankings_do_not_reveal_monetary_order(self):
        # Two one-note customers: lexicographic order contradicts monetary ranking.
        first, second = (db.session.get(DeliveryNote, i) for i in self.ids[:2])
        first.customer_name, first.total_amount = "AA físico", Decimal("1.01")
        second.customer_name, second.total_amount = "ZZ monetario", Decimal("99999.98")
        db.session.commit()
        physical = self.clients["inventario"].get("/api/reports/delivery-notes-by-customer?limit=1")
        monetary = self.clients["admin"].get("/api/reports/delivery-notes-by-customer?limit=1")
        self.assertEqual(physical.json["items"][0]["customer_name"], "AA físico")
        self.assertIsNone(physical.json["items"][0]["total_amount"])
        self.assertEqual(monetary.json["items"][0]["customer_name"], "ZZ monetario")

    def test_pdf_multipage_physical_and_historical_projection(self):
        note = db.session.get(DeliveryNote, self.ids[0])
        for index in range(45):
            db.session.add(DeliveryNoteItem(delivery_note=note, product_id=self.product.id,
                product_code=f"MANY-{index}", product_name="Producto sintético áéíóú ñ Ñ " + "largo " * 15,
                quantity=2, unit_price=PRICE, line_total=PRICE * 2))
        db.session.commit()
        response = self.clients["inventario"].get(f"/api/delivery-notes/{note.id}/pdf")
        self.assertGreater(pdf_page_count(response.data), 1)
        text = self.assert_pdf(response, False)
        self.assertIn("MANY-44", text)
        self.assertIn("No constituye factura", text)
        with self.app.test_request_context():
            login_user(self.users["inventario"])
            self.assert_pdf(self.clients["inventario"].get(f"/api/delivery-notes/{note.id}/pdf"), False)
            direct = delivery_note_pdf_service.render_delivery_note_pdf(note)
            self.assertNotIn(variants(PRICE)[-1], extract_pdf_text(direct))

    def test_write_responses_stock_validation_and_no_extra_permissions(self):
        inventory = Role.query.filter_by(slug="inventario").one()
        self.assertNotIn("products:view_price", inventory.permission_codes)
        self.assertNotIn("products:view_cost", inventory.permission_codes)
        response = self.clients["vendedor"].post("/api/delivery-notes", json={
            "customer_name": "Nuevo sintético", "items": [{"product_id": self.product.id, "quantity": 2}]})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["delivery_note"]["items"][0]["unit_price"], float(CURRENT_PRICE))
        self.assert_cost_absent(response.get_data(as_text=True))
        note_id = response.json["delivery_note"]["id"]
        cancelled = self.clients["admin"].post(f"/api/delivery-notes/{note_id}/cancel", json={})
        self.assertEqual(cancelled.status_code, 200)
        self.assert_cost_absent(cancelled.get_data(as_text=True))
        before = self.snapshot()
        for role in ("inventario", "asistente", "soporte_ti"):
            self.assertEqual(self.clients[role].post("/api/delivery-notes", json={}).status_code, 403)
        invalid = self.clients["vendedor"].post("/api/delivery-notes", json={
            "customer_name": "No debe existir", "items": [{"product_id": self.product.id, "quantity": 9999}]})
        self.assertEqual(invalid.status_code, 409)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DeliveryNoteSecurity)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("SECURITY_EVIDENCE=" + json.dumps(EVIDENCE, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result.wasSuccessful() else 1)
