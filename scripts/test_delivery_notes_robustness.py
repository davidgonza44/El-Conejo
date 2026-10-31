"""Notas de entrega y clientes: borrado con documentos, horas, fechas, duplicados, numeración,
reintentos, motivo de cancelación, cliente enlazado, paginación y migración 0010.

SQLite temporal en memoria; no usa servidor, MySQL ni datos reales. Uso:
    python scripts/test_delivery_notes_robustness.py
"""
import importlib.util
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from approval_test_support import IsolatedCase
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect, text

from app.extensions import db
from app.models import Customer, DeliveryNote, DocumentSequence, Order, Quote
from app.services import delivery_note_pdf_service
from test_delivery_note_pdf import extract_pdf_text

REASON = {"reason": "Cliente devolvió la mercancía"}


class Base(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.product.current_stock = 50
        db.session.commit()
        self.seller = self.clients["vendedor"]
        self.admin = self.clients["admin"]

    def note(self, client=None, **extra):
        payload = {"customer_name": "Cliente mostrador", "items": [{"product_id": self.product.id, "quantity": 1}], **extra}
        return (client or self.seller).post("/api/delivery-notes", json=payload)

    def customer(self, **fields):
        customer = Customer(name=fields.pop("name", "Constructora Sintética"), **fields)
        db.session.add(customer)
        db.session.commit()
        return customer


class CustomerDeletion(Base):
    def test_customer_with_quote_or_order_is_deactivated_not_500(self):
        seller_id = self.users["vendedor"].id
        with_quote, with_order = self.customer(name="Con cotización"), self.customer(name="Con pedido")
        db.session.add_all([
            Quote(customer_id=with_quote.id, seller_id=seller_id, validity_days=15, valid_until=date.today()),
            Order(customer_id=with_order.id, seller_id=seller_id, estimated_delivery_date=date.today()),
        ])
        db.session.commit()
        for customer in (with_quote, with_order):
            response = self.seller.delete(f"/api/customers/{customer.id}")
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()["action"], "deactivated")
            db.session.expire_all()
            self.assertFalse(db.session.get(Customer, customer.id).is_active)

    def test_customer_with_linked_note_is_deactivated_and_unused_one_is_deleted(self):
        linked, unused = self.customer(name="Con nota"), self.customer(name="Sin uso")
        self.assertEqual(self.note(customer_id=linked.id).status_code, 201)
        self.assertEqual(self.seller.delete(f"/api/customers/{linked.id}").get_json()["action"], "deactivated")
        self.assertEqual(self.seller.delete(f"/api/customers/{unused.id}").get_json()["action"], "deleted")


