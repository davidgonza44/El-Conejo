"""Pure real-2026 package checks: synthetic bytes, no Flask, files or database."""
from copy import deepcopy
import csv
from datetime import timedelta
from decimal import Decimal
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch


_PATH = Path(__file__).resolve().parents[1] / "app/services/real_2026_source_service.py"
_SPEC = importlib.util.spec_from_file_location("real2026_pure_source", _PATH)
source = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(source)


def csv_bytes(fields, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def calendar_days():
    days = [source.START + timedelta(days=index)
            for index in range((source.END - source.START).days + 1)]
    return [day for day in days if (day.weekday() != 6 and day not in source._CLOSURES)
            or day in source._OPENINGS]


def rewrite_report(files):
    report = json.loads(files[source.REPORT_FILE])
    report["artifact_sha256"] = {name: source._digest(files[name]) for name in (
        source.MANIFEST_FILE, source.CALENDAR_FILE, *source.SERIES_FILES.values())}
    report["source_sha256_before"][source.CATALOG_FILE] = source._digest(files[source.CATALOG_FILE])
    report["source_sha256_after"] = dict(report["source_sha256_before"])
    files[source.REPORT_FILE] = json.dumps(report, sort_keys=True).encode("utf-8")


def synthetic_package():
    days = calendar_days()
    identities = (("0001", "STRICT", False), ("0808", "STRICT", True),
                  ("0002", "SENSITIVITY", False), ("0003", "QUARANTINE", False))
    catalog, manifest = [], []
    series = {cohort: [] for cohort in source.SERIES_FILES}
    for code, cohort, service in identities:
        name = "Producto sintético " + code
        included = cohort != "QUARANTINE"
        catalog.append({"codigo": code, "nombre": name, "unidad_deventa_a": "UND",
                        "costo": "dato no persistido"})
        manifest.append(dict(zip(source.MANIFEST_FIELDS, (
            code, name, cohort, "CONTINUIDAD_PLAUSIBLE" if included else "REVISAR_IDENTIDAD",
            "VARIANTE_TEXTUAL_PLAUSIBLE" if cohort == "SENSITIVITY" else "UNIDAD_SIMPLE",
            str(service).lower(), str(included).lower(), str(included and not service).lower(),
            "sin motivo semántico pendiente" if included else "Identidad pendiente"))))
        if included:
            for index, day in enumerate(days[-2:], 1):
                amount = "0.100001" if index == 1 else "0"
                series[cohort].append(dict(zip(source.SERIES_FIELDS, (
                    code, name, cohort, str(service).lower(), day.isoformat(),
                    str(days.index(day) + 1), str(index), amount, str(index == 2).lower()))))
    files = {
        source.CATALOG_FILE: csv_bytes(("codigo", "nombre", "unidad_deventa_a", "costo"), catalog),
        source.MANIFEST_FILE: csv_bytes(source.MANIFEST_FIELDS, manifest),
        source.CALENDAR_FILE: csv_bytes(source.CALENDAR_FIELDS, [dict(zip(source.CALENDAR_FIELDS, (
            day.isoformat(), str(index), "DIA_OPERATIVO_NORMAL", "Calendario sintético")))
            for index, day in enumerate(days, 1)]),
    }
    files.update({source.SERIES_FILES[cohort]: csv_bytes(source.SERIES_FIELDS, rows)
                  for cohort, rows in series.items()})
    report = {"READY_FOR_BACKTEST": True, "cohort_counts": {"STRICT": 2, "SENSITIVITY": 1, "QUARANTINE": 1},
              "source_sha256_before": {"demanda_limpia_2026.csv": source._digest(b"original sintetico")},
              "protocol": {"start": source.START.isoformat(), "cutoff": source.END.isoformat()}}
    files[source.REPORT_FILE] = json.dumps(report).encode("utf-8")
    rewrite_report(files)
    return files


def change_row(files, filename, index, **changes):
    reader = csv.DictReader(io.StringIO(files[filename].decode("utf-8")))
    rows = list(reader)
    rows[index].update(changes)
    files[filename] = csv_bytes(reader.fieldnames, rows)
    rewrite_report(files)


class Real2026SourceTests(unittest.TestCase):
    def setUp(self):
        self.files = synthetic_package()

    def parse(self, files=None):
        return source.parse_package(self.files if files is None else files, allow_revision=True)

    def test_frozen_pin_required_for_initial_package(self):
        with self.assertRaisesRegex(ValueError, "referencia"):
            source.parse_package(self.files)
        with patch.object(source, "BUILD_SHA256", source._digest(self.files[source.REPORT_FILE])):
            result = source.parse_package(self.files)
        self.assertTrue(result["provenance"]["reference_match"])

    def test_exact_decimal_original_codes_and_isolated_fields(self):
        before = deepcopy(self.files)
        result = self.parse()
        products = {product["code"]: product for product in result["products"]}
        self.assertEqual(products["0001"]["points"][0][1], Decimal("0.100001"))
        self.assertEqual(products["0001"]["unit"], "UND")
        self.assertEqual(products["0003"]["points"], [])
        self.assertTrue(products["0808"]["is_service"])
        self.assertEqual(set(products["0001"]), {"code", "name", "unit", "cohort", "identity_status",
                                                "unit_status", "is_service", "included", "reason", "points"})
        self.assertEqual(result["summary"]["total_points"], 6)
        self.assertFalse(result["provenance"]["reference_match"])
        self.assertEqual(result["provenance"]["observation_basis"], "validated_revision_operational_suffix")
        self.assertIn("demanda_limpia_2026.csv", result["provenance"]["missing_originals"])
        self.assertEqual(before, self.files)
        json.dumps(result["summary"])

    def test_optional_original_only_changes_provenance(self):
        first = self.parse()
        self.files["demanda_limpia_2026.csv"] = b"original sintetico"
        second = self.parse()
        self.assertEqual(first["package_hash"], second["package_hash"])
        self.assertIn("demanda_limpia_2026.csv", second["provenance"]["provided_originals"])
        self.files["demanda_limpia_2026.csv"] = b"original diferente"
        with self.assertRaisesRegex(ValueError, "fuente original"):
            self.parse()

    def test_receipt_manifest_matches_all_four_originals(self):
        names = (source.CATALOG_FILE, "demanda_limpia_2026.csv", "movimientos_detalle_2026.csv", "existencias_2026_final.csv")
        report = json.loads(self.files[source.REPORT_FILE])
        for name in names[1:]:
            self.files[name] = b"original sintetico"
            report["source_sha256_before"][name] = source._digest(self.files[name])
        self.files[source.REPORT_FILE] = json.dumps(report).encode()
        rewrite_report(self.files)
        entries = [{"archivo_del_zip": name, "tamano_bytes": len(self.files[name]),
                    "sha256": source._digest(self.files[name])} for name in names]
        self.files[source.RECEIPT_FILE] = json.dumps(entries).encode()
        result = self.parse()
        self.assertTrue(result["provenance"]["receipt_manifest_verified"])
        entries[0]["sha256"] = "0" * 64
        self.files[source.RECEIPT_FILE] = json.dumps(entries).encode()
        with self.assertRaisesRegex(ValueError, "manifiesto SHA-256"):
            self.parse()

    def test_repeated_day_or_gap_is_rejected(self):
        name = source.SERIES_FILES["STRICT"]
        change_row(self.files, name, 1, fecha_real=calendar_days()[-2].isoformat(), operational_index="215")
        with self.assertRaisesRegex(ValueError, "duplicadas"):
            self.parse()

    def test_absent_last_observation_is_not_filled(self):
        name = source.SERIES_FILES["STRICT"]
        reader = csv.DictReader(io.StringIO(self.files[name].decode()))
        rows = [row for row in reader if not (row["codigo_producto"] == "0001"
                                             and row["product_operational_index"] == "2")]
        self.files[name] = csv_bytes(source.SERIES_FIELDS, rows)
        rewrite_report(self.files)
        with self.assertRaisesRegex(ValueError, "período completo"):
            self.parse()

    def test_leading_zero_or_wrong_amount_is_rejected(self):
        name = source.SERIES_FILES["STRICT"]
        change_row(self.files, name, 0, demanda_observada="0", es_cero_observado="true")
        with self.assertRaisesRegex(ValueError, "primera demanda"):
            self.parse()
        for amount in ("NaN", "Infinity", "-1", "0.0000001", "1,5"):
            with self.subTest(amount=amount):
                with self.assertRaisesRegex(ValueError, "cantidad"):
                    source._quantity(amount)

    def test_wrong_unit_or_foreign_series_cohort_is_rejected(self):
        change_row(self.files, source.MANIFEST_FILE, 0, estado_unidad="VARIANTE_TEXTUAL_PLAUSIBLE")
        with self.assertRaisesRegex(ValueError, "unidad"):
            self.parse()
        self.files = synthetic_package()
        change_row(self.files, source.SERIES_FILES["STRICT"], 0, cohorte="SENSITIVITY")
        with self.assertRaisesRegex(ValueError, "cohorte"):
            self.parse()

    def test_catalog_name_and_hash_tampering_are_rejected(self):
        self.files[source.MANIFEST_FILE] += b"changed"
        with self.assertRaisesRegex(ValueError, "hash"):
            self.parse()
        self.files = synthetic_package()
        change_row(self.files, source.MANIFEST_FILE, 0, nombre_actual="Otro producto")
        with self.assertRaisesRegex(ValueError, "mismo producto"):
            self.parse()

    def test_calendar_protocol_and_service_replenishment_flags_are_checked(self):
        change_row(self.files, source.CALENDAR_FILE, 0, fecha_real="2026-01-04")
        with self.assertRaisesRegex(ValueError, "216 días"):
            self.parse()
        self.files = synthetic_package()
        change_row(self.files, source.MANIFEST_FILE, 1, incluido_reabastecimiento="true")
        with self.assertRaisesRegex(ValueError, "inclusión"):
            self.parse()

    def test_file_and_daily_limits_and_safe_errors(self):
        with patch.object(source, "MAX_TOTAL_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "64 MiB"):
                self.parse()
        with patch.object(source, "MAX_DAILY_POINTS", 5):
            with self.assertRaisesRegex(ValueError, "observaciones"):
                self.parse()
        self.files["../../private-marker"] = b"sensitive-marker"
        with self.assertRaises(ValueError) as caught:
            self.parse()
        self.assertNotIn("private-marker", str(caught.exception))
        self.assertNotIn("sensitive-marker", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
