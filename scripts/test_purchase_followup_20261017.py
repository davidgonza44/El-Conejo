"""Seguimiento del 17 oct 2026 a las correcciones del tutor (5 oct 2026).

1. Una orden cerrada no presenta su faltante como mercancía por recibir.
2. Si la OC generada por una solicitud de faltante se rechaza o se anula, el faltante vuelve a
   poder tramitarse una sola vez; mientras otra solicitud lo cubra, no se duplica.
3. Un interbloqueo de InnoDB (1213) se reintenta en el controlador; ningún otro error.

SQLite en memoria (approval_test_support). No abre MySQL, el puerto 5001 ni la base de tesis.
"""
import csv
import io
import unittest
from unittest.mock import patch

from test_purchase_tutor_20261005 import TutorFlowCase
from app.models.replenishment_request import ReplenishmentRequest


class ClosedOrderPresentationTests(TutorFlowCase):
    def closed_partial(self):
        order = self.approved()
        response = self.receive(order, [6], confirm_partial_closure=True,
                                closure_reason="El proveedor solo tenía 6 unidades disponibles")
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_open_orders_keep_the_amount_to_receive(self):
        draft = self.order(self.draft()["id"], client="admin")
        self.assertEqual((draft["pending_amount"], draft["pending_units"], draft["expecting_supply"]),
                         ("100.00", 10, True))
        approved = self.order(self.approved()["id"], client="admin")
        self.assertEqual((approved["pending_amount"], approved["items"][0]["quantity_expected"]), ("100.00", 10))

    def test_closed_partial_shows_not_supplied_instead_of_pending(self):
        closed = self.order(self.closed_partial()["id"], client="admin")
        self.assertEqual((closed["pending_amount"], closed["pending_units"], closed["expecting_supply"]),
                         ("0.00", 0, False))
        self.assertEqual((closed["quantity_not_supplied"], closed["not_supplied_amount"]), (4, "40.00"))
        self.assertEqual(closed["items"][0]["quantity_expected"], 0)
        page = self.clients["admin"].get("/purchases").get_data(as_text=True)
        self.assertIn("Sin mercancía por recibir", page)
        self.assertIn("No suministrado: 4 unidades · Bs. 40.00", page)

    def test_rejected_and_cancelled_expect_nothing(self):
        rejected = self.draft()
        response = self.clients["admin"].post(f"/api/purchases/{rejected['id']}/reject", json={"reason": "No procede"})
        self.assertEqual(response.status_code, 200, response.get_json())
        cancelled = self.approved()
        response = self.clients["admin"].post(f"/api/purchases/{cancelled['id']}/cancel", json={"reason": "Error de carga"})
        self.assertEqual(response.status_code, 200, response.get_json())
        for order_id in (rejected["id"], cancelled["id"]):
            row = self.order(order_id, client="admin")
            self.assertEqual((row["pending_amount"], row["pending_units"], row["not_supplied_amount"]),
                             ("0.00", 0, "0.00"))

    def test_user_without_cost_sees_units_only(self):
        self.closed_partial()
        page = self.clients["inventario"].get("/purchases").get_data(as_text=True)
        self.assertIn("No suministrado: 4 unidades</small>", page)
        self.assertNotIn("40.00", page)
        detail = self.order(self.closed_partial()["id"], client="inventario")
        self.assertNotIn("not_supplied_amount", detail)
        self.assertNotIn("pending_amount", detail)

    def test_csv_separates_to_receive_from_not_supplied(self):
        closed = self.closed_partial()
        response = self.clients["admin"].get("/api/purchases/export.csv")
        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(response.data.decode("utf-8-sig")), delimiter=self.delimiter(response)))
        row = next(item for item in rows if item["N° Orden"] == closed["order_number"])
        self.assertEqual((row["Unidades por recibir"], row["Unidades no suministradas"]), ("0", "4"))
        self.assertNotIn("Monto pendiente", row)
        self.assertIn("40", row["Monto no suministrado"])
        self.assertIn("0", row["Monto por recibir"])
        hidden = self.clients["inventario"].get("/api/purchases/export.csv").data.decode("utf-8-sig")
        self.assertIn("Unidades no suministradas", hidden)
        self.assertNotIn("Monto no suministrado", hidden)

    @staticmethod
    def delimiter(response):
        header = response.data.decode("utf-8-sig").splitlines()[0]
        return ";" if header.count(";") > header.count(",") else ","


