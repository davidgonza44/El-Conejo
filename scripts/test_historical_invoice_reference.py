"""Número de factura del sistema comercial externo en el importador histórico (corrección del tutor, 5 oct 2026).

Recorrido real: subida, vista previa, simulación y confirmación. SQLite en memoria y carpeta privada
temporal (approval_test_support). Comprueba que el número original se conserva como texto, separado del
normalizado; que la ausencia se conserva; y que importar no crea ventas, salidas ni facturas, ni duplica
demanda al reimportar.
"""
import csv
import io
import unittest

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import StockMovement
from app.models.historical_demand_record import HistoricalDemandRecord
from app.models.sale import Sale
from app.services.historical_validation_service import CSV_HEADERS

ROWS = [
    {"event_date": "2025-03-04", "product_code": "TEST-01", "quantity": "2", "record_type": "sale",
     "record_status": "issued", "document_number": "fac-000123", "source_record_id": "R-1", "source_line_id": "1"},
    {"event_date": "2025-03-04", "product_code": "TEST-01", "quantity": "3", "record_type": "sale",
     "record_status": "issued", "document_number": "fac-000123", "source_record_id": "R-1", "source_line_id": "2"},
    {"event_date": "2025-03-05", "product_code": "TEST-01", "quantity": "1", "record_type": "sale",
     "record_status": "issued", "document_number": "0004501", "source_record_id": "R-2", "source_line_id": "1"},
]
WITHOUT_NUMBER = {"event_date": "2025-03-06", "product_code": "TEST-01", "quantity": "4", "record_type": "sale",
                  "record_status": "issued", "document_number": "", "source_record_id": "R-3", "source_line_id": "1"}


def csv_bytes(rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_HEADERS, delimiter=";", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in CSV_HEADERS})
    return buffer.getvalue().encode("utf-8-sig")


class InvoiceReferenceTests(IsolatedCase):
    def call(self, method, path, **kwargs):
        response = getattr(self.clients["admin"], method)(path, **kwargs)
        return response.status_code, response.get_json()

    def preview_file(self, content, name):
        status, payload = self.call("post", "/api/historical-imports/upload", data={
            "file": (io.BytesIO(content), name, "text/csv"), "source_system": "sistema-comercial-sintetico",
            "document_type": "historical_demand"}, content_type="multipart/form-data")
        self.assertEqual(status, 201, payload)
        public_id = payload["historical_import"]["id"]
        status, payload = self.call("post", f"/api/historical-imports/{public_id}/preview",
                                    json={"mapping": {name: name for name in CSV_HEADERS}})
        self.assertEqual(status, 200, payload)
        return public_id

    def import_file(self, content, name):
        public_id = self.preview_file(content, name)
        status, payload = self.call("post", f"/api/historical-imports/{public_id}/dry-run", json={})
        self.assertEqual(status, 200, (payload, self.call("get", f"/api/historical-imports/{public_id}/errors")[1]))
        status, payload = self.call("post", f"/api/historical-imports/{public_id}/confirm",
                                    json={"confirmation_token": payload["confirmation_token"]})
        self.assertEqual(status, 200, payload)
        return public_id

    def operational(self):
        db.session.expire_all()
        return self.stock(), StockMovement.query.count(), Sale.query.count()

    def records(self, public_id):
        status, payload = self.call("get", f"/api/historical-imports/{public_id}/records?per_page=100")
        self.assertEqual(status, 200, payload)
        return sorted(payload["items"], key=lambda item: item["source_row_number"])

    def test_original_number_is_kept_apart_from_normalized(self):
        before = self.operational()
        public_id = self.import_file(csv_bytes(ROWS), "facturas.csv")
        meta = [record["admin_metadata"] for record in self.records(public_id)]
        self.assertEqual([item["document_number_original"] for item in meta], ["fac-000123", "fac-000123", "0004501"])
        self.assertEqual([item["document_number_normalized"] for item in meta], ["FAC-000123", "FAC-000123", "0004501"])
        self.assertEqual([item["source_line_id_original"] for item in meta], ["1", "2", "1"])
        self.assertEqual(self.operational(), before)

    def test_missing_number_is_kept_absent_and_sent_to_review(self):
        before = self.operational()
        public_id = self.preview_file(csv_bytes(ROWS + [WITHOUT_NUMBER]), "sin-numero.csv")
        last = self.records(public_id)[-1]["admin_metadata"]
        self.assertEqual((last["document_number_original"], last["document_number_normalized"]), (None, None))
        errors = self.call("get", f"/api/historical-imports/{public_id}/errors")[1]["items"]
        self.assertEqual([item["code"] for item in errors], ["weak_possible_duplicate"])
        status, _ = self.call("post", f"/api/historical-imports/{public_id}/dry-run", json={})
        self.assertEqual(status, 422)
        self.assertEqual(HistoricalDemandRecord.query.filter_by(include_in_demand=True).count(), 0)
        self.assertEqual(self.operational(), before)

    def test_reimport_does_not_duplicate_demand(self):
        first = self.import_file(csv_bytes(ROWS), "facturas.csv")
        active = HistoricalDemandRecord.query.filter_by(include_in_demand=True).count()
        before = self.operational()
        second = self.preview_file(csv_bytes(list(reversed(ROWS))), "facturas-reenviadas.csv")
        self.assertNotEqual(first, second)
        errors = self.call("get", f"/api/historical-imports/{second}/errors")[1]["items"]
        self.assertEqual([item["code"] for item in errors], ["strong_duplicate_existing"] * len(ROWS))
        self.assertEqual(self.call("post", f"/api/historical-imports/{second}/dry-run", json={})[0], 422)
        db.session.expire_all()
        self.assertEqual(active, len(ROWS))
        self.assertEqual(HistoricalDemandRecord.query.filter_by(include_in_demand=True).count(), active)
        documents = {(item["admin_metadata"]["document_number_original"], item["admin_metadata"]["source_line_id_original"])
                     for item in self.records(second)}
        self.assertEqual(documents, {("fac-000123", "1"), ("fac-000123", "2"), ("0004501", "1")})
        self.assertEqual(self.operational(), before)

    def test_format_help_does_not_promise_an_invoice_for_every_source(self):
        page = self.clients["admin"].get("/historical-imports").get_data(as_text=True)
        self.assertIn("cuando la fuente identifique el documento como factura", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
