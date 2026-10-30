"""Correcciones del tutor (5 oct 2026): borrador, recepción parcial, faltante, vías directas y cotización.

SQLite en memoria (approval_test_support). No abre MySQL, el puerto 5001 ni la base de tesis.
Las fixtures siguen el flujo completo: solicitud, procesamiento, cotización, preparación y aprobación.
La concurrencia real se prueba aparte en MariaDB desechable (test_purchase_tutor_concurrency.py).
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from approval_test_support import LinkedSupplierCase
from app.extensions import db
from app.models import Product, StockMovement, User
from app.models.purchase import PurchaseOrder, PurchaseReception, Supplier
from app.models.replenishment_request import ReplenishmentRequest
from app.models.role import Permission, Role
from app.models.supplier_quote import SupplierQuote
from app.services import inventory_service, rbac_service, replenishment_service


def caracas_today():
    return datetime.now(ZoneInfo("America/Caracas")).date()


class TutorFlowCase(LinkedSupplierCase):
    def setUp(self):
        super().setUp()
        self.today = caracas_today().isoformat()

    def request(self, items=None, client="inventario"):
        items = items or [(self.product.id, 10)]
        response = self.clients[client].post("/api/replenishment-requests", json={
            "items": [{"product_id": product_id, "quantity": quantity} for product_id, quantity in items]})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def start(self, row):
        response = self.clients["asistente"].post(f"/api/replenishment-requests/{row['id']}/start", json={})
        self.assertEqual(response.status_code, 200, response.get_json())

    def quote(self, row, price="10.00", currency="VES", issued_on=None, valid_until="2099-01-01"):
        response = self.clients["asistente"].post(f"/api/replenishment-requests/{row['id']}/quotes", json={
            "supplier_id": self.supplier.id, "currency": currency, "issued_on": issued_on or self.today,
            "valid_until": valid_until,
            "items": [{"product_id": item["product_id"], "unit_price": price} for item in row["items"]]})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def prepare(self, row, quote):
        response = self.clients["asistente"].post(f"/api/replenishment-requests/{row['id']}/prepare",
                                                  json={"supplier_quote_id": quote["id"]})
        self.assertEqual(response.status_code, 200, response.get_json())
        return self.order(response.get_json()["purchase_order"]["id"])

    def draft(self, items=None, **quote):
        row = self.request(items)
        self.start(row)
        return self.prepare(row, self.quote(row, **quote))

    def approved(self, items=None, **quote):
        order = self.draft(items, **quote)
        response = self.clients["admin"].post(f"/api/purchases/{order['id']}/approve", json={})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def order(self, order_id, client="asistente"):
        response = self.clients[client].get(f"/api/purchases/{order_id}")
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def receive(self, order, quantities, **extra):
        items = [{"line_id": line["id"], "quantity": quantity}
                 for line, quantity in zip(order["items"], quantities) if quantity]
        return self.clients["inventario"].post(f"/api/purchases/{order['id']}/receptions",
                                               json={"received_at": self.today, "items": items, **extra})

    def counts(self):
        db.session.expire_all()
        return (PurchaseOrder.query.count(), PurchaseReception.query.count(), StockMovement.query.count(),
                ReplenishmentRequest.query.count())


class DraftAndImmutabilityTests(TutorFlowCase):
    def test_draft_print_is_not_final(self):
        order = self.draft()
        self.assertEqual(order["status_label"], "Borrador pendiente de aprobación")
        printed = self.clients["asistente"].get(f"/purchases/{order['id']}/print").get_data(as_text=True)
        self.assertIn("BORRADOR PENDIENTE DE APROBACIÓN", printed)
        self.assertIn("No constituye una orden de compra definitiva", printed)
        approved = self.clients["admin"].post(f"/api/purchases/{order['id']}/approve", json={}).get_json()
        stock = self.stock()
        for _ in range(2):
            final = self.clients["admin"].get(f"/purchases/{order['id']}/print").get_data(as_text=True)
            self.assertNotIn("BORRADOR", final)
            self.assertIn("Aprobada por:", final)
        again = self.order(order["id"])
        self.assertEqual((again["status"], again["approved_at"]), ("approved", approved["approved_at"]))
        self.assertEqual(self.stock(), stock)

    def test_approved_order_is_immutable(self):
        order = self.approved()
        before = self.order(order["id"])
        for change in ({"notes": "cambio"}, {"estimated_delivery_date": "2099-01-01"},
                       {"supplier_quote_id": order["supplier_quote_id"]},
                       {"items": [{"product_id": self.product.id, "quantity": 1}]}):
            response = self.clients["asistente"].patch(f"/api/purchases/{order['id']}", json=change)
            self.assertEqual(response.status_code, 409, (change, response.get_json()))
        after = self.order(order["id"])
        for key in ("status", "notes", "estimated_delivery_date", "supplier", "currency", "supplier_quote_id",
                    "items", "total", "approved_at", "approved_by_user_id"):
            self.assertEqual(after[key], before[key], key)


class ReceptionTests(TutorFlowCase):
    def test_partial_requires_reason(self):
        order = self.approved()
        before, stock = self.counts(), self.stock()
        for extra, fragment in (({}, "confirme"),
                                ({"confirm_partial_closure": True}, "motivo concreto"),
                                ({"confirm_partial_closure": True, "closure_reason": "corto"}, "motivo concreto"),
                                ({"confirm_partial_closure": "true", "closure_reason": "Proveedor sin existencia"}, "confirme"),
                                ({"closure_reason": "Proveedor sin existencia del resto"}, "confirme")):
            response = self.receive(order, [6], **extra)
            self.assertEqual(response.status_code, 400, (extra, response.get_json()))
            self.assertIn(fragment, response.get_json()["error"])
            self.assertEqual((self.counts(), self.stock()), (before, stock))
            self.assertEqual(self.order(order["id"])["status"], "approved")
        reason = "El proveedor solo despachó 6 unidades por falta de existencia."
        response = self.receive(order, [6], confirm_partial_closure=True, closure_reason=reason)
        self.assertEqual(response.status_code, 200, response.get_json())
        closed = response.get_json()
        self.assertEqual((closed["status"], closed["closure_reason"]), ("closed_partial", reason))
        line = closed["items"][0]
        self.assertEqual((line["quantity_received"], line["quantity_not_supplied"], line["quantity_expected"]), (6, 4, 0))
        self.assertEqual(self.stock(), stock + 6)
        self.assertNotIn(self.product.id, replenishment_service.pending_supply_by_product())
        printed = self.clients["inventario"].get(f"/purchases/{order['id']}/print").get_data(as_text=True)
        self.assertIn(reason, printed)

    def test_complete_receipt_needs_no_reason(self):
        order = self.approved()
        stock = self.stock()
        response = self.receive(order, [10])
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["status"], "closed_complete")
        self.assertEqual(self.stock(), stock + 10)

    def test_partial_receipt_is_atomic(self):
        second = Product(code="TEST-02", name="Segundo producto sintético", category_id=self.product.category_id,
                         current_stock=5, minimum_stock=0, purchase_price=2, sale_price=3,
                         purchase_price_currency="USD", sale_price_currency="USD", is_active=True)
        db.session.add(second)
        db.session.commit()
        self.associate(second, self.supplier)
        order = self.approved(items=[(self.product.id, 10), (second.id, 8)])
        before, stocks = self.counts(), (self.stock(), db.session.get(Product, second.id).current_stock)
        real, calls = inventory_service.register_entry, []

        def fail_on_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("fallo simulado en el segundo renglón")
            return real(*args, **kwargs)

        with patch("app.services.inventory_service.register_entry", side_effect=fail_on_second):
            try:
                response = self.receive(order, [6, 3], confirm_partial_closure=True,
                                        closure_reason="Despacho incompleto del proveedor sintético")
                self.assertGreaterEqual(response.status_code, 400)
            except RuntimeError:
                pass
        self.assertEqual(len(calls), 2)
        db.session.rollback()
        self.assertEqual(self.counts(), before)
        self.assertEqual((self.stock(), db.session.get(Product, second.id).current_stock), stocks)
        current = self.order(order["id"])
        self.assertEqual(current["status"], "approved")
        self.assertEqual([line["quantity_received"] for line in current["items"]], [0, 0])

    def test_second_receipt_rejected(self):
        order = self.approved()
        first = self.receive(order, [6], confirm_partial_closure=True, closure_reason="Proveedor sin existencia del resto")
        self.assertEqual(first.status_code, 200, first.get_json())
        before, stock = self.counts(), self.stock()
        again = self.receive(order, [4], confirm_partial_closure=True, closure_reason="Llegó el resto después")
        self.assertEqual(again.status_code, 409)
        self.assertEqual((self.counts(), self.stock()), (before, stock))


class ShortageTests(TutorFlowCase):
    def closed_partial(self):
        order = self.approved()
        response = self.receive(order, [6], confirm_partial_closure=True,
                                closure_reason="El proveedor solo tenía 6 unidades disponibles")
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def shortage(self, order, client="inventario", body=None):
        return self.clients[client].post(f"/api/replenishment-requests/from-order/{order['id']}", json=body or {})

    def test_shortage_request_links_orders(self):
        original = self.closed_partial()
        response = self.shortage(original)
        self.assertEqual(response.status_code, 201, response.get_json())
        request = response.get_json()
        self.assertEqual(request["origin"], "shortage")
        self.assertEqual(request["status"], "open")
        self.assertEqual([(item["product_id"], item["quantity"]) for item in request["items"]], [(self.product.id, 4)])
        self.assertEqual(request["source_purchase_order"]["id"], original["id"])
        self.assertNotIn("unit_cost", request["items"][0])
        retry = self.shortage(original)
        self.assertEqual(retry.get_json()["id"], request["id"])
        self.assertEqual(ReplenishmentRequest.query.filter_by(source_purchase_order_id=original["id"]).count(), 1)
        self.start(request)
        new_order = self.prepare(request, self.quote(request, price="12.00"))
        self.assertEqual(new_order["status"], "pending_approval")
        self.assertEqual(new_order["replenishment_request_id"], request["id"])
        self.assertEqual([(line["product_id"], line["quantity_ordered"]) for line in new_order["items"]], [(self.product.id, 4)])
        chain = self.order(original["id"])["shortage_requests"]
        self.assertEqual([(row["id"], row["purchase_order_id"]) for row in chain], [(request["id"], new_order["id"])])
        page = self.clients["inventario"].get(f"/purchases/requests/{request['id']}").get_data(as_text=True)
        self.assertIn(original["order_number"], page)
        unchanged = self.order(original["id"], client="inventario")
        for key in ("status", "closure_reason", "items", "supplier_quote_id", "closed_at"):
            self.assertEqual(unchanged[key], original[key], key)
        fulfilled = self.shortage(original)
        self.assertEqual(fulfilled.status_code, 409)
        self.assertIn(request["request_number"], fulfilled.get_json()["error"])

    def test_cancelled_shortage_request_can_be_replaced(self):
        original = self.closed_partial()
        first = self.shortage(original).get_json()
        cancelled = self.clients["asistente"].post(f"/api/replenishment-requests/{first['id']}/cancel", json={})
        self.assertEqual(cancelled.status_code, 200, cancelled.get_json())
        second = self.shortage(original)
        self.assertEqual(second.status_code, 201, second.get_json())
        self.assertNotEqual(second.get_json()["id"], first["id"])

    def test_shortage_guards(self):
        original = self.closed_partial()
        for client in ("asistente", "admin", "vendedor"):
            self.assertEqual(self.shortage(original, client=client).status_code, 403, client)
        tampered = self.shortage(original, body={"items": [{"product_id": self.product.id, "quantity": 400}]})
        self.assertEqual(tampered.status_code, 400)
        approved = self.approved()
        self.assertEqual(self.shortage(approved).status_code, 409)
        complete = self.approved()
        self.assertEqual(self.receive(complete, [10]).status_code, 200)
        self.assertEqual(self.shortage(complete).status_code, 409)
        other = User(name="Inventario 2", username="inventario2", email="inv2@example.invalid", role="inventario")
        db.session.add(other)
        db.session.commit()
        first = self.shortage(original)
        self.assertEqual(first.status_code, 201)
        duplicate = self.client_for(other).post(f"/api/replenishment-requests/from-order/{original['id']}", json={})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(ReplenishmentRequest.query.filter_by(source_purchase_order_id=original["id"]).count(), 1)

    def test_closed_unavailable_order_reuses_the_shortage_flow(self):
        order = self.approved()
        asked = self.clients["asistente"].post(f"/api/purchases/{order['id']}/closure-requests",
                                               json={"kind": "unavailable", "reason": "Sin existencia en proveedor"})
        self.assertEqual(asked.status_code, 200, asked.get_json())
        closed = self.clients["admin"].post(f"/api/purchases/{order['id']}/closure-requests/approve", json={})
        self.assertEqual(closed.get_json()["status"], "closed_unavailable")
        response = self.shortage(order)
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()["items"][0]["quantity"], 10)


class DirectPathTests(TutorFlowCase):
    def test_direct_order_and_duplicate_denied(self):
        order = self.approved()
        quote_id = order["supplier_quote_id"]
        before = self.counts()
        direct = self.clients["asistente"].post("/api/purchases", json={
            "supplier_id": self.supplier.id, "supplier_quote_id": quote_id,
            "items": [{"product_id": self.product.id, "quantity": 3}]})
        self.assertEqual(direct.status_code, 409, direct.get_json())
        self.assertIn("solicitud", direct.get_json()["error"])
        reused = self.clients["asistente"].post("/api/purchases", json={
            "supplier_id": self.supplier.id, "supplier_quote_id": quote_id,
            "replenishment_request_id": order["replenishment_request_id"],
            "items": [{"product_id": self.product.id, "quantity": 10}]})
        self.assertEqual(reused.status_code, 409)
        duplicate = self.clients["asistente"].post(f"/api/purchases/{order['id']}/duplicate", json={})
        self.assertEqual(duplicate.status_code, 409)
        retry = self.clients["asistente"].post(f"/api/replenishment-requests/{order['replenishment_request_id']}/prepare",
                                               json={"supplier_quote_id": quote_id})
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(self.counts(), before)

    def test_mismatched_quantities_cannot_be_forced_through_the_request(self):
        row = self.request()
        self.start(row)
        quote = self.quote(row)
        before = self.counts()
        forged = self.clients["asistente"].post("/api/purchases", json={
            "supplier_id": self.supplier.id, "supplier_quote_id": quote["id"], "replenishment_request_id": row["id"],
            "items": [{"product_id": self.product.id, "quantity": 99}]})
        self.assertEqual(forged.status_code, 400)
        self.assertEqual(self.counts(), before)

    def test_new_order_button_points_to_requests(self):
        page = self.clients["asistente"].get("/purchases").get_data(as_text=True)
        self.assertIn('id="po-new" href="/purchases/requests"', page)
        self.assertNotIn("Nueva orden de compra</button>", page)

    def test_roles_follow_required_sequence(self):
        row = self.request()
        self.assertEqual(self.clients["asistente"].post("/api/replenishment-requests", json={
            "items": [{"product_id": self.product.id, "quantity": 1}]}).status_code, 403)
        self.assertEqual(self.clients["inventario"].post(f"/api/replenishment-requests/{row['id']}/start", json={}).status_code, 403)
        self.start(row)
        quote = self.quote(row)
        for client in ("inventario", "admin"):
            self.assertEqual(self.clients[client].post(f"/api/replenishment-requests/{row['id']}/prepare",
                                                       json={"supplier_quote_id": quote["id"]}).status_code, 403)
        order = self.prepare(row, quote)
        for client in ("asistente", "inventario"):
            self.assertEqual(self.clients[client].post(f"/api/purchases/{order['id']}/approve", json={}).status_code, 403)
        for client in ("asistente", "admin"):
            self.assertEqual(self.receive_as(client, order).status_code, 403)
        # Granting the permission to another role never skips the operational separation.
        role = Role.query.filter_by(slug="inventario").one()
        for code in ("replenishment:process", "purchases:write", "purchases:approve"):
            permission = Permission.query.filter_by(code=code).one()
            if permission not in role.permissions:
                role.permissions.append(permission)
        db.session.commit()
        rbac_service.invalidate_cache()
        self.assertEqual(self.clients["inventario"].post(f"/api/purchases/{order['id']}/approve", json={}).status_code, 403)
        other = self.request([(self.product.id, 2)])
        self.assertEqual(self.clients["inventario"].post(f"/api/replenishment-requests/{other['id']}/start", json={}).status_code, 403)
        approved = self.clients["admin"].post(f"/api/purchases/{order['id']}/approve", json={})
        self.assertEqual(approved.status_code, 200, approved.get_json())
        self.assertEqual(self.receive(approved.get_json(), [10]).status_code, 200)

    def receive_as(self, client, order):
        return self.clients[client].post(f"/api/purchases/{order['id']}/receptions", json={
            "received_at": self.today, "items": [{"line_id": order["items"][0]["id"], "quantity": 10}]})


class QuoteRevalidationTests(TutorFlowCase):
    def approve(self, order):
        return self.clients["admin"].post(f"/api/purchases/{order['id']}/approve", json={})

    def assert_blocked(self, order, fragment):
        response = self.approve(order)
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertIn(fragment, response.get_json()["error"])
        current = self.order(order["id"])
        self.assertEqual((current["status"], current["approved_at"]), ("pending_approval", None))

    def replacement(self, order, price="11.00", currency="VES"):
        response = self.clients["asistente"].post("/api/supplier-quotes", json={
            "supplier_id": self.supplier.id, "issued_on": self.today, "currency": currency, "valid_until": "2099-01-01",
            "replaces_quote_id": order["supplier_quote_id"],
            "items": [{"product_id": self.product.id, "quantity": 10, "unit_price": price}]})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def test_cancelled_quote_blocks_approval(self):
        order = self.draft()
        cancelled = self.clients["asistente"].post(f"/api/supplier-quotes/{order['supplier_quote_id']}/cancel", json={})
        self.assertEqual(cancelled.status_code, 200, cancelled.get_json())
        self.assert_blocked(order, "no está vigente")

    def test_replaced_quote_blocks_and_renewal_recalculates(self):
        order = self.draft()
        replacement = self.replacement(order)
        self.assert_blocked(order, "no está vigente")
        renewed = self.clients["asistente"].patch(f"/api/purchases/{order['id']}",
                                                  json={"supplier_quote_id": replacement["id"]})
        self.assertEqual(renewed.status_code, 200, renewed.get_json())
        line = renewed.get_json()["items"][0]
        self.assertEqual((line["quantity_ordered"], line["unit_cost"], line["line_total"]), (10, "11.00", "110.00"))
        approved = self.approve(order)
        self.assertEqual(approved.status_code, 200, approved.get_json())
        self.assertEqual(approved.get_json()["supplier_quote_id"], replacement["id"])

    def test_renewal_keeps_supplier_and_currency(self):
        order = self.draft()
        other_currency = self.clients["asistente"].post("/api/supplier-quotes", json={
            "supplier_id": self.supplier.id, "issued_on": self.today, "currency": "USD", "valid_until": "2099-01-01",
            "items": [{"product_id": self.product.id, "quantity": 10, "unit_price": "3.00"}]}).get_json()
        response = self.clients["asistente"].patch(f"/api/purchases/{order['id']}", json={"supplier_quote_id": other_currency["id"]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("moneda", response.get_json()["error"])
        supplier = Supplier(name="Otro proveedor", tax_id="TEST-SUPPLIER-2")
        db.session.add(supplier)
        db.session.commit()
        self.associate(self.product, supplier)
        foreign = self.clients["asistente"].post("/api/supplier-quotes", json={
            "supplier_id": supplier.id, "issued_on": self.today, "currency": "VES", "valid_until": "2099-01-01",
            "items": [{"product_id": self.product.id, "quantity": 10, "unit_price": "9.00"}]}).get_json()
        response = self.clients["asistente"].patch(f"/api/purchases/{order['id']}", json={"supplier_quote_id": foreign["id"]})
        self.assertEqual(response.status_code, 400)
        for change in ({"supplier_id": supplier.id}, {"items": [{"product_id": self.product.id, "quantity": 3}]}):
            self.assertEqual(self.clients["asistente"].patch(f"/api/purchases/{order['id']}", json=change).status_code, 409)
        self.assertEqual(self.order(order["id"])["items"][0]["unit_cost"], "10.00")

    def test_expiry_uses_caracas_date_without_a_fixed_window(self):
        same_day = self.draft(valid_until=self.today)
        self.assertEqual(self.approve(same_day).status_code, 200)
        old_issue = (caracas_today() - timedelta(days=40)).isoformat()
        long_lived = self.draft(issued_on=old_issue, valid_until="2099-01-01")
        self.assertEqual(self.approve(long_lived).status_code, 200)
        expired = self.draft()
        quote = db.session.get(SupplierQuote, expired["supplier_quote_id"])
        quote.valid_until = caracas_today() - timedelta(days=1)
        db.session.commit()
        self.assert_blocked(expired, "no está vigente")

    def test_product_supplier_and_association_are_revalidated(self):
        order = self.draft()
        product = db.session.get(Product, self.product.id)
        product.is_active = False
        db.session.commit()
        self.assert_blocked(order, "productos")
        product.is_active = True
        supplier = db.session.get(Supplier, self.supplier.id)
        supplier.is_active = False
        db.session.commit()
        self.assert_blocked(order, "proveedor")
        supplier.is_active = True
        db.session.commit()
        self.assertEqual(self.approve(order).status_code, 200)

    def test_tampered_price_is_detected(self):
        order = self.draft()
        line = db.session.get(PurchaseOrder, order["id"]).lines[0]
        line.unit_cost = 1
        db.session.commit()
        self.assert_blocked(order, "no coincide")


if __name__ == "__main__":
    unittest.main(verbosity=2)
