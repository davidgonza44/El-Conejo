"""HTTP acceptance using synthetic CSVs and disposable SQLite; no live services."""
import csv
import hashlib
import io
import json
import unittest
from datetime import date
from unittest.mock import patch

from approval_test_support import IsolatedCase
from test_real_2026_source import calendar_days, change_row, csv_bytes, rewrite_report, synthetic_package

from app.extensions import db
from app.models import Product
from app.models.real_2026 import Real2026Daily, Real2026Import, Real2026Product
from app.services import real_2026_source_service as source


BASE = "/api/historical-2026/imports"


def analytical_fixture():
    files = synthetic_package()
    filename = source.SERIES_FILES["STRICT"]
    reader = csv.DictReader(io.StringIO(files[filename].decode("utf-8")))
    rows = [row for row in reader if row["codigo_producto"] != "0001"]
    for index, day in enumerate(calendar_days(), 1):
        rows.append(dict(zip(source.SERIES_FIELDS, (
            "0001", "Producto sintético 0001", "STRICT", "false", day.isoformat(),
            str(index), str(index), "2.500001" if index % 3 else "0", str(index % 3 == 0).lower()))))
    files[filename] = csv_bytes(source.SERIES_FIELDS, rows)
    rewrite_report(files)
    return files


def operational_snapshot():
    """Compare contents, including legacy 2025, rather than just stock counts."""
    names = ("products", "categories", "stock_movements", "delivery_notes", "delivery_note_items",
             "customers", "sales", "sale_lines", "quotes", "quote_lines", "orders", "order_lines",
             "purchase_orders", "purchase_order_lines", "purchase_receptions", "purchase_reception_lines",
             "inventory_snapshots", "historical_imports", "historical_demand_records", "historical_import_errors")
    result = {}
    for name in names:
        table = db.metadata.tables.get(name)
        if table is not None:
            rows = [dict(row) for row in db.session.execute(table.select()).mappings()]
            result[name] = sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
    return result


