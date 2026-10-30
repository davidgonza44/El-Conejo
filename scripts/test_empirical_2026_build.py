"""Synthetic data-only checks: no models, database, source CSVs or application factory."""
import copy
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_empirical_2026 as builder


class EmpiricalBuildTests(unittest.TestCase):
    def setUp(self):
        self.calendar = builder.business_calendar()
        self.observations = {
            "0808": [(date(2026, 5, 2), Decimal("0.1")),
                     (date(2026, 5, 2), Decimal("0.2")),
                     (date(2026, 5, 4), Decimal("2.000001"))],
            "0012": [(date(2026, 9, 18), Decimal("1.5"))],
            "VARIOS": [(date(2026, 1, 5), Decimal("1"))],
        }
        self.cohorts = {"STRICT": {"0808"}, "SENSITIVITY": {"0012"}, "QUARANTINE": {"VARIOS"}}
        self.manifest = builder.make_manifest(
            {"0808": "FLETE", "0012": "VARIANTE", "VARIOS": "VARIOS"}, self.cohorts,
            {"VARIOS"}, set(), {"0012": "T"}, set())
        self.rows = builder.make_series(self.manifest, self.observations, self.calendar, "STRICT")

    def validate(self, rows=None):
        return builder.validate_series(self.rows if rows is None else rows, self.manifest,
                                       self.observations, self.calendar, "STRICT")

    def test_calendar_216_and_business_exceptions(self):
        self.assertEqual(len(self.calendar), 216)
        self.assertEqual({day for day in self.calendar if day.weekday() == 6}, builder.OPENINGS)
        self.assertFalse(set(self.calendar) & builder.CLOSURES)
        self.assertEqual((self.calendar[0], self.calendar[-1]), (builder.START, builder.END))

    def test_regular_sunday_and_extraordinary_closure_absent(self):
        self.assertNotIn(date(2026, 1, 11), self.calendar)
        self.assertNotIn(date(2026, 1, 10), self.calendar)
        self.assertIn(date(2026, 1, 12), self.calendar)

    def test_calendar_comparison_rejects_missing_or_extra_dates(self):
        builder.validate_calendar(self.calendar, set(self.calendar))
        with self.assertRaisesRegex(ValueError, "canonical-only"):
            builder.validate_calendar(self.calendar, set(self.calendar) | {date(2026, 1, 11)})
        with self.assertRaisesRegex(ValueError, "Calendar-only"):
            builder.validate_calendar(self.calendar, set(self.calendar[1:]))

    def test_left_censoring_and_operational_zero(self):
        self.assertEqual(self.rows[0]["fecha_real"], "2026-05-02")
        self.assertEqual(self.rows[0]["product_operational_index"], "1")
        sunday = next(row for row in self.rows if row["fecha_real"] == "2026-05-03")
        self.assertEqual(sunday["es_cero_observado"], "true")
        self.assertEqual(sunday["demanda_observada"], "0")
        monday = next(row for row in self.rows if row["fecha_real"] == "2026-05-11")
        self.assertEqual(monday["es_cero_observado"], "true")
        self.assertEqual(self.validate()["left_censoring_violations"], 0)

    def test_exact_decimal_and_daily_positive_conservation(self):
        self.assertEqual(Decimal(self.rows[0]["demanda_observada"]), Decimal("0.3"))
        self.assertEqual(sum((Decimal(row["demanda_observada"]) for row in self.rows), Decimal(0)),
                         Decimal("2.300001"))
        check = self.validate()
        self.assertEqual(check["positive_missing"], 0)
        self.assertEqual(check["positive_additional"], 0)
        self.assertEqual(check["maximum_absolute_error"], "0")

    def test_global_and_product_indices_with_closure(self):
        observations = {"0808": [(date(2026, 1, 9), Decimal(1))]}
        rows = builder.make_series(self.manifest, observations, self.calendar, "STRICT")
        self.assertEqual([row["fecha_real"] for row in rows[:2]], ["2026-01-09", "2026-01-12"])
        self.assertEqual([row["operational_index"] for row in rows[:2]], ["5", "6"])
        self.assertEqual([row["product_operational_index"] for row in rows[:2]], ["1", "2"])

    def test_leading_zero_and_nonnumeric_codes_preserved(self):
        parsed = builder.parsed_csv(builder.csv_bytes(builder.MANIFEST_FIELDS, self.manifest))
        self.assertEqual([row["codigo_producto"] for row in parsed], ["0012", "0808", "VARIOS"])

    def test_service_0808_only_and_no_replenishment(self):
        rows = {row["codigo_producto"]: row for row in self.manifest}
        self.assertEqual(rows["0808"]["is_service"], "true")
        self.assertEqual(rows["0808"]["incluido_pronostico"], "true")
        self.assertEqual(rows["0808"]["incluido_reabastecimiento"], "false")
        self.assertEqual(rows["0012"]["is_service"], "false")
        self.assertEqual(rows["VARIOS"]["incluido_pronostico"], "false")

    def test_cohort_complement_exact_and_exhaustive(self):
        cohorts = builder.partition(set(self.observations), {"VARIOS"}, set(), set(), {"0012"},
                                    {"STRICT": 1, "SENSITIVITY": 1, "QUARANTINE": 1})
        self.assertEqual(cohorts, self.cohorts)

    def test_cohort_overlap_unknown_and_wrong_count_fail(self):
        expected = {"STRICT": 1, "SENSITIVITY": 1, "QUARANTINE": 1}
        for changed, sensitivity, counts in (({"VARIOS"}, {"VARIOS"}, expected),
                                              ({"UNKNOWN"}, {"0012"}, expected),
                                              ({"VARIOS"}, {"0012"}, builder.EXPECTED)):
            with self.subTest(changed=changed, sensitivity=sensitivity, counts=counts):
                with self.assertRaises(ValueError):
                    builder.partition(set(self.observations), changed, set(), set(), sensitivity, counts)

    def test_ruling_parser_keeps_nonnumeric_codes_and_rejects_duplicates(self):
        changed = ["VARIOS"] + [f"C{i:03}" for i in range(52)]
        review = [f"R{i:03}" for i in range(344)]
        units = [(f"T{i:03}", "T") for i in range(52)] + [(f"N{i:03}", "N") for i in range(9)] + [("3611", "M")]
        text = "## 3.2 Lista completa\n" + "\n".join(f"| `{code}` | closed |" for code in changed)
        text += "\n### Ejemplos\n## 3.3 Lista completa\n" + "\n".join(f"| `{code}` | closed |" for code in review)
        text += "\n# 4. Elegibilidad de unidades\n## 4.2 Perfiles\n"
        text += "\n".join(f"| `{code}` | metadata | {state} |" for code, state in units)
        text += "\n## 4.3 Cuatro alertas\n" + "\n".join(f"| `{code}` | pending |" for code in ("0878", "1116", "1388", "1858")) + "\n# 5."
        parsed = builder.parse_ruling(text)
        self.assertEqual(parsed[0], set(changed))
        self.assertEqual(len(parsed[1]), 344)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            builder.parse_ruling(text.replace("### Ejemplos", "| `VARIOS` | duplicate |\n### Ejemplos"))

    def test_unit_and_integrity_reasons_combined_without_reclassification(self):
        rows = builder.make_manifest({"0716": "X", "2093": "Y", "0878": "Z"},
                                     {"QUARANTINE": {"0716", "2093", "0878"}}, {"2093"}, set(),
                                     {"0716": "T", "2093": "N"}, {"0878"})
        indexed = {row["codigo_producto"]: row for row in rows}
        self.assertIn("DOCUMENTO_INTEGRIDAD_PENDIENTE", indexed["0716"]["motivo"])
        self.assertIn("MULTIUNIDAD_NO_RESUELTA", indexed["2093"]["motivo"])
        self.assertIn("CAMBIO_CLARO_DE_ARTICULO", indexed["2093"]["motivo"])
        self.assertEqual(indexed["0878"]["estado_unidad"], "UNIDAD_SIMPLE")

    def test_invalid_canonical_quantities_fail(self):
        for raw in ("NaN", "inf", "-1", "0", "1.0000001", "1,2.3"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                builder.decimal_quantity(raw)
        self.assertEqual(builder.decimal_quantity("1,000001"), Decimal("1.000001"))

    def test_readback_rejects_tampering(self):
        for field, value in (("demanda_observada", "NaN"), ("demanda_observada", "Infinity"),
                             ("demanda_observada", "-1"), ("demanda_observada", "0.4"),
                             ("operational_index", "1"), ("product_operational_index", "2"),
                             ("es_cero_observado", "true"), ("codigo_producto", "808"),
                             ("fecha_real", "2026-05-10")):
            with self.subTest(field=field, value=value):
                rows = copy.deepcopy(self.rows)
                rows[0][field] = value
                with self.assertRaises(ValueError):
                    self.validate(rows)

    def test_readback_rejects_missing_extra_duplicate_positive(self):
        with self.assertRaises(ValueError):
            self.validate(self.rows[1:])
        with self.assertRaises(ValueError):
            self.validate(self.rows + [self.rows[-1]])
        rows = copy.deepcopy(self.rows)
        rows[1]["demanda_observada"], rows[1]["es_cero_observado"] = "1", "false"
        with self.assertRaisesRegex(ValueError, "Positive keys"):
            self.validate(rows)

    def test_sensitivity_separate_and_no_quarantine_rows(self):
        rows = builder.make_series(self.manifest, self.observations, self.calendar, "SENSITIVITY")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["codigo_producto"], "0012")
        self.assertNotIn("VARIOS", {row["codigo_producto"] for row in self.rows + rows})

    def test_deterministic_bytes_and_non_overwrite(self):
        root = Path(__file__).resolve().parents[1] / ".tmp"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="empirical-builder-test-", dir=root) as temporary:
            output = Path(temporary)
            artifacts = {"manifest.csv": builder.csv_bytes(builder.MANIFEST_FIELDS, self.manifest),
                         "series.csv": builder.csv_bytes(builder.SERIES_FIELDS, self.rows)}
            builder.write_new_or_identical(output, artifacts)
            before = builder.hashes({name: output / name for name in artifacts})
            builder.write_new_or_identical(output, artifacts)
            self.assertEqual(before, builder.hashes({name: output / name for name in artifacts}))
            with self.assertRaisesRegex(ValueError, "Existing output differs"):
                builder.write_new_or_identical(output, {"new.csv": b"new", "series.csv": b"changed"})
            self.assertFalse((output / "new.csv").exists())
            self.assertEqual(before, builder.hashes({name: output / name for name in artifacts}))

    def test_descriptive_summary_has_no_cross_product_quantity_sum(self):
        summary = builder.statistics_for(self.rows)
        self.assertEqual(summary["products"], 1)
        self.assertEqual(summary["positive_rows"], 2)
        self.assertEqual(summary["zero_rows"], len(self.rows) - 2)
        self.assertNotIn("total_quantity", summary)


if __name__ == "__main__":
    unittest.main(verbosity=2)
