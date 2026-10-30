"""Regresiones de filtros SR, HTML y cinco roles en SQLite desechable.

No accede a dotenv, MySQL, puertos ni almacenamiento de la empresa.
"""
import unittest
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import Product, User
from app.models.purchase import PurchaseOrder
from app.models.replenishment_request import ReplenishmentRequest, ReplenishmentRequestLine


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def find(self, tag, **attrs):
        return [node for kind, node in self.elements if kind == tag
                and all(node.get(key) == value for key, value in attrs.items())]


class ReplenishmentFilterTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        support = User(name="Soporte aislado", username="soporte", email="soporte@example.invalid", role="soporte_ti")
        self.cement = Product(code="CEM-01", name="Cemento blanco", category_id=self.product.category_id,
                              current_stock=10, minimum_stock=0, purchase_price=2, sale_price=3)
        db.session.add_all([support, self.cement])
        db.session.commit()
        self.clients["soporte_ti"] = self.client_for(support)
        self.rows = [
            self.seed("open", "2026-01-09T12:00:00", self.cement),
            self.seed("open", "2026-01-10T00:00:00"),
            self.seed("open", "2026-01-11T00:00:00"),
            self.seed("processing", "2026-01-10T23:59:59.999999", self.cement),
            self.seed("processing", "2026-01-12T12:00:00"),
            self.seed("fulfilled", "2026-01-10T12:00:00"),
            self.seed("cancelled", "2026-01-10T12:00:00"),
        ]
        db.session.commit()

    def seed(self, status, local_time, product=None, creator=None):
        product = product or self.product
        created = datetime.fromisoformat(local_time).replace(tzinfo=ZoneInfo("America/Caracas"))
        row = ReplenishmentRequest(status=status, origin="manual",
                                   created_by_user_id=(creator or self.users["inventario"]).id,
                                   created_at=created.astimezone(timezone.utc).replace(tzinfo=None))
        row.lines = [ReplenishmentRequestLine(product_id=product.id, product_code=product.code,
                                             product_name=product.name, unit=product.unit,
                                             quantity=1, stock_on_hand=10)]
        db.session.add(row)
        db.session.flush()
        row.request_number = f"SR-{row.id:06d}"
        return row

    def listing(self, role="inventario", **filters):
        response = self.clients[role].get("/api/replenishment-requests", query_string=filters)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def assert_rows(self, indices, **filters):
        body = self.listing(**filters)
        self.assertEqual(body["pagination"]["total"], len(indices))
        self.assertEqual({item["id"] for item in body["items"]}, {self.rows[i].id for i in indices})
        return body

    def test_three_open_and_processing_filter_returns_zero(self):
        for row in self.rows[3:]:
            db.session.delete(row)
        db.session.commit()
        self.assertEqual(self.listing()["pagination"]["total"], 3)
        body = self.listing(status="processing")
        self.assertEqual(body["items"], [])
        self.assertEqual(body["pagination"]["total"], 0)
        html = self.clients["inventario"].get("/purchases/requests?status=processing").get_data(as_text=True)
        self.assertIn("No hay solicitudes con estos filtros.", html)
        self.assertIn("Mostrando 0 a 0 de 0", html)
        self.assertNotIn('class="sr-number-cell"', html)

    def test_two_processing_and_all_valid_states(self):
        for status, indices in (("", range(7)), ("open", [0, 1, 2]), ("processing", [3, 4]),
                                ("fulfilled", [5]), ("cancelled", [6])):
            with self.subTest(status=status):
                body = self.assert_rows(indices, status=status)
                if status:
                    self.assertTrue(all(item["status"] == status for item in body["items"]))

    def test_visual_labels_are_not_accepted_as_internal_values(self):
        for status in ("En proceso", "Abierta", "converted", "unknown"):
            with self.subTest(status=status):
                result = self.clients["inventario"].get("/api/replenishment-requests", query_string={"status": status})
                self.assertEqual(result.status_code, 400)
                self.assertNotIn("items", result.get_json())

    def test_search_code_and_product_combine_with_status(self):
        self.assert_rows([3], status="processing", search="Cemento")
        self.assert_rows([3], status="processing", search="CEM-01")
        self.assert_rows([4], status="processing", search=self.rows[4].request_number)
        self.assert_rows([], status="processing", search=self.rows[0].request_number)
        self.assert_rows([], status="processing", search="Cemento%")

    def test_start_is_inclusive_at_local_midnight(self):
        self.assert_rows([1, 2, 3, 4, 5, 6], start="2026-01-10")

    def test_end_is_inclusive_through_last_microsecond(self):
        self.assert_rows([0, 1, 3, 5, 6], end="2026-01-10")

    def test_range_and_all_filters_combine(self):
        self.assert_rows([1, 3, 5, 6], start="2026-01-10", end="2026-01-10")
        self.assert_rows([3], start="2026-01-10", end="2026-01-10", status="processing", search="Cemento")

    def test_invalid_range_and_dates_render_validation_without_fallback_records(self):
        for filters in ({"start": "2026-01-12", "end": "2026-01-10", "status": "processing"},
                        {"start": "2026-02-30"}, {"end": "not-a-date"}, {"end": "9999-12-31"}):
            for path in ("/api/replenishment-requests", "/purchases/requests"):
                with self.subTest(filters=filters, path=path):
                    response = self.clients["inventario"].get(path, query_string=filters)
                    self.assertEqual(response.status_code, 400)
                    if path.startswith("/api"):
                        self.assertIn("error", response.get_json())
                    else:
                        html = response.get_data(as_text=True)
                        self.assertIn("Corrija los filtros", html)
                        self.assertIn('id="sr-server-error"', html)
                        self.assertNotIn('class="sr-number-cell"', html)

    def test_filters_pagination_and_refresh_preserve_query(self):
        for i in range(10):
            self.seed("processing", "2026-01-10T16:00:00", self.cement)
        db.session.commit()
        filters = {"status": "processing", "search": "Cemento", "start": "2026-01-10",
                   "end": "2026-01-10", "per_page": "8"}
        first = self.listing(**filters)
        second = self.listing(**filters, page=2)
        self.assertEqual(first["pagination"]["total"], 11)
        self.assertEqual(len(first["items"]), 8)
        self.assertEqual(len(second["items"]), 3)
        self.assertTrue({r["id"] for r in first["items"]}.isdisjoint({r["id"] for r in second["items"]}))
        response = self.clients["inventario"].get("/purchases/requests", query_string=filters)
        page = Page(response.get_data(as_text=True))
        next_url = page.find("a", **{"aria-label": "Página siguiente"})[0]["href"]
        query = parse_qs(urlsplit(next_url).query)
        self.assertEqual({key: value[0] for key, value in query.items()}, {**filters, "page": "2"})
        for _ in range(2):
            html = self.clients["inventario"].get(next_url).get_data(as_text=True)
            controls = Page(html)
            self.assertEqual(controls.find("input", name="search")[0]["value"], "Cemento")
            self.assertIn('value="processing" selected', html)
            self.assertIn("Mostrando 9 a 11 de 11 solicitudes", html)
        clamped = self.listing(**filters, page=999)
        self.assertEqual(clamped["pagination"]["page"], 2)
        self.assert_rows([], status="cancelled", search="Cemento", page=2)

    def test_clear_filters_resets_query_and_page(self):
        html = self.clients["inventario"].get(
            "/purchases/requests?status=processing&search=Cemento&start=2026-01-10&end=2026-01-10&page=2").get_data(as_text=True)
        link = Page(html).find("a", **{"class": "po-clear"})[0]["href"]
        self.assertEqual(link, "/purchases/requests")
        cleared = self.clients["inventario"].get(link).get_data(as_text=True)
        self.assertIn("Mostrando 1 a 7 de 7 solicitudes", cleared)
        for name in ("search", "start", "end"):
            self.assertEqual(Page(cleared).find("input", name=name)[0]["value"], "")

    def test_visible_labels_submit_fallback_and_script_are_present(self):
        html = self.clients["inventario"].get("/purchases/requests").get_data(as_text=True)
        page = Page(html)
        for control_id in ("sr-search", "sr-status", "sr-start-date", "sr-end-date"):
            self.assertTrue(page.find("label", **{"for": control_id}))
        button = page.find("button", type="submit")[0]
        self.assertEqual(button["class"], "po-btn is-primary")
        self.assertTrue(page.find("script", src="/static/js/ix_replenishment_requests.js"))
        self.assertIn('title="Ver detalle de SR-', html)

    def test_five_roles_scope_creator_column_and_detail_permissions(self):
        foreign = self.seed("processing", "2026-01-10T12:00:00", creator=self.users["asistente"])
        db.session.commit()
        for role in ("admin", "asistente", "inventario", "vendedor", "soporte_ti"):
            allowed = role in ("admin", "asistente", "inventario")
            expected = 200 if allowed else 403
            with self.subTest(role=role):
                for path in ("/api/replenishment-requests", "/purchases/requests"):
                    self.assertEqual(self.clients[role].get(path + "?status=processing").status_code, expected)
                for path in (f"/api/replenishment-requests/{self.rows[3].id}",
                             f"/purchases/requests/{self.rows[3].id}"):
                    self.assertEqual(self.clients[role].get(path).status_code, expected)
                if allowed:
                    body = self.listing(role=role, status="processing")
                    self.assertEqual(body["pagination"]["total"], 2 if role == "inventario" else 3)
                    html = self.clients[role].get("/purchases/requests").get_data(as_text=True)
                    self.assertEqual('<th scope="col">Creada por</th>' in html, role != "inventario")
                    detail = self.clients[role].get(f"/purchases/requests/{self.rows[3].id}").get_data(as_text=True)
                    self.assertEqual('id="sr-quote"' in detail, role == "asistente")
        for prefix in ("/api/replenishment-requests", "/purchases/requests"):
            self.assertEqual(self.clients["inventario"].get(f"{prefix}/{foreign.id}").status_code, 404)

    def test_fulfilled_badge_and_linked_order_current_status(self):
        order = PurchaseOrder(order_number="OC-000001", supplier_id=self.supplier.id,
                              created_by_user_id=self.users["asistente"].id, status="pending_approval", currency="VES")
        db.session.add(order)
        db.session.flush()
        self.rows[5].purchase_order_id = order.id
        db.session.commit()
        listing = self.clients["inventario"].get("/purchases/requests?status=fulfilled").get_data(as_text=True)
        self.assertIn("Convertida en OC", listing)
        for status, label in (("pending_approval", "Pendiente de aprobación"), ("approved", "Aprobada / pendiente de recepción")):
            order.status = status
            db.session.commit()
            detail = self.clients["inventario"].get(f"/purchases/requests/{self.rows[5].id}").get_data(as_text=True)
            self.assertIn("OC-000001", detail)
            self.assertIn(f" · {label}", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