class Real2026FlowTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.files = analytical_fixture()
        self.pin = patch.object(source, "BUILD_SHA256", hashlib.sha256(self.files[source.REPORT_FILE]).hexdigest())
        self.pin.start()
        self.addCleanup(self.pin.stop)
        self.admin = self.clients["admin"]
        self.inventory = self.clients["inventario"]
        db.session.add(Product(code="0001", name="Producto operativo distinto", category_id=self.product.category_id,
                               current_stock=777, minimum_stock=1, purchase_price=1, sale_price=2))
        db.session.commit()
        self.before = operational_snapshot()

    def tearDown(self):
        self.assertEqual(self.before, operational_snapshot())
        super().tearDown()

    def upload(self, files=None, **fields):
        data = {**fields, "files": [(io.BytesIO(content), name) for name, content in (files or self.files).items()]}
        return self.admin.post(BASE + "/preview", data=data, content_type="multipart/form-data")

    def confirmed(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.get_json())
        data = response.get_json()
        public_id = data["batch"]["id"]
        confirmed = self.admin.post(BASE + f"/{public_id}/confirm", json={"token": data["confirmation_token"]})
        self.assertEqual(confirmed.status_code, 200, confirmed.get_json())
        return public_id, data["confirmation_token"]

    def test_csv_preview_confirm_product_history_forecast_and_replay(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.get_json())
        preview = response.get_json()
        public_id = preview["batch"]["id"]
        self.assertEqual(preview["batch"]["status"], "preview")
        self.assertEqual(self.admin.get(BASE + f"/{public_id}/products").status_code, 404)
        confirm = self.admin.post(BASE + f"/{public_id}/confirm", json={"token": preview["confirmation_token"]})
        self.assertEqual(confirm.status_code, 200, confirm.get_json())
        listed = self.admin.get(BASE + f"/{public_id}/products?search=0001&page=1").get_json()
        self.assertEqual(listed["total"], 1)
        product = listed["items"][0]
        self.assertEqual(product["name"], "Producto sintético 0001")
        path = BASE + f"/{public_id}/products/{product['id']}"
        history = self.admin.get(path).get_json()
        self.assertEqual(history["summary"]["count"], 216)
        self.assertEqual(history["summary"]["last_date"], "2026-09-18")
        self.assertEqual(history["points"][0]["quantity"], "2.500001")
        result = self.admin.get(path + "/analysis?model=moving_average&horizon=7")
        self.assertEqual(result.status_code, 200, result.get_json())
        forecast = result.get_json()["forecast"]
        self.assertEqual(forecast["status"], "available")
        self.assertEqual(len(forecast["points"]), 7)
        self.assertEqual(forecast["cutoff"], "2026-09-18")
        self.assertEqual([point["date"] for point in forecast["points"]],
                         ["2026-09-19", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-26"])
        result = self.admin.get(path + "/analysis?model=random_forest&horizon=30")
        self.assertEqual(result.status_code, 200, result.get_json())
        forecast = result.get_json()["forecast"]
        self.assertEqual(forecast["status"], "available", forecast["reason"])
        self.assertEqual(len(forecast["points"]), 30)
        self.assertEqual((forecast["points"][0]["date"], forecast["points"][-1]["date"]), ("2026-09-19", "2026-10-23"))
        self.assertEqual(forecast["future_calendar"]["calendar_days"], 35)
        count = Real2026Daily.query.count()
        replay = self.upload().get_json()
        self.assertFalse(replay["created"])
        self.assertEqual(replay["batch"]["id"], public_id)
        repeated = self.admin.post(BASE + f"/{public_id}/confirm", json={"token": preview["confirmation_token"]})
        self.assertFalse(repeated.get_json()["changed"])
        self.assertEqual(count, Real2026Daily.query.count())

    def test_permission_and_csrf_guards(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get(BASE).status_code, 401)
        self.assertEqual(self.clients["vendedor"].get(BASE).status_code, 403)
        self.assertEqual(self.inventory.post(BASE + "/preview", data={}).status_code, 403)
        self.assertEqual(self.admin.post(BASE + "/preview", data={}, headers={"X-CSRFToken": "bad"}).status_code, 400)
        public_id, _ = self.confirmed()
        self.assertEqual(self.inventory.get(BASE + f"/{public_id}/products").status_code, 200)
        self.assertEqual(self.inventory.post(BASE + f"/{public_id}/confirm", json={"token": "bad"}).status_code, 403)

    def test_invalid_csv_is_rejected_without_a_batch(self):
        self.files[source.SERIES_FILES["STRICT"]] += b"invalid"
        response = self.upload()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Real2026Import.query.count(), 0)

    def test_revision_preserves_previous_version_and_one_effective_dataset(self):
        public_id, _ = self.confirmed()
        corrected = dict(self.files)
        change_row(corrected, source.SERIES_FILES["STRICT"], 2, demanda_observada="3.000001")
        response = self.upload(corrected, supersedes_public_id=public_id, revision_reason="Corrección explícita de prueba")
        self.assertEqual(response.status_code, 201, response.get_json())
        revision = response.get_json()
        self.assertFalse(revision["batch"]["provenance"]["reference_match"])
        result = self.admin.post(BASE + f"/{revision['batch']['id']}/confirm", json={"token": revision["confirmation_token"]})
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(Real2026Import.query.filter_by(status="confirmed").count(), 1)
        self.assertEqual(Real2026Import.query.filter_by(status="superseded").count(), 1)
        self.assertEqual(self.admin.get(BASE + f"/{public_id}").get_json()["status"], "superseded")
        self.assertTrue(Real2026Product.query.filter_by(batch_id=Real2026Import.query.filter_by(public_id=public_id).one().id).count())

    def test_integer_parameters_empty_search_and_wrong_batch(self):
        public_id, _ = self.confirmed()
        self.assertEqual(self.admin.get(BASE + f"/{public_id}/products?page=no").status_code, 400)
        empty = self.admin.get(BASE + f"/{public_id}/products?search=missing").get_json()
        self.assertEqual(empty["items"], [])
        product = self.admin.get(BASE + f"/{public_id}/products?search=0001").get_json()["items"][0]
        path = BASE + f"/{public_id}/products/{product['id']}/analysis"
        for query in ("horizon=0", "horizon=8", "horizon=abc", "model=missing"):
            self.assertEqual(self.admin.get(path + "?" + query).status_code, 400)
        self.assertEqual(self.admin.get(BASE + f"/{public_id}/products/999999").status_code, 404)

    def test_page_and_existing_entry_points_render(self):
        page = self.admin.get("/historical-2026")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Histórico real 2026", page.get_data(as_text=True))
        self.assertIn('id="rh-files"', page.get_data(as_text=True))
        old_page = self.admin.get("/historical-imports")
        self.assertEqual(old_page.status_code, 200)
        self.assertIn('href="/historical-2026"', old_page.get_data(as_text=True))
        self.assertIn("Importación histórica 2025", old_page.get_data(as_text=True))

    def test_real_history_does_not_enter_the_operational_predictive_source(self):
        from app.services import predictive_data_service
        self.confirmed()
        self.assertGreater(Real2026Daily.query.count(), 0)
        self.assertEqual(predictive_data_service.load_series(today=date(2026, 10, 15)), {})
        response = self.admin.get("/predictive/demand")
        self.assertEqual(response.status_code, 200, response.get_json(silent=True))
        self.assertIn('href="/historical-2026"', response.get_data(as_text=True))
        self.assertNotIn("Producto sintético 0001", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