class Duplicates(Base):
    def test_simultaneous_duplicate_customer_returns_409(self):
        self.customer(name="Primero", tax_id="J-12345678-9")
        # Simula la carrera: la revisión previa no ve el registro que la otra petición acaba de guardar.
        with patch("app.services.customer_service._unique"):
            response = self.seller.post("/api/customers", json={"name": "Segundo", "tax_id": "J-12345678-9"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("RIF o correo", response.get_json()["error"])

    def test_any_unhandled_integrity_error_is_409_and_session_usable(self):
        from sqlalchemy.exc import IntegrityError

        with patch("app.services.customer_service.create_customer",
                   side_effect=IntegrityError("INSERT", {}, Exception("duplicado"))):
            response = self.seller.post("/api/customers", json={"name": "Carrera"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.seller.get("/api/customers").status_code, 200)

    def test_customer_phone_uses_delivery_note_format(self):
        bad = self.seller.post("/api/customers", json={"name": "Teléfono malo", "phone": "llamar luego"})
        self.assertEqual(bad.status_code, 400)
        good = self.seller.post("/api/customers", json={"name": "Teléfono bueno", "phone": "0414-1234567"})
        self.assertEqual(good.status_code, 201, good.get_json())


class TimesAndDates(Base):
    def test_pdf_prints_caracas_time(self):
        note_id = self.note().get_json()["delivery_note"]["id"]
        db.session.get(DeliveryNote, note_id).created_at = datetime(2026, 10, 30, 19, 0)  # 15:00 en Caracas
        db.session.commit()
        pdf, _ = delivery_note_pdf_service.build_pdf_response(note_id)
        content = extract_pdf_text(pdf)
        self.assertIn("30/10/2026 15:00", content)
        self.assertNotIn("30/10/2026 19:00", content)

    def test_note_after_8pm_belongs_to_the_same_commercial_day(self):
        note_id = self.note().get_json()["delivery_note"]["id"]
        db.session.get(DeliveryNote, note_id).created_at = datetime(2026, 10, 31, 0, 30)  # 30/10 20:30 en Caracas
        db.session.commit()
        same_day = self.seller.get("/api/delivery-notes?date_from=2026-10-30&date_to=2026-10-30").get_json()
        next_day = self.seller.get("/api/delivery-notes?date_from=2026-10-31&date_to=2026-10-31").get_json()
        self.assertEqual([n["id"] for n in same_day["items"]], [note_id])
        self.assertEqual(next_day["items"], [])


class Numbering(Base):
    def test_failed_note_leaves_no_gap(self):
        first = self.note().get_json()["delivery_note"]["note_number"]
        failed = self.note(items=[{"product_id": self.product.id, "quantity": 10_000}])
        self.assertEqual(failed.status_code, 409)
        second = self.note().get_json()["delivery_note"]["note_number"]
        self.assertEqual((first, second), ("NE-000001", "NE-000002"))

    def test_sequence_continues_after_existing_notes(self):
        self.note()
        db.session.query(DocumentSequence).delete()  # base anterior a la migración 0010
        db.session.commit()
        self.assertEqual(self.note().get_json()["delivery_note"]["note_number"], "NE-000002")


class Retries(Base):
    def test_same_key_returns_same_note_and_moves_stock_once(self):
        before = self.stock()
        first = self.note(idempotency_key="envio-0001-abcdef")
        again = self.note(idempotency_key="envio-0001-abcdef")
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(first.get_json()["delivery_note"]["id"], again.get_json()["delivery_note"]["id"])
        self.assertEqual(self.stock(), before - 1)
        self.assertEqual(DeliveryNote.query.count(), 1)

    def test_key_of_another_user_is_rejected_without_data(self):
        self.note(idempotency_key="envio-0002-abcdef")
        other = self.note(self.admin, idempotency_key="envio-0002-abcdef")
        self.assertEqual(other.status_code, 409)
        self.assertNotIn("delivery_note", other.get_json())

    def test_invalid_key_is_rejected(self):
        self.assertEqual(self.note(idempotency_key="corta").status_code, 400)


class Cancellation(Base):
    def test_reason_is_required_and_stored(self):
        note_id = self.note().get_json()["delivery_note"]["id"]
        self.assertEqual(self.admin.post(f"/api/delivery-notes/{note_id}/cancel", json={}).status_code, 400)
        self.assertEqual(self.admin.post(f"/api/delivery-notes/{note_id}/cancel", json={"reason": "no"}).status_code, 400)
        response = self.admin.post(f"/api/delivery-notes/{note_id}/cancel", json=REASON)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["delivery_note"]["cancellation_reason"], REASON["reason"])


class LinkedCustomer(Base):
    def test_note_copies_directory_data_and_filters_by_customer(self):
        customer = self.customer(name="Ferretería Directorio", tax_id="J-30777888-2", phone="0241-8990011")
        response = self.note(customer_id=customer.id, customer_name="Nombre escrito a mano",
                             customer_address="Av. Bolívar")
        self.assertEqual(response.status_code, 201, response.get_json())
        note = response.get_json()["delivery_note"]
        self.assertEqual((note["customer_id"], note["customer_name"], note["customer_document"], note["customer_phone"],
                          note["customer_address"]),
                         (customer.id, "Ferretería Directorio", "J-30777888-2", "0241-8990011", "Av. Bolívar"))
        self.note()  # cliente ocasional, sin vínculo
        history = self.seller.get(f"/api/delivery-notes?customer_id={customer.id}").get_json()
        self.assertEqual([n["id"] for n in history["items"]], [note["id"]])

    def test_inactive_or_missing_customer_is_rejected(self):
        inactive = self.customer(name="Inactivo", is_active=False)
        self.assertEqual(self.note(customer_id=inactive.id).status_code, 409)
        self.assertEqual(self.note(customer_id=999).status_code, 404)
        self.assertEqual(self.note(customer_id="1").status_code, 400)


class Pagination(Base):
    def test_page_and_summary_cover_all_filtered_notes(self):
        ids = [self.note().get_json()["delivery_note"]["id"] for _ in range(3)]
        self.admin.post(f"/api/delivery-notes/{ids[0]}/cancel", json=REASON)
        page = self.admin.get("/api/delivery-notes?page=1&per_page=2").get_json()
        self.assertEqual((page["count"], page["pages"], len(page["items"])), (3, 2, 2))
        self.assertEqual((page["summary"]["issued"], page["summary"]["cancelled"]), (2, 1))
        self.assertAlmostEqual(page["summary"]["issued_amount"], 2 * float(self.product.sale_price))
        second = self.admin.get("/api/delivery-notes?page=2&per_page=2").get_json()
        self.assertEqual([n["id"] for n in second["items"]], [ids[0]])
        self.assertEqual(self.admin.get("/api/delivery-notes?page=0").status_code, 400)
        self.assertEqual(self.admin.get("/api/delivery-notes?per_page=101").status_code, 400)

    def test_unpaginated_contract_is_unchanged(self):
        self.note()
        data = self.seller.get("/api/delivery-notes").get_json()
        self.assertEqual(set(data), {"count", "items"})


@unittest.skipUnless(importlib.util.find_spec("alembic"), "Alembic solo está en requirements-container.txt")
class Migration0010(unittest.TestCase):
    def test_upgrade_adds_columns_and_seeds_sequence_from_last_note(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        engine = create_engine("sqlite:///:memory:")
        metadata = MetaData()
        Table("customers", metadata, Column("id", Integer, primary_key=True))
        notes = Table("delivery_notes", metadata, Column("id", Integer, primary_key=True),
                      Column("note_number", String(30)))
        metadata.create_all(engine)
        path = Path(__file__).resolve().parents[1] / "migrations/versions/0010_delivery_note_robustness.py"
        spec = importlib.util.spec_from_file_location("delivery_note_robustness", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with engine.begin() as connection:
            connection.execute(notes.insert(), [{"id": 4, "note_number": "NE-000004"}, {"id": 9, "note_number": "NE-000009"}])
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            columns = {column["name"] for column in inspect(connection).get_columns("delivery_notes")}
            self.assertTrue({"customer_id", "cancellation_reason", "idempotency_key"} <= columns)
            self.assertEqual(connection.execute(text("SELECT name, last_value FROM document_sequences")).all(),
                             [("delivery_note", 9)])
        engine.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
