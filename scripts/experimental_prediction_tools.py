"""Offline experimental CLI: synthetic demo or future input audit, never a final comparison."""
import argparse
import csv
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def load_service(name):
    """Load pure siblings without executing app/__init__.py or reading configuration."""
    package = "_offline_experimental_services"
    if package not in sys.modules:
        module = types.ModuleType(package)
        module.__path__ = [str(ROOT / "app/services")]
        sys.modules[package] = module
    qualified = package + "." + name
    if qualified not in sys.modules:
        spec = importlib.util.spec_from_file_location(qualified, ROOT / "app/services" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
    return sys.modules[qualified]


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def audit_inputs(development, external):
    contract = load_service("experimental_history_contract")
    result = contract.prepare_inputs(read_csv(development), read_csv(external))
    return {"status": result["status"], "final_comparison_executed": False,
            "unobserved_periods": result["unobserved_periods"],
            "partitions": {role: {"rows": len(result[role]["rows"]),
                                  "series": len(result[role]["series"]),
                                  "segments": sum(len(item["segments"]) for item in result[role]["series"])}
                           for role in ("development_history", "external_evaluation")}}


def demo():
    evaluation = load_service("experimental_evaluation_service")
    inventory = load_service("experimental_inventory_service")
    history = [0, 2, 0, 2, 0, 2, 1] * 2
    starts = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(history))]
    origin = starts[-1] + timedelta(days=1)
    targets = [origin + timedelta(days=i) for i in range(7)]
    candidate = evaluation.evaluate_adida(history, [1] * 7, training_dates=starts,
                                         target_dates=targets, aggregation_size=7)
    simulation = inventory.simulate_inventory(
        demand_series=[2, 2, 2], forecast_series=[[2], [2], [2]], initial_stock=2,
        lead_time_periods=0, review_period=1, stockout_mode="lost_sales",
        policy="forecast_order_up_to", policy_parameters={"target_stock": 0},
    )
    return {"data_kind": "synthetic", "purpose": "DEMO_fixture_only", "scientific_evidence": False,
            "candidate": candidate, "inventory": simulation}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="Run deterministic synthetic fixtures only")
    audit = commands.add_parser("audit-inputs", help="Validate future real CSV inputs, without fitting/scoring")
    audit.add_argument("--development", required=True)
    audit.add_argument("--external", required=True)
    args = parser.parse_args()
    result = demo() if args.command == "demo" else audit_inputs(args.development, args.external)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
