"""DEMO acceptance: read-only business checks, real HTTP login and LIVE pipeline.

Only authentication, the predictive cache and the private verification snapshot
are written. Never calls experimental thesis scripts or regenerates their results.
"""
import argparse
import csv
from io import StringIO
import json
import os
from pathlib import Path
import time
from sqlalchemy import inspect, text
from docker_support.guard import validate_demo, wait_database
from docker_support.bootstrap import engine, verify_installation
from docker_support.verify import check, fingerprint, login


def verify_pipeline(app):
    from app.services.predictive_analysis_service import analyze
    from app.services.predictive_job_service import get_jobs
    from docker_support.demo_data import ANCHOR

    with app.app_context():
        print("Calculando pipeline LIVE real (backtest, selección, pronóstico y reabastecimiento)...", flush=True)
        deadline = time.monotonic() + 900
        analysis = analyze(defer_predictions=True, release_session=True)
        while analysis.computation_status != "ready":
            if analysis.computation_status == "error" or time.monotonic() > deadline:
                raise AssertionError("Falló el pipeline LIVE de la DEMO")
            print("Esperando pipeline LIVE y caché persistente...", flush=True)
            get_jobs(app).wait(30)
            analysis = analyze(defer_predictions=True, release_session=True)
        check(len(analysis.with_model) >= 5, "al menos cinco productos con modelos calculados")
        check(len(analysis.evaluated) >= 5, "precisión con pares de backtest reales")
        check(sum(i.recommendation is not None and i.recommendation.suggested_quantity > 0
                  for i in analysis.items) >= 5, "reabastecimiento con compras sugeridas")
        check(sum(i.risk.level == "critical" for i in analysis.items) >= 5, "cinco productos críticos")
        # Fixed fixture assertions remain reproducible; live windows use the real clock.
        fixed = analyze(today=ANCHOR, observed_only=True, release_session=True)
        by_code = {i.product.code: i for i in fixed.items}
        for code, status in (("DEMO-002", "stable"), ("DEMO-003", "up"), ("DEMO-004", "down")):
            check(by_code[code].trend.status == status, code + " tendencia " + status)
        check(by_code["DEMO-006"].eligibility.status == "INTERMITTENT", "demanda intermitente identificada")
        check(by_code["DEMO-046"].eligibility.status == "INACTIVE", "producto sin movimiento reciente")
        print(json.dumps({"forecast_products": len(analysis.with_forecast), "evaluated_products": len(analysis.evaluated),
                          "models": analysis.models_used()}, ensure_ascii=False), flush=True)


def verify_exports(client):
    for page in ("demand_forecast", "replenishment", "critical_products", "trends", "model_accuracy"):
        response = wait_export(client, page)
        rows = list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
        check(len(rows) > 1 and len(rows[0]) > 3 and "DEMO" in response.content.decode("utf-8-sig"),
              "CSV predictivo con datos " + page)
    with engine().connect() as connection:
        note_ids = connection.execute(text("SELECT id FROM delivery_notes ORDER BY id")).scalars().all()
    for note_id in note_ids:
        pdf = client.call("GET", f"/api/delivery-notes/{note_id}/pdf")
        check(pdf.content.startswith(b"%PDF") and len(pdf.content) > 1000, f"PDF nota {note_id}")


def wait_export(client, page):
    from docker_support.verify import BASE
    deadline = time.monotonic() + 900
    last_notice = 0
    while True:
        response = client.session.get(BASE + f"/api/predictions/export/{page}?trend=all", timeout=180)
        if response.status_code != 202:
            check(response.status_code == 200 and "csv" in response.headers.get("Content-Type", ""),
                  "exportación disponible " + page)
            return response
        if response.json()["status"] not in ("pending", "busy"):
            raise AssertionError("Estado inesperado del pipeline")
        if time.monotonic() > deadline:
            raise AssertionError("El pipeline DEMO no terminó en 15 minutos: " + page)
        if time.monotonic() - last_notice > 30:
            print("Esperando cálculo LIVE: " + page, flush=True)
            last_notice = time.monotonic()
        time.sleep(3)


def verify_pages(client):
    redirect = client.call("GET", "/reports", 302)
    check(redirect.headers["Location"] == "/dashboard", "reportes conserva su redirección")
    for path in ("/dashboard", "/products", "/categories", "/inventory", "/sales", "/sales/list",
                 "/delivery-notes", "/purchases", "/purchases/receptions", "/reports/sales",
                 "/predictive/demand", "/predictive/replenishment", "/predictive/critical",
                 "/predictive/trends", "/predictive/accuracy", "/reconciliation", "/historical-imports"):
        response = client.call("GET", path)
        check("text/html" in response.headers["Content-Type"], "HTML autenticado " + path)
    dashboard = client.json("GET", "/api/dashboard/summary")
    check(dashboard["kpis"]["products_in_stock"]["value"] == 43, "dashboard: 43 productos activos con stock")
    check(len(dashboard["inventory_distribution"]["items"]) > 0 and len(dashboard["recent_movements"]) > 0,
          "dashboard: categorías y actividad")
    for path in ("/api/reports/dashboard-summary", "/api/reports/stock-vs-minimum",
                 "/api/reports/entries-vs-exits", "/api/reports/movements-by-category",
                 "/api/predictions/readiness", "/api/predictions/products", "/api/reconciliation",
                 "/api/reconciliation/1"):
        check(bool(client.json("GET", path)), "API con contenido " + path)


def state_snapshot():
    state = {"environment_id": os.environ["ENVIRONMENT_ID"], "rows": fingerprint()}
    with engine().connect() as connection:
        # Additional seed-owned tables absent from the older portable verifier.
        for name in ("demo_seed", "product_suppliers", "product_state_histories"):
            rows = connection.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings().all()
            state["rows"][name] = [dict(row) for row in rows]
    return json.loads(json.dumps(state, default=str))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--skip-pipeline", action="store_true", help="Repeat ledger/HTTP checks using an already warmed cache")
    args = parser.parse_args()
    validate_demo()
    wait_database()
    verify_installation()
    from app import create_app
    from docker_support.demo_invariants import verify_dataset
    app = create_app()
    with app.app_context():
        counts = verify_dataset()
        check(True, "conteos, catálogo, dinero, aprobación separada, documentos, ledger e histórico")
        print(json.dumps(counts, sort_keys=True), flush=True)
    if not args.skip_pipeline:
        verify_pipeline(app)
    client = login()
    verify_pages(client)
    verify_exports(client)
    with engine().connect() as connection:
        all_counts = {name: connection.execute(text(f"SELECT COUNT(*) FROM `{name}`")).scalar_one()
                      for name in inspect(connection).get_table_names()}
        print("ALL_TABLE_COUNTS " + json.dumps(all_counts, sort_keys=True), flush=True)
    snapshot = Path("/app/instance/demo-validation.json")
    state = state_snapshot()
    if args.persist:
        check(json.loads(snapshot.read_text()) == state, "demo idempotente: misma identidad, filas y hashes")
    else:
        snapshot.write_text(json.dumps(state))
        check(True, "huella privada guardada para verificar segundo arranque y persistencia")


if __name__ == "__main__":
    main()
