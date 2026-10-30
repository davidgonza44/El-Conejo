"""Disposable stdlib fixtures; never imports Flask or opens a database."""
import copy
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "app/services/experimental_sources_2025.py"
SPEC = importlib.util.spec_from_file_location("experimental_sources_2025", MODULE_PATH)
SOURCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCE)


def movement(detail="1", code="0001", day="2025-01-02", quantity="2,000000", kind="2", state="Procesado"):
    values = {key: "0" for key in SOURCE.SCHEMAS["movements"].split()}
    values.update(fecha_original=day[8:10] + "/" + day[5:7] + "/" + day[:4], fecha_normalizada=day,
                  id_movimiento=detail, tipo_movimiento=kind, numero_movimiento="D" + detail,
                  estado=state, almacen_movimiento="01", almacen_detalle="01", items_documento="1",
                  total_documento="6,00", id_detalle=detail, codigo_producto=code,
                  nombre_producto="Producto de prueba " + code, cantidad=quantity,
                  precio_unitario="3,00", total_linea="6,00", tipo_unidad="UND", costo_bruto_unitario="")
    return values


def demand(detail):
    return {"fecha": detail["fecha_normalizada"], "id_movimiento": detail["id_movimiento"],
            "fuente_demanda": "DESPACHO" if detail["tipo_movimiento"] == "2" else "FACTURA_SIN_DESPACHO_EQUIVALENTE",
            "numero_documento": detail["numero_movimiento"], "id_detalle": detail["id_detalle"],
            "codigo_producto": detail["codigo_producto"], "nombre_producto": detail["nombre_producto"],
            "cantidad": detail["cantidad"], "precio_unitario": detail["precio_unitario"],
            "total_linea": detail["total_linea"], "almacen": detail["almacen_detalle"]}


def inventory(code="0001", stock="0,00", kardex="OK"):
    values = {key: "" for key in SOURCE.SCHEMAS["inventory"].split()}
    values.update(id_producto=code, estado_producto="1", id_almacen="01", codigo_producto=code,
                  nombre_producto="Producto de prueba " + code, stock_actual=stock, unidad="UND",
                  fecha_ultimo_movimiento="02/01/2025" if stock else "", hora_ultimo_movimiento="12:00:00" if stock else "",
                  ultima_accion="SALIDA" if stock else "", ultimo_modulo="2" if stock else "",
                  ultimo_documento="D1" if stock else "", estado_kardex=kardex)
    return values


class SourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="structured-2025-fixtures-")
        self.addCleanup(self.tmp.cleanup)
        self.paths = [Path(self.tmp.name) / name for name in ("demand.csv", "movements.csv", "inventory.csv")]
        detail = movement()
        self.rows = {"demand": [demand(detail)], "movements": [detail], "inventory": [inventory()]}

    def write(self):
        for (role, rows), path in zip(self.rows.items(), self.paths):
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=SOURCE.SCHEMAS[role].split())
                writer.writeheader()
                writer.writerows(rows)

    def load(self, **options):
        self.write()
        return SOURCE.load_2025_bundle(*self.paths, **options)

    def test_load_preserves_text_identifiers_and_decimal_comma(self):
        result = self.load()
        self.assertEqual(result["observations"][0]["product_code"], "0001")
        self.assertEqual(result["observations"][0]["warehouse_code"], "01")
        self.assertEqual(result["observations"][0]["quantity"], 2.0)
        self.assertEqual(result["observations"][0]["unit"], "UND")

    def test_provenance_per_observation_and_context(self):
        result = self.load()
        for row in [result["metadata"], result["audit"], *result["observations"], *result["inventory_context"]]:
            for key, value in SOURCE.METADATA.items():
                self.assertEqual(row[key], value)

    def test_missing_dates_are_not_zero_by_default(self):
        self.assertEqual(len(self.load()["observations"]), 1)
        self.assertFalse(self.load()["metadata"]["absence_is_observed_zero"])

    def test_calendar_fill_requires_explicit_scenario_and_no_pre_first_date(self):
        result = self.load(calendar_zero_fill_synthetic_scenario=True)
        rows = result["observations"]
        self.assertEqual(len(rows), 364)
        self.assertEqual(rows[0]["date"], "2025-01-02")
        self.assertEqual(rows[1]["quantity"], 0)
        self.assertEqual(rows[1]["detail_ids"], [])
        self.assertEqual(rows[1]["observation_basis"], "calendar_zero_fill_synthetic_scenario")
        self.assertFalse(result["metadata"]["completeness_demonstrated"])

    def test_snapshot_only_products_do_not_create_demand(self):
        self.rows["inventory"].append(inventory("0002", "", "SIN_REGISTRO_KARDEX"))
        result = self.load(calendar_zero_fill_synthetic_scenario=True)
        self.assertEqual({r["product_code"] for r in result["observations"]}, {"0001"})
        self.assertIsNone(result["inventory_context"][1]["stock"])

    def test_stock_zero_is_distinct_from_unknown(self):
        self.rows["inventory"].append(inventory("0002", "", "SIN_REGISTRO_KARDEX"))
        result = self.load()
        self.assertEqual(result["inventory_context"][0]["stock"], 0)
        self.assertIsNone(result["inventory_context"][1]["stock"])
        self.assertEqual(result["audit"]["sources"]["inventory"]["numeric"]["stock_actual"]["null_count"], 1)

    def test_aggregate_multiple_details_same_day(self):
        second = movement("2")
        self.rows["movements"].append(second)
        self.rows["demand"].append(demand(second))
        rows = self.load()["observations"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 4)
        self.assertEqual(rows[0]["detail_ids"], ["1", "2"])

    def test_filter_canceled_budget_and_invoice_equivalent(self):
        self.rows["movements"].extend([movement("2", kind="1"), movement("3", kind="2", state="ANULADO"), movement("4", kind="3")])
        cross = self.load()["audit"]["cross_consistency"]
        self.assertEqual(cross["excluded_rows"], 3)
        self.assertEqual(cross["invoice_dispatch_equivalent_count"], 1)
        self.assertEqual(cross["expected_demand_detail_count"], 1)

    def test_invoice_without_equivalent_included(self):
        invoice = movement("2", day="2025-01-04", kind="1")
        self.rows["movements"].append(invoice)
        self.rows["demand"].append(demand(invoice))
        self.assertEqual(len(self.load()["observations"]), 2)

    def test_cross_fields_every_contract_field_rejected(self):
        base = copy.deepcopy(self.rows)
        for key in ("id_movimiento", "numero_documento", "codigo_producto", "nombre_producto",
                    "cantidad", "precio_unitario", "total_linea", "fecha", "almacen", "fuente_demanda"):
            with self.subTest(field=key):
                self.rows = copy.deepcopy(base)
                self.rows["demand"][0][key] = "2025-01-03" if key == "fecha" else "4,00" if key in ("cantidad", "precio_unitario", "total_linea") else "wrong"
                with self.assertRaises(SOURCE.DatasetAuditError):
                    self.load()

    def test_missing_detail_is_fatal(self):
        self.rows["demand"][0]["id_detalle"] = "99"
        with self.assertRaises(SOURCE.DatasetAuditError):
            self.load()

    def test_missing_product_is_fatal(self):
        self.rows["inventory"][0]["codigo_producto"] = "999"
        with self.assertRaises(SOURCE.DatasetAuditError):
            self.load()

    def test_duplicate_detail_is_fatal(self):
        self.rows["movements"].append(dict(self.rows["movements"][0]))
        with self.assertRaises(SOURCE.DatasetAuditError):
            self.load()

    def test_negative_quantity_is_fatal(self):
        self.rows["movements"][0]["cantidad"] = "-2,0"
        self.rows["demand"][0]["cantidad"] = "-2,0"
        with self.assertRaises(SOURCE.DatasetAuditError):
            self.load()

    def test_zero_quantity_is_fatal_in_positive_source(self):
        self.rows["movements"][0]["cantidad"] = "0"
        self.rows["demand"][0]["cantidad"] = "0"
        with self.assertRaises(SOURCE.DatasetAuditError):
            self.load()

    def test_nonfinite_quantity_rejected(self):
        for value in ("NaN", "Infinity", "1e999", "1.234,56"):
            with self.subTest(value=value):
                self.rows["movements"][0]["cantidad"] = value
                self.rows["demand"][0]["cantidad"] = value
                with self.assertRaises(SOURCE.DatasetAuditError):
                    self.load()

    def test_invalid_date_fatal(self):
        self.rows["movements"][0]["fecha_normalizada"] = "2025-02-30"
        self.rows["demand"][0]["fecha"] = "2025-02-30"
        with self.assertRaises(SOURCE.DatasetAuditError):
            self.load()

    def test_foreign_year_fatal(self):
        self.rows["movements"][0]["fecha_original"] = "02/01/2026"
        self.rows["movements"][0]["fecha_normalizada"] = "2026-01-02"
        self.rows["demand"][0]["fecha"] = "2026-01-02"
        with self.assertRaises(SOURCE.DatasetAuditError):
            self.load()

    def test_snapshot_old_last_movement_valid_future_impossible(self):
        self.rows["inventory"][0]["fecha_ultimo_movimiento"] = "31/01/2022"
        self.assertEqual(self.load()["inventory_context"][0]["last_movement_date"], "2022-01-31")
        self.rows["inventory"][0]["fecha_ultimo_movimiento"] = "01/01/2026"
        with self.assertRaises(SOURCE.DatasetAuditError):
            self.load()

    def test_snapshot_name_disagreement_warned_without_rewrite(self):
        self.rows["inventory"][0]["nombre_producto"] = "Nombre del contexto"
        result = self.load()
        self.assertIn("snapshot_name_mismatch_product_count", result["audit"]["warnings"])
        self.assertEqual(result["product_identity_by_code"]["0001"]["name"], "Producto de prueba 0001")
        self.assertEqual(result["inventory_context"][0]["name"], "Nombre del contexto")

    def test_snapshot_unit_disagreement_prevents_stock_use(self):
        self.rows["inventory"][0]["unidad"] = "KG."
        self.assertFalse(self.load()["inventory_context"][0]["usable_for_demand_unit"])

    def test_invalid_snapshot_time_is_fatal(self):
        self.rows["inventory"][0]["hora_ultimo_movimiento"] = "25:00:00"
        with self.assertRaises(SOURCE.DatasetAuditError):
            self.load()

    def test_unknown_stock_requires_missing_kardex_status(self):
        self.rows["inventory"][0]["stock_actual"] = ""
        with self.assertRaises(SOURCE.DatasetAuditError):
            self.load()

    def test_original_date_invalid_is_structured_audit_failure(self):
        self.rows["movements"][0]["fecha_original"] = "31/02/2025"
        with self.assertRaises(SOURCE.DatasetAuditError) as raised:
            self.load()
        self.assertIn("movement_inconsistent_metadata_or_dates", raised.exception.audit["fatal_reasons"])

    def test_monetary_inconsistency_preserved_and_warned(self):
        self.rows["movements"][0]["total_linea"] = "5,00"
        self.rows["demand"][0]["total_linea"] = "5,00"
        result = self.load()
        self.assertEqual(result["audit"]["sources"]["demand"]["line_arithmetic"]["mismatch_count"], 1)
        self.assertEqual(result["observations"][0]["quantity"], 2)
        self.assertFalse(result["metadata"]["monetary_evaluation"])

    def test_reproducible_hashes_and_no_nan(self):
        first = self.load()
        second = SOURCE.load_2025_bundle(*self.paths)
        self.assertEqual(first, second)
        json.dumps(first, allow_nan=False)
        self.assertEqual(first["metadata"]["sources"]["demand"]["sha256"], hashlib.sha256(self.paths[0].read_bytes()).hexdigest())
        self.assertNotIn(self.tmp.name, json.dumps(first))

    def test_reader_read_only_does_not_import_app_database_or_orders(self):
        self.write()
        before = {p: p.read_bytes() for p in self.paths}
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), patch("sqlite3.connect", side_effect=AssertionError("database forbidden")):
            SOURCE.load_2025_bundle(*self.paths)
        self.assertEqual(before, {p: p.read_bytes() for p in self.paths})
        self.assertNotIn("app.models.purchase_order", sys.modules)
        self.assertNotIn("app", sys.modules)

    def test_missing_header_and_empty_csv_rejected(self):
        self.write()
        self.paths[0].write_text("fecha,cantidad\n2025-01-02,2\n", encoding="utf-8")
        with self.assertRaises(SOURCE.DatasetAuditError):
            SOURCE.load_2025_bundle(*self.paths)
        self.paths[0].write_text(",".join(SOURCE.SCHEMAS["demand"].split()) + "\n", encoding="utf-8")
        with self.assertRaises(SOURCE.DatasetAuditError):
            SOURCE.load_2025_bundle(*self.paths)


if __name__ == "__main__":
    unittest.main(verbosity=2)
