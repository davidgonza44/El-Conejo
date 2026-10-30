"""Summarize existing synthetic DEMO evidence without importing the application.

No database, Docker command, estimator or sealed experiment is invoked. A missing
after file produces a baseline report; it never invents unavailable measurements.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import statistics


CASE_FIELDS = ("implementation", "method", "pass", "product_id", "horizon")
RESULT_FIELDS = ("status", "selected", "forecast", "backtests", "unavailable_reason",
                 "blocked_folds", "requested_origins", "comparison_eligible")
METRICS = ("wape", "mae", "mase", "rmsse", "bias")


def case_index(report):
    result = {}
    for row in report["records"]:
        key = tuple(row[field] for field in CASE_FIELDS)
        if key in result:
            raise ValueError(f"Duplicate benchmark case: {key}")
        result[key] = row
    return result


class Comparison:
    """Compare scientific outputs, including all folds and unavailable states."""

    def __init__(self, atol, rtol):
        self.atol, self.rtol = atol, rtol
        self.failures = []
        self.numeric_values = 0
        self.max_absolute_delta = 0.0
        self.max_relative_delta = 0.0

    def compare(self, before, after, path):
        if type(before) is not type(after):
            return self.failures.append(f"{path}: different types")
        if isinstance(before, dict):
            return self._mapping(before, after, path)
        if isinstance(before, list):
            return self._sequence(before, after, path)
        if isinstance(before, float):
            return self._number(before, after, path)
        if before != after:
            self.failures.append(f"{path}: {before!r} != {after!r}")

    def _mapping(self, before, after, path):
        if before.keys() != after.keys():
            self.failures.append(f"{path}: different keys")
        for key in before.keys() & after.keys():
            self.compare(before[key], after[key], f"{path}.{key}")

    def _sequence(self, before, after, path):
        if len(before) != len(after):
            self.failures.append(f"{path}: different lengths")
        for index, (left, right) in enumerate(zip(before, after)):
            self.compare(left, right, f"{path}[{index}]")

    def _number(self, before, after, path):
        self.numeric_values += 1
        delta = abs(before - after)
        self.max_absolute_delta = max(self.max_absolute_delta, delta)
        relative = delta / max(abs(before), abs(after)) if before or after else 0.0
        self.max_relative_delta = max(self.max_relative_delta, relative)
        if not math.isclose(before, after, abs_tol=self.atol, rel_tol=self.rtol):
            self.failures.append(f"{path}: {before!r} != {after!r}")


def compare_reports(before, after, atol, rtol):
    comparison = Comparison(atol, rtol)
    for name in ("horizons", "arima_orders", "max_folds_unchanged", "metric_policy"):
        comparison.compare(before.get(name), after.get(name), name)
    for name in ("data_fingerprint", "measured_products", "eligible_products"):
        comparison.compare(before["coverage"].get(name), after["coverage"].get(name), name)
    comparison.compare(before["registry"]["live"], after["registry"]["live"], "registry.live")
    metadata_failures = list(comparison.failures)
    left, right = case_index(before), case_index(after)
    case_results = []
    for key in sorted(left.keys() | right.keys()):
        initial = len(comparison.failures)
        if key not in left or key not in right:
            comparison.failures.append(f"{key}: missing benchmark case")
        else:
            for field in RESULT_FIELDS:
                comparison.compare(left[key].get(field), right[key].get(field), f"{key}.{field}")
        case_results.append({**dict(zip(CASE_FIELDS, key)),
                             "equivalent": len(comparison.failures) == initial})
    return {"equivalent": not comparison.failures,
            "comparable": not metadata_failures and left.keys() == right.keys(),
            "metadata_failures": metadata_failures, "atol": atol, "rtol": rtol,
            "numeric_values": comparison.numeric_values,
            "max_absolute_delta": comparison.max_absolute_delta,
            "max_relative_delta": comparison.max_relative_delta,
            "failure_count": len(comparison.failures), "failures": comparison.failures,
            "cases": case_results}


def group_times(report, fields):
    grouped = defaultdict(list)
    for row in report["records"]:
        grouped[tuple(row[name] for name in fields)].append(row)
    return [{**dict(zip(fields, key)), "cases": len(rows),
             "seconds": sum(row["seconds"] for row in rows),
             "cache_hits": sum(row.get("cache_hits", 0) for row in rows),
             "cache_misses": sum(row.get("cache_misses", 0) for row in rows)}
            for key, rows in sorted(grouped.items())]


def selected_metrics(rows):
    folds = [fold["metrics"] for row in rows
             for fold in row["backtests"].get(row["selected"], {}).get("folds", [])]
    actual = sum(fold["actual_total"] for fold in folds)
    absolute = sum(fold["absolute_error_total"] for fold in folds)
    signed = sum(fold["signed_error_total"] for fold in folds)
    count = sum(fold["n"] for fold in folds)
    result = {"wape": 100 * absolute / actual if actual else None,
              "mae": absolute / count if count else None,
              "bias": signed / count if count else None, "folds": len(folds)}
    for name in ("mase", "rmsse"):
        available = [fold[name] for fold in folds if fold[name] is not None]
        result[name] = statistics.mean(available) if available else None
    return result


def metrics_table(report):
    grouped = defaultdict(list)
    for row in report["records"]:
        if row["implementation"] == "live" and row["pass"] == "cold":
            grouped[(row["method"], row["horizon"])].append(row)
    return [{"method": method, "horizon": horizon, **selected_metrics(rows),
             "winners": dict(Counter(row["selected"] for row in rows))}
            for (method, horizon), rows in sorted(grouped.items())]


def baseline_fit_counts(report):
    """Infer counts only for the old, entirely cold instrument and reconcile them."""
    if report.get("phase") != "before" or "events" in report:
        return {}
    records = [row for row in report["records"] if row["implementation"] == "live" and row["pass"] == "cold"]
    expected_misses = [sum(len(b["folds"]) for b in row["backtests"].values()) + 1 for row in records]
    if any(row["cache_hits"] or row["cache_misses"] != expected for row, expected in zip(records, expected_misses)):
        return {"supported": False}
    rows, expected, measured = [], Counter(), Counter()
    for row in records:
        for candidate, orders in (("arima", len(report["arima_orders"])), ("random_forest", 1)):
            folds = len(row["backtests"].get(candidate, {}).get("folds", []))
            final = int(row["selected"] == candidate)
            if not folds and not final:
                continue
            expected[candidate] += (folds + final) * orders
            rows.append({"requested_method": row["method"], "candidate": candidate,
                         "product_id": row["product_id"], "horizon": row["horizon"],
                         "fold_fits": folds * orders, "final_fits": final * orders})
    for row in report["profile"]:
        if row["implementation"] == "live" and row["stage"] == "fit" and row["pass"] == "cold":
            measured[row["method"]] += row["calls"]
    return {"supported": expected == measured, "measured": dict(measured), "reconstructed": dict(expected),
            "source": "reconstructed from records and reconciled with profile; no timestamps", "cases": rows}


def summarize(report):
    return {"complete": report["complete"], "coverage": report["coverage"],
            "seconds_total": report.get("seconds_total"), "machine": report["machine"],
            "times": group_times(report, ("implementation", "method", "pass", "horizon")),
            "products": group_times(report, CASE_FIELDS), "metrics": metrics_table(report),
            "profile": report["profile"], "raw_events_available": "events" in report,
            "events": report.get("events", []), "cache_sizes": report.get("cache_sizes", {}),
            "as_of": report.get("as_of", report["coverage"].get("as_of")),
            "reconstructed_fits": baseline_fit_counts(report)}


def _compare_option(comparison, base, case):
    for field in ("available", "order", "aic"):
        comparison.compare(base.get(field), case.get(field), field)
    for left, right in zip(base["forecasts"], case["forecasts"]):
        for field in ("horizon", "periods", "path", "metrics"):
            comparison.compare(left[field], right[field], field)
    comparison.compare(len(base["forecasts"]), len(case["forecasts"]), "forecast_count")


def summarize_options(report, atol, rtol):
    references, groups = {}, defaultdict(list)
    for row in report["records"]:
        default_arima = row["method"] == "arima" and row.get("thread_limit") == 8
        if default_arima or row.get("n_jobs") == 1:
            references[(row["method"], row["product_id"], row["origin"])] = row
        option = {key: row[key] for key in ("thread_limit", "low_memory", "maxiter", "n_jobs") if key in row}
        groups[(row["method"], json.dumps(option, sort_keys=True))].append(row)
    rows = []
    for (method, option), cases in sorted(groups.items()):
        comparison = Comparison(atol, rtol)
        for case in cases:
            base = references[(method, case["product_id"], case["origin"])]
            _compare_option(comparison, base, case)
        rows.append({"method": method, "option": json.loads(option), "cases": len(cases),
                     "seconds": sum(case["seconds"] for case in cases),
                     "fit_seconds": sum(case["fit_seconds"] for case in cases),
                     "predict_seconds": sum(f["predict_seconds"] for case in cases for f in case["forecasts"]),
                     "fits": sum(case["fits"] for case in cases),
                     "equivalent": not comparison.failures,
                     "max_absolute_delta": comparison.max_absolute_delta,
                     "errors": dict(Counter(c["error"] for case in cases
                                            for c in case.get("candidates", []) if c["error"])),
                     "failures": comparison.failures})
    return {"repetitions": report["repetitions"], "measured_products": report["measured_products"],
            "coverage": report["coverage"], "rows": rows}


def number(value):
    return "—" if value is None else f"{value:.6f}"


def table(lines, headers, rows):
    lines.extend(["", "| " + " | ".join(headers) + " |",
                  "| " + " | ".join("---" for _ in headers) + " |"])
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)


def timing_section(lines, before, after, comparison):
    lines.extend(["", "## Tiempo LIVE por método, horizonte y pasada", "",
                  "Segundos acumulados de los cinco productos elegibles. `hot` y `second` "
                  "usan memoria; `persisted` crea otra instancia de caché en el mismo proceso. "
                  "Esta última pasada no demuestra un reinicio HTTP de Gunicorn."])
    lookup = {(r["method"], r["pass"], r["horizon"]): r for r in after.get("times", [])
              if r["implementation"] == "live"}
    rows = []
    for row in before["times"]:
        if row["implementation"] != "live":
            continue
        key = row["method"], row["pass"], row["horizon"]
        later = lookup.get(key)
        elapsed = later["seconds"] if later else None
        speedup = row["seconds"] / elapsed if elapsed and comparison.get("comparable") else None
        state = "pendiente" if not later else ("sí" if comparison.get("equivalent") else "ver comparación")
        rows.append((*key, number(row["seconds"]), number(elapsed), number(speedup), state))
    table(lines, ("Método", "Pasada", "H", "Antes s", "Después s", "Speedup", "Equivalencia"), rows)


def profile_section(lines, before, after):
    lines.extend(["", "## Cálculo, fits y persistencia", "",
                  "Las etapas se solapan: `fit_predict` contiene `fit` y `predict`; "
                  "no se suman entre sí. En el baseline, `method` dentro de `predict` "
                  "identifica al candidato y mezcla el manual con auto. `cache_write` "
                  "identifica la solicitud. Solo `stage=fit` cuenta ajustes de estimadores; "
                  "`fit_predict` de un método simple no es un ajuste estadístico."])
    rows = []
    for label, report in (("antes", before), ("después", after)):
        for row in report.get("profile", []):
            if row.get("implementation") != "live" or row.get("pass") != "cold":
                continue
            if row["stage"] not in ("fit", "fit_predict", "cache_write", "cache_transaction", "features", "predict"):
                continue
            rows.append((label, row["method"], row["stage"], row.get("fit_kind") or "—",
                         row["calls"], number(row["seconds_total"]),
                         number(row["seconds_mean"]), json.dumps(row.get("errors", {}))))
    table(lines, ("Fase", "Método/candidato", "Etapa", "Tipo", "Llamadas", "Total s",
                  "Media s", "Errores"), rows)
    lines.extend(["", "El baseline no guardó eventos individuales: no es posible recuperar "
                  "el tiempo exacto de cada fold, ni separar sus fits entre manual y auto "
                  "a partir del perfil agregado. La media por fold es una media medida "
                  "de `fit_predict`, no el tiempo individual. Tampoco guardó bytes de "
                  "caché; el tamaño anterior queda sin evidencia en este archivo."])


def events_section(lines, after):
    events = after.get("events", [])
    if not events:
        return
    grouped = defaultdict(list)
    for event in events:
        if event["stage"] == "fit":
            key = event.get("requested_method"), event.get("method"), event.get("fit_kind")
            grouped[key].append(event)
    lines.extend(["", "## Ajustes individuales después", "",
                  "Conteos medidos mediante `stage=fit`, sin duplicar `fit_predict`. "
                  "El JSON resumen conserva los eventos con producto, horizonte, origen, "
                  "orden, duración y errores para consultar cada fold individualmente."])
    rows = [(request, candidate, kind, len(group), number(sum(e["seconds"] for e in group)))
            for (request, candidate, kind), group in grouped.items()]
    table(lines, ("Solicitud", "Candidato", "Tipo", "Fits", "Segundos"), rows)
    transactions = [event for event in events if event["stage"] == "cache_transaction"]
    if transactions:
        lines.extend(["", f"Transacciones de caché medidas después: {len(transactions)}; "
                      f"entradas: {sum(e.get('rows', 0) for e in transactions)}; "
                      f"bytes aproximados de los valores JSON: {sum(e.get('bytes', 0) for e in transactions)}. "
                      "Estos bytes excluyen claves, índices SQLite y archivos auxiliares."])
    if after.get("cache_sizes"):
        lines.extend(["", "Tamaños de los archivos de caché registrados después:", "",
                      "```json", json.dumps(after["cache_sizes"], indent=2, sort_keys=True), "```"])


def reconstructed_fits_section(lines, before):
    counts = before.get("reconstructed_fits", {})
    if not counts.get("supported"):
        return
    grouped = defaultdict(Counter)
    for row in counts["cases"]:
        grouped[(row["requested_method"], row["candidate"])].update(
            fold=row["fold_fits"], final=row["final_fits"])
    lines.extend(["", "## Fits baseline reconstruidos por solicitud", "",
                  "Conteos reconstruidos desde registros + perfil, sin timestamps. Todos los "
                  "casos fríos tuvieron cero hits: 13 misses por manual (12 folds y un final), "
                  "73 por auto (6 candidatos por 12 folds y un final). Cada ajuste ARIMA "
                  "intenta sus 6 órdenes; RF ajusta un bosque. Los totales coinciden "
                  "exactamente con stage=fit. El JSON conserva el desglose por producto/horizonte."])
    rows = [(request, candidate, count["fold"], count["final"], sum(count.values()))
            for (request, candidate), count in sorted(grouped.items())]
    table(lines, ("Solicitud", "Candidato", "Fits folds", "Fits final", "Total"), rows)
    lines.extend(["", "Los 30 ConvergenceWarning del baseline corresponden a órdenes "
                  "candidatos descartados dentro de ARIMA; están incluidos en los intentos "
                  "medidos. No implican repetir el fold: todos sus pronósticos completaron. "
                  "Los tiempos de cada intento no se pueden reconstruir."])


def options_section(lines, options):
    if not options:
        return
    lines.extend(["", "## Sonda de opciones de estimadores", "",
                  f"{options['measured_products']} productos sintéticos, historial final y un "
                  f"origen común anterior a H90; {options['repetitions']} repetición por caso. "
                  "Cada fit calcula H7/H30/H90. Se mantienen 6 órdenes ARIMA y maxiter=50; "
                  "RF conserva 50 árboles, profundidad 6 y random_state=42. RF usa BLAS=1. "
                  "La preparación de features se comparte y queda fuera de estos tiempos."])
    rows = [(row["method"], json.dumps(row["option"], sort_keys=True), row["fits"],
             number(row["seconds"]), number(row["fit_seconds"]), number(row["predict_seconds"]),
             row["equivalent"], f"{row['max_absolute_delta']:.3g}") for row in options["rows"]]
    table(lines, ("Modelo", "Opción", "Fits", "Total s", "Fit s", "Predict s", "Equivalente", "Máximo delta"), rows)
    lines.extend(["", "El delta mostrado incluye AIC además de pronósticos y métricas. "
                  "BLAS=1 y RF n_jobs=1 obtuvieron el menor tiempo agregado entre los "
                  "límites probados. La sonda es acotada y no sustituye el benchmark completo.", "",
                  "low_memory=True preservó los pronósticos y métricas de esta muestra. "
                  "Su reducción agregada frente a BLAS=1/False fue modesta y heterogénea "
                  "entre casos. Con una repetición y orden secuencial fijo no se puede "
                  "estimar varianza de ejecución ni atribuir con firmeza esa mejora. "
                  "Esta evidencia por sí sola no justifica cambiar low_memory ni maxiter."])


def metrics_section(lines, before, after):
    lines.extend(["", "## Métricas antes/después", "",
                  "WAPE/MAE/Bias agrupados con las sumas de error y demanda de todos los "
                  "folds; MASE/RMSSE son medias de folds con denominador calculado solo "
                  "en entrenamiento. Para auto se muestran los ganadores de cada producto. "
                  "La comprobación científica adicional compara todos los candidatos, "
                  "folds, pronósticos, estados y ganadores individualmente."])
    lookup = {(row["method"], row["horizon"]): row for row in after.get("metrics", [])}
    rows = []
    for row in before["metrics"]:
        later = lookup.get((row["method"], row["horizon"]), {})
        values = [f"{number(row[name])} / {number(later.get(name))}" for name in METRICS]
        rows.append((row["method"], row["horizon"], *values))
    table(lines, ("Método", "H", *[name.upper() + " antes / después" for name in METRICS]), rows)
    winners = [(row["horizon"], json.dumps(row["winners"], sort_keys=True),
                json.dumps(lookup.get(("auto", row["horizon"]), {}).get("winners")))
               for row in before["metrics"] if row["method"] == "auto"]
    table(lines, ("H auto", "Ganadores antes", "Ganadores después"), winners)


def product_section(lines, before, after):
    lines.extend(["", "## Tiempo frío por producto", "",
                  "Se identifican únicamente IDs de los productos sintéticos DEMO. "
                  "Las restantes pasadas por producto están en el JSON resumen."])
    lookup = {tuple(row[name] for name in CASE_FIELDS): row for row in after.get("products", [])}
    rows = []
    for row in before["products"]:
        if row["implementation"] != "live" or row["pass"] != "cold":
            continue
        later = lookup.get(tuple(row[name] for name in CASE_FIELDS), {})
        rows.append((row["method"], row["product_id"], row["horizon"],
                     number(row["seconds"]), number(later.get("seconds"))))
    table(lines, ("Método", "Producto", "H", "Antes s", "Después s"), rows)


def render(payload):
    before, after = payload["before"], payload.get("after", {})
    comparison = payload.get("comparison", {})
    coverage = before["coverage"]
    lines = ["# Continuación: evidencia computacional DEMO", "",
             f"Baseline completo: {before['complete']}; productos elegibles medidos: "
             f"{coverage['measured_products']}/{coverage['eligible_products']}. "
             f"Tiempo total del proceso: {number(before['seconds_total'])} s.", "",
             "Fuente exclusivamente DEMO sintética; este resumen no ejecuta cálculos, "
             "no consulta una base de datos y no modifica resultados experimentales cerrados. "
             "Reutiliza el baseline y la sonda de opciones finalizados; no los repite.", "",
             f"Fingerprint del baseline: `{coverage['data_fingerprint']}`.", "",
             f"Tolerancia: abs(a-b) <= max({payload['atol']}, "
             f"{payload['rtol']} * max(abs(a), abs(b))). "
             "IDs, conteos, conjuntos de casos, estados, orígenes y ganadores se exigen exactos."]
    if comparison:
        lines.extend(["", f"Fingerprint después: `{after['coverage']['data_fingerprint']}`; "
                      f"fecha de corte explícita: {after.get('as_of') or 'no registrada'}. "
                      f"Comparabilidad de datos/metodología/casos: **{comparison['comparable']}**.", "",
                      f"Equivalencia: **{comparison['equivalent']}**; "
                      f"valores flotantes comparados: {comparison['numeric_values']}; "
                      f"diferencias fuera de tolerancia o estructurales: {comparison['failure_count']}; "
                      f"delta absoluto máximo: {comparison['max_absolute_delta']:.12g}."])
    else:
        lines.extend(["", "Después: pendiente. No se atribuye speedup ni equivalencia sin evidencia."])
    timing_section(lines, before, after, comparison)
    profile_section(lines, before, after)
    reconstructed_fits_section(lines, before)
    events_section(lines, after)
    options_section(lines, payload.get("options"))
    metrics_section(lines, before, after)
    product_section(lines, before, after)
    if comparison.get("failures"):
        lines.extend(["", "## Diferencias que requieren revisión", "",
                      "Se muestran las primeras 30; el JSON contiene la lista completa.", ""])
        lines.extend(f"- {item}" for item in comparison["failures"][:30])
    return "\n".join(lines) + "\n"


def load(path):
    report = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if report.get("complete") is not True:
        raise ValueError(f"Incomplete benchmark: {Path(path).name}")
    case_index(report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True)
    parser.add_argument("--after")
    parser.add_argument("--options", help="Optional complete model-options probe JSON")
    parser.add_argument("--output", required=True, help="Markdown report")
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--atol", type=float, default=1e-8)
    parser.add_argument("--rtol", type=float, default=1e-7)
    args = parser.parse_args()
    if min(args.atol, args.rtol) < 0 or not all(map(math.isfinite, (args.atol, args.rtol))):
        parser.error("Tolerances must be finite and nonnegative")
    inputs = {Path(path).resolve() for path in (args.before, args.after, args.options) if path}
    outputs = {Path(args.output).resolve(), Path(args.json_output).resolve()}
    if len(outputs) != 2 or inputs & outputs:
        parser.error("Report outputs must differ from each other and from evidence inputs")
    before = load(args.before)
    payload = {"atol": args.atol, "rtol": args.rtol, "before": summarize(before)}
    if args.options:
        options = json.loads(Path(args.options).read_text(encoding="utf-8-sig"))
        if options.get("complete") is not True:
            parser.error("Incomplete estimator-options probe")
        payload["options"] = summarize_options(options, args.atol, args.rtol)
    if args.after:
        after = load(args.after)
        payload.update(after=summarize(after),
                       comparison=compare_reports(before, after, args.atol, args.rtol))
    Path(args.output).write_text(render(payload), encoding="utf-8")
    Path(args.json_output).write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    equivalent = payload.get("comparison", {}).get("equivalent")
    print(json.dumps({"baseline_cases": len(before["records"]), "equivalent": equivalent,
                      "failure_count": payload.get("comparison", {}).get("failure_count")}))
    return 1 if equivalent is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