class ShortageReprocessingTests(TutorFlowCase):
    def closed_partial(self):
        order = self.approved()
        response = self.receive(order, [6], confirm_partial_closure=True,
                                closure_reason="El proveedor solo tenía 6 unidades disponibles")
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def shortage(self, order, client="inventario"):
        return self.clients[client].post(f"/api/replenishment-requests/from-order/{order['id']}", json={})

    def generated_draft(self, original):
        request = self.shortage(original).get_json()
        self.start(request)
        return request, self.prepare(request, self.quote(request, price="12.00"))

    def assert_freed_once(self, original, first_request):
        before = ReplenishmentRequest.query.filter_by(source_purchase_order_id=original["id"]).count()
        again = self.shortage(original)
        self.assertEqual(again.status_code, 201, again.get_json())
        renewed = again.get_json()
        self.assertNotEqual(renewed["id"], first_request["id"])
        self.assertEqual([(item["product_id"], item["quantity"]) for item in renewed["items"]], [(self.product.id, 4)])
        retry = self.shortage(original)
        self.assertEqual(retry.get_json()["id"], renewed["id"])
        self.assertEqual(ReplenishmentRequest.query.filter_by(source_purchase_order_id=original["id"]).count(), before + 1)
        chain = {row["id"]: row["covers_shortage"] for row in self.order(original["id"])["shortage_requests"]}
        self.assertEqual(chain, {first_request["id"]: False, renewed["id"]: True})
        return renewed

    def test_rejected_generated_order_frees_the_shortage(self):
        original = self.closed_partial()
        request, draft = self.generated_draft(original)
        self.assertEqual(self.shortage(original).status_code, 409)
        rejected = self.clients["admin"].post(f"/api/purchases/{draft['id']}/reject", json={"reason": "Precio fuera de rango"})
        self.assertEqual(rejected.status_code, 200, rejected.get_json())
        self.assert_freed_once(original, request)
        unchanged = self.order(original["id"])
        self.assertEqual((unchanged["status"], unchanged["closure_reason"]), (original["status"], original["closure_reason"]))

    def test_cancelled_generated_order_frees_the_shortage(self):
        original = self.closed_partial()
        request, draft = self.generated_draft(original)
        cancelled = self.clients["admin"].post(f"/api/purchases/{draft['id']}/cancel", json={"reason": "Carga duplicada"})
        self.assertEqual(cancelled.status_code, 200, cancelled.get_json())
        self.assert_freed_once(original, request)

    def test_live_generated_order_keeps_blocking(self):
        original = self.closed_partial()
        _request, draft = self.generated_draft(original)
        self.assertEqual(self.shortage(original).status_code, 409)
        approved = self.clients["admin"].post(f"/api/purchases/{draft['id']}/approve", json={})
        self.assertEqual(approved.status_code, 200, approved.get_json())
        self.assertEqual(self.shortage(original).status_code, 409)
        self.assertEqual(self.receive(approved.get_json(), [4]).status_code, 200)
        self.assertEqual(self.shortage(original).status_code, 409)
        self.assertEqual(ReplenishmentRequest.query.filter_by(source_purchase_order_id=original["id"]).count(), 1)

    def test_other_user_cannot_duplicate_a_renewed_request(self):
        original = self.closed_partial()
        request, draft = self.generated_draft(original)
        self.clients["admin"].post(f"/api/purchases/{draft['id']}/reject", json={"reason": "Precio fuera de rango"})
        self.assert_freed_once(original, request)
        from app.extensions import db
        from app.models import User

        other = User(name="Inventario 2", username="inventario2", email="inv2@example.invalid", role="inventario")
        db.session.add(other)
        db.session.commit()
        duplicate = self.client_for(other).post(f"/api/replenishment-requests/from-order/{original['id']}", json={})
        self.assertEqual(duplicate.status_code, 409)


class DeadlockRetryTests(TutorFlowCase):
    """El controlador reintenta solo el error 1213 de InnoDB, a lo sumo dos veces."""

    def post_request(self, failures, code=1213):
        from sqlalchemy.exc import OperationalError
        from app.services import replenishment_request_service

        real, calls = replenishment_request_service.create, []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) <= failures:
                raise OperationalError("INSERT", {}, Exception(code, "sintético"))
            return real(*args, **kwargs)

        with patch.object(replenishment_request_service, "create", side_effect=flaky):
            response = self.clients["inventario"].post("/api/replenishment-requests", json={
                "items": [{"product_id": self.product.id, "quantity": 3}]})
        return response, len(calls)

    def test_deadlock_is_retried_until_it_succeeds(self):
        before = ReplenishmentRequest.query.count()
        response, calls = self.post_request(failures=2)
        self.assertEqual((response.status_code, calls), (201, 3), response.get_json())
        self.assertEqual(ReplenishmentRequest.query.count(), before + 1)

    def test_persistent_deadlock_stops_after_two_retries(self):
        before = ReplenishmentRequest.query.count()
        response, calls = self.post_request(failures=5)
        self.assertEqual((response.status_code, calls), (409, 3))
        self.assertNotIn("sintético", response.get_data(as_text=True))
        self.assertEqual(ReplenishmentRequest.query.count(), before)

    def test_other_database_errors_are_not_retried(self):
        response, calls = self.post_request(failures=1, code=1205)
        self.assertEqual((response.status_code, calls), (409, 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
