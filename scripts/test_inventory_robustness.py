"""Robustez del inventario (auditoría de 2026-10): SQLite desechable, datos sintéticos.

Cubre: ajustes de conciliación por diferencia y con segundo aprobador, notas de
entrega por el servicio central, CHECK de stock en la base, orden de bloqueo y
409 ante deadlock, reintentos idempotentes, productos inactivos, stock inicial al
aprobar, categorías con productos activos y catálogo paginado en el servidor.
"""
import unittest
from datetime import datetime
from io import BytesIO
from unittest.mock import patch

from approval_test_support import IsolatedCase, accredit_run
from flask_login import login_user
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.extensions import db
from app.models import Category, InventoryAdjustmentRequest, Product, StockMovement
from app.models.purchase import PurchaseOrder, PurchaseOrderLine, PurchaseReception
from app.models.purchase_correction import PurchaseReceptionCorrection, PurchaseReceptionCorrectionLine
from app.services import inventory_service


class InventoryRobustnessTests(IsolatedCase):
    # ---- utilidades ----
    def exit_units(self, quantity):
        with self.app.test_request_context():
            login_user(self.users["inventario"])
            inventory_service.register_exit({"product_id": self.product.id, "quantity": quantity})

    def discrepancy(self, external=14, accredited=True):
        """Conciliación confirmada: externo ``external`` contra sistema 10; por defecto, corte acreditado."""
        content = f"product_code;quantity\nTEST-01;{external}\n".encode()
        run = self.clients["admin"].post("/api/reconciliation", data={
            "file": (BytesIO(content), "cut.csv"), "cutoff_at": "2026-09-28T12:00:00-04:00"},
            content_type="multipart/form-data").json
        self.assertEqual(self.clients["admin"].post(f"/api/reconciliation/{run['id']}/confirm", json={}).status_code, 200)
        if accredited:
            accredit_run(self.app, run["id"])
        return run, f"/api/reconciliation/{run['id']}/lines/{run['items'][0]['id']}/resolve"

    def request_adjustment(self, path):
        response = self.clients["admin"].post(path, json={"action": "adjustment", "note": "Conteo físico"})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["items"][0]["adjustment_request"]

    def review(self, request, action="approve", actor="admin2"):
        return self.clients[actor].post(f"/api/inventory/adjustments/{request['id']}/{action}", json={})

    # ---- 1. conciliación ----
    def test_reconciliation_rejected_request_keeps_line_open(self):
        _, path = self.discrepancy()
        request = self.request_adjustment(path)
        blocked = self.clients["admin"].post(path, json={"action": "acknowledge", "note": "Sin ajuste"})
        self.assertEqual(blocked.status_code, 409)  # no se resuelve con una solicitud pendiente
        self.assertEqual(self.review(request, "reject").status_code, 200)
        self.assertEqual(self.stock(), 10)
        again = self.request_adjustment(path)  # rechazada: se puede volver a pedir
        self.assertNotEqual(again["id"], request["id"])
        self.assertEqual(db.session.get(InventoryAdjustmentRequest, again["id"]).to_dict()["source"], "reconciliation")

    def test_reconciliation_blocks_double_correction_and_negative_stock(self):
        _, path = self.discrepancy(external=14)
        first = self.request_adjustment(path)
        _, second_path = self.discrepancy(external=14)  # el mismo faltante medido en otro corte
        second = self.request_adjustment(second_path)
        self.assertEqual(self.review(first).status_code, 200)
        self.assertEqual(self.stock(), 14)
        # El segundo corte ya no es de fiar: un ajuste posterior pudo corregir la misma diferencia.
        self.assertEqual(self.review(second).status_code, 409)
        self.assertEqual(self.stock(), 14)
        self.assertEqual(StockMovement.query.filter_by(movement_type="ajuste").count(), 1)

    def test_unaccredited_csv_cannot_adjust_even_with_two_admins(self):
        _, path = self.discrepancy(accredited=False)  # CSV subido a mano por un administrador
        response = self.clients["admin"].post(path, json={"action": "adjustment", "note": "Conteo externo"})
        self.assertEqual(response.status_code, 403, response.json)
        self.assertEqual((InventoryAdjustmentRequest.query.count(), self.stock()), (0, 10))
        # Una solicitud ya creada tampoco se aplica si la fuente pierde la acreditación.
        _, accredited_path = self.discrepancy()
        request = self.request_adjustment(accredited_path)
        self.app.config["RECONCILIATION_ACCREDITED_SOURCES"] = ()
        self.assertEqual(self.review(request).status_code, 403)
        self.assertEqual(self.stock(), 10)

    def test_reception_correction_after_cut_blocks_double_correction(self):
        _, path = self.discrepancy(external=8)  # faltan 2 según el corte
        request = self.request_adjustment(path)
        # Después del corte, Compras corrige la recepción y descuenta esas mismas 2 unidades.
        admin = self.users["admin"]
        order = PurchaseOrder(order_number="OC-COR", supplier_id=self.supplier.id, created_by_user_id=admin.id,
                              status="closed_complete", currency="USD")
        db.session.add(order)
        db.session.flush()
        po_line = PurchaseOrderLine(purchase_order_id=order.id, product_id=self.product.id, product_code="TEST-01",
                                    product_name="Producto sintético", quantity_ordered=2, quantity_received=2,
                                    unit_cost=1, line_total=2)
        reception = PurchaseReception(purchase_order_id=order.id, received_by_user_id=admin.id,
                                      received_at=datetime.utcnow().date())
        db.session.add_all([po_line, reception])
        db.session.flush()
        with self.app.test_request_context():
            login_user(self.users["inventario"])
            movement = inventory_service.register_exit({"product_id": self.product.id, "quantity": 2}, commit=False)
            correction = PurchaseReceptionCorrection(reception_id=reception.id, requested_by_user_id=self.users["inventario"].id,
                                                     reason="Llegaron 0", status="approved")
            db.session.add(correction)
            db.session.flush()
            db.session.add(PurchaseReceptionCorrectionLine(
                correction_id=correction.id, purchase_order_line_id=po_line.id, product_id=self.product.id,
                previous_quantity=2, target_quantity=0, expected_stock=10, stock_movement_id=movement.id))
            db.session.commit()
        self.assertEqual(self.stock(), 8)
        self.assertEqual(self.review(request).status_code, 409)  # no se descuentan otras 2
        self.assertEqual(self.stock(), 8)

    def test_reconciliation_target_cannot_go_negative(self):
        _, path = self.discrepancy(external=2)  # diferencia -8
        request = self.request_adjustment(path)
        self.exit_units(5)  # quedan 5; 5 - 8 sería negativo
        self.assertEqual(self.review(request).status_code, 409)
        self.assertEqual(self.stock(), 5)

    # ---- 2 y 4. notas de entrega ----
    def test_delivery_note_uses_central_service_notifies_and_locks_in_order(self):
        second = Product(code="TEST-00", name="Otro", category_id=self.product.category_id, current_stock=5,
                         minimum_stock=4, purchase_price=1, sale_price=2, purchase_price_currency="USD",
                         sale_price_currency="USD", is_active=True)
        db.session.add(second)
        db.session.commit()
        payload = {"customer_name": "Cliente sintético",
                   "items": [{"product_id": self.product.id, "quantity": 1}, {"product_id": second.id, "quantity": 2}]}
        with patch.object(inventory_service, "lock_products", wraps=inventory_service.lock_products) as lock, \
                patch.object(inventory_service, "register_exit", wraps=inventory_service.register_exit) as exit_, \
                patch("app.services.notification_service.product_stock_changed") as notify:
            response = self.clients["admin"].post("/api/delivery-notes", json=payload)
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(exit_.call_count, 2)
        self.assertEqual(sorted(call.args[0].id for call in notify.call_args_list), sorted([self.product.id, second.id]))
        self.assertEqual(lock.call_count, 1)
        self.assertEqual(self.stock(), 9)

    def test_deadlock_returns_409_and_rolls_back(self):
        deadlock = OperationalError("SELECT ... FOR UPDATE", {}, Exception(1213, "Deadlock found"))
        payload = {"customer_name": "Cliente", "items": [{"product_id": self.product.id, "quantity": 1}]}
        with patch.object(inventory_service, "lock_products", side_effect=deadlock):
            response = self.clients["admin"].post("/api/delivery-notes", json=payload)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Intente de nuevo", response.json["error"])
        self.assertEqual(self.stock(), 10)

    def test_cancel_note_returns_stock_to_inactive_product(self):
        note = self.clients["admin"].post("/api/delivery-notes", json={
            "customer_name": "Cliente", "items": [{"product_id": self.product.id, "quantity": 10}]}).json
        self.product.is_active = False  # fixture: desactivado después de emitir la nota
        db.session.commit()
        response = self.clients["admin"].post(f"/api/delivery-notes/{note['delivery_note']['id']}/cancel", json={"reason": "Devolución sintética"})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.stock(), 10)

    # ---- 3. CHECK en la base ----
    def test_database_rejects_invalid_stock_rows(self):
        statements = (
            "UPDATE products SET current_stock = -1",
            "UPDATE products SET minimum_stock = -1",
            "INSERT INTO stock_movements (product_id, movement_type, quantity, previous_stock, new_stock, created_at) "
            "VALUES (:id, 'robo', 1, 10, 9, CURRENT_TIMESTAMP)",
            "INSERT INTO stock_movements (product_id, movement_type, quantity, previous_stock, new_stock, created_at) "
            "VALUES (:id, 'salida', 0, 10, 10, CURRENT_TIMESTAMP)",
            "INSERT INTO stock_movements (product_id, movement_type, quantity, previous_stock, new_stock, created_at) "
            "VALUES (:id, 'entrada', 2, 10, 9, CURRENT_TIMESTAMP)",
            "INSERT INTO stock_movements (product_id, movement_type, quantity, previous_stock, new_stock, created_at) "
            "VALUES (:id, 'salida', 11, 10, -1, CURRENT_TIMESTAMP)",
        )
        for statement in statements:
            with self.assertRaises(IntegrityError, msg=statement):
                db.session.execute(text(statement), {"id": self.product.id})
            db.session.rollback()
        self.assertEqual((self.stock(), StockMovement.query.count()), (10, 0))

    # ---- 5. idempotencia ----
    def test_manual_retry_with_same_key_does_not_duplicate(self):
        body = {"product_id": self.product.id, "quantity": 2, "idempotency_key": "retry-key-0001"}
        first = self.clients["inventario"].post("/api/inventory/exit", json=body)
        second = self.clients["inventario"].post("/api/inventory/exit", json=body)
        self.assertEqual((first.status_code, second.status_code), (201, 200), second.json)
        self.assertEqual(first.json["movement"]["id"], second.json["movement"]["id"])
        self.assertEqual((self.stock(), StockMovement.query.count()), (8, 1))
        self.assertEqual(self.clients["inventario"].post("/api/inventory/exit", json={**body, "quantity": 3}).status_code, 409)
        self.assertEqual(self.clients["inventario"].post("/api/inventory/entry", json=body).status_code, 409)
        for key in ("corta", "x" * 65, "con espacio!", 12345678):
            self.assertEqual(self.clients["inventario"].post(
                "/api/inventory/exit", json={**body, "idempotency_key": key}).status_code, 400, key)
        without_key = {"product_id": self.product.id, "quantity": 1}
        self.assertEqual(self.clients["inventario"].post("/api/inventory/entry", json=without_key).status_code, 201)
        self.assertEqual((self.stock(), StockMovement.query.count()), (9, 2))

    # ---- 6. productos inactivos ----
    def test_deactivation_requires_zero_stock_and_no_pending_purchases(self):
        path = f"/api/products/{self.product.id}"
        self.assertEqual(self.clients["asistente"].delete(path).status_code, 409)  # 10 en stock
        self.product.current_stock = 0
        order = PurchaseOrder(order_number="OC-T1", supplier_id=self.supplier.id, created_by_user_id=self.users["asistente"].id,
                              status="approved", currency="USD")
        db.session.add(order)
        db.session.flush()
        db.session.add(PurchaseOrderLine(purchase_order_id=order.id, product_id=self.product.id, product_code="TEST-01",
                                         product_name="Producto sintético", quantity_ordered=5, unit_cost=1, line_total=5))
        db.session.commit()
        self.assertEqual(self.clients["asistente"].delete(path).status_code, 409)  # compra por recibir
        order.status = "closed_complete"
        db.session.commit()
        proposal = self.clients["asistente"].delete(path)
        self.assertEqual(proposal.status_code, 200, proposal.json)

    def test_inactive_product_only_accepts_adjustment_to_zero(self):
        self.product.is_active = False  # fixture: inactivo con existencias heredadas
        db.session.commit()
        body = {"product_id": self.product.id, "reason": "Baja de existencias"}
        self.assertEqual(self.clients["inventario"].post("/api/inventory/adjustment", json={**body, "new_stock": 5}).status_code, 409)
        request = self.clients["inventario"].post("/api/inventory/adjustment", json={**body, "new_stock": 0})
        self.assertEqual(request.status_code, 201, request.json)
        self.assertEqual(self.review(request.json, actor="admin").status_code, 200)
        self.assertEqual(self.stock(), 0)

    # ---- 7. stock inicial al aprobar ----
    def create_product(self, code, stock):
        response = self.clients["asistente"].post("/api/products", json={
            "code": code, "name": "Alta " + code, "category_id": self.product.category_id, "unit": "unidad",
            "current_stock": stock, "minimum_stock": 0, "purchase_price": 1, "sale_price": 2,
            "purchase_price_currency": "USD", "sale_price_currency": "USD"})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def test_initial_stock_enters_only_on_approval(self):
        rejected = self.create_product("ALTA-1", 7)
        self.assertEqual((rejected["current_stock"], rejected["proposed_initial_stock"]), (0, 7))
        self.assertEqual(StockMovement.query.count(), 0)
        response = self.clients["admin"].post(f"/api/products/{rejected['id']}/reject", json={"reason": "Duplicado"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((db.session.get(Product, rejected["id"]).current_stock, StockMovement.query.count()), (0, 0))
        approved = self.create_product("ALTA-2", 7)
        response = self.clients["admin"].post(f"/api/products/{approved['id']}/approve", json={})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual((response.json["current_stock"], response.json["proposed_initial_stock"]), (7, None))
        movement = StockMovement.query.filter_by(product_id=approved["id"]).one()
        self.assertEqual((movement.creation_origin, movement.quantity, movement.user_id),
                         ("initial_stock", 7, self.users["admin"].id))

    # ---- 8. categorías ----
    def test_category_with_active_products_cannot_be_deactivated(self):
        category = db.session.get(Category, self.product.category_id)
        path = f"/api/categories/{category.id}"
        self.assertEqual(self.clients["asistente"].patch(path, json={"is_active": False}).status_code, 409)
        pending = self.create_product("ALTA-3", 0)  # alta pendiente en la categoría
        self.product.is_active = False
        db.session.commit()
        proposal = self.clients["asistente"].patch(path, json={"is_active": False})
        self.assertEqual(proposal.status_code, 200, proposal.json)
        decided = self.clients["admin"].post(path + "/approve", json={"proposal_id": proposal.json["pending_change"]["id"]})
        self.assertEqual(decided.status_code, 200, decided.json)
        # Aprobar el alta pendiente la dejaría activa en una categoría inactiva.
        self.assertEqual(self.clients["admin"].post(f"/api/products/{pending['id']}/approve", json={}).status_code, 400)

    # ---- 9. catálogo paginado ----
    def test_catalog_api_contract_is_paginated(self):
        for index in range(15):
            db.session.add(Product(code=f"CAT-{index:02d}", name=f"Catálogo {index:02d}", category_id=self.product.category_id,
                                   current_stock=index, minimum_stock=3, purchase_price=1, sale_price=2,
                                   purchase_price_currency="USD", sale_price_currency="USD", is_active=True))
        db.session.commit()
        page = self.clients["vendedor"].get("/api/products?page=2&per_page=12&sort=name-asc&status=disponible").json
        self.assertEqual(page["pagination"]["page"], 2)
        self.assertLessEqual(len(page["items"]), 12)
        self.assertTrue(all(item["stock_status"] == "disponible" for item in page["items"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
