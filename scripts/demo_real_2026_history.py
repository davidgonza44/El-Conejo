"""Reproduce the historical workflow in a new disposable SQLite application.

No dotenv, existing server, MySQL, containers, migrations or operational storage.
--verify exercises the HTTP API. --serve opens a separate, randomly assigned port
for manual/browser verification; only that temporary server can be stopped here.
"""
import argparse
import csv
import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path
import secrets
import threading
from urllib.parse import urlparse

from flask import jsonify, redirect, request, session
from sqlalchemy import case, func, select
from werkzeug.serving import WSGIRequestHandler, make_server

from approval_test_support import IsolatedCase
from test_real_2026_flow import BASE, operational_snapshot
from verify_real_2026_sources import compare

from app.extensions import db
from app.models.real_2026 import Real2026Daily, Real2026Import, Real2026Product
from app.services import real_2026_source_service as source


ROOT = Path(__file__).resolve().parents[1]


def load_files(source_dir, normalized_dir):
    comparison = compare(source_dir, normalized_dir)
    if (comparison["status"]["sources"] != "SAME_RESEARCH_SOURCES"
            or comparison["status"]["canonical_series"] != "CANONICAL_SERIES_EXACT"
            or not comparison["original_sources_unchanged"]
            or not comparison["receipt_manifest"].get("structure_valid")
            or not all(row["bytes_match"] and row["sha_match"] for row in comparison["receipt_manifest"].get("files", []))):
        raise RuntimeError("Las fuentes difieren del procedimiento de investigación; no se importó ningún dato.")
    files = {name: (normalized_dir / name).read_bytes() for name in source.REQUIRED_FILES
             if name != source.CATALOG_FILE}
    files.update({name: (source_dir / name).read_bytes() for name in
                  (source.CATALOG_FILE, "demanda_limpia_2026.csv", "movimientos_detalle_2026.csv",
                   "existencias_2026_final.csv")})
    files[source.RECEIPT_FILE] = (source_dir / source.RECEIPT_FILE).read_bytes()
    return files, comparison


def isolated_case():
    fixture = IsolatedCase()
    fixture.setUp()
    if db.engine.dialect.name != "sqlite" or db.engine.url.database != ":memory:":
        fixture.doCleanups()
        raise RuntimeError("Se requiere una base SQLite nueva en memoria.")
    return fixture


def checked(response, status=200):
    if response.status_code != status:
        raise RuntimeError("El flujo HTTP aislado no produjo el estado esperado: " + str(response.status_code))
    return response.get_json()


def choose_product(public_id):
    batch = Real2026Import.query.filter_by(public_id=public_id).one()
    positive = func.sum(case((Real2026Daily.quantity > 0, 1), else_=0))
    row = (db.session.query(Real2026Product.id).join(Real2026Daily)
           .filter(Real2026Product.batch_id == batch.id, Real2026Product.cohort == "STRICT",
                   Real2026Product.is_service.is_(False))
           .group_by(Real2026Product.id).having(func.count() >= 60, positive >= 12)
           .order_by(positive.desc(), Real2026Product.code).first())
    if row is None:
        raise RuntimeError("El lote no contiene un producto suficiente para esta demostración.")
    return row.id


def daily_integrity(files):
    expected = []
    for filename in source.SERIES_FILES.values():
        reader = csv.DictReader(io.StringIO(files[filename].decode("utf-8-sig")))
        expected.extend((row["codigo_producto"], row["fecha_real"],
                         format(Decimal(row["demanda_observada"]), ".6f")) for row in reader)
    expected.sort()
    statement = (select(Real2026Product.code, Real2026Daily.event_date, Real2026Daily.quantity)
                 .join(Real2026Daily, Real2026Product.id == Real2026Daily.product_id)
                 .order_by(Real2026Product.code, Real2026Daily.event_date))
    actual = [(code, day.isoformat(), format(amount, ".6f")) for code, day, amount in db.session.execute(statement)]
    if expected != actual:
        raise RuntimeError("La persistencia no conserva exactamente las cantidades y fechas de las series.")
    return True


def verify(fixture, files, comparison):
    before = operational_snapshot()
    original_hashes = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    client = fixture.clients["admin"]
    data = {"files": [(io.BytesIO(content), name) for name, content in files.items()]}
    uploaded = checked(client.post(BASE + "/preview", data=data, content_type="multipart/form-data"), 201)
    batch_id = uploaded["batch"]["id"]
    if client.get(BASE + f"/{batch_id}/products").status_code != 404:
        raise RuntimeError("Una vista previa habilitó datos antes de su confirmación.")
    confirmed = checked(client.post(BASE + f"/{batch_id}/confirm", json={"token": uploaded["confirmation_token"]}))
    product_id = choose_product(batch_id)
    path = BASE + f"/{batch_id}/products/{product_id}"
    history = checked(client.get(path))
    analyzed = checked(client.get(path + "/analysis?model=moving_average&horizon=7"))
    daily_count = Real2026Daily.query.count()
    replay = checked(client.post(BASE + "/preview", content_type="multipart/form-data",
        data={"files": [(io.BytesIO(content), name) for name, content in files.items()]}))
    if replay["created"] or replay["batch"]["id"] != batch_id or daily_count != Real2026Daily.query.count():
        raise RuntimeError("Una importación repetida duplicó el histórico.")
    if before != operational_snapshot():
        raise RuntimeError("La prueba modificó tablas operativas o el histórico 2025.")
    if analyzed["forecast"]["status"] != "available" or history["summary"]["last_date"] != "2026-09-18":
        raise RuntimeError("No se generó el análisis trazable esperado.")
    exact_daily = daily_integrity(files)
    if original_hashes != {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}:
        raise RuntimeError("Una fuente fue modificada en memoria.")
    return {"result": "PASS", "source_comparison": comparison["status"],
            "batch_confirmed": confirmed["batch"]["status"], "products": Real2026Product.query.count(),
            "daily_observations": daily_count, "selected_product_code": history["product"]["code"],
            "history_periods": history["summary"]["count"], "cutoff": history["summary"]["last_date"],
            "forecast_status": analyzed["forecast"]["status"], "forecast_periods": len(analyzed["forecast"]["points"]),
            "repeated_import_duplicates": 0, "operational_and_2025_tables_unchanged": True,
            "all_daily_dates_codes_quantities_exact": exact_daily,
            "source_files_unchanged": comparison["original_sources_unchanged"],
            "traceability_limitation": uploaded["batch"]["provenance"]["raw_traceability_note"]}


class QuietHandler(WSGIRequestHandler):
    def log(self, _type, _message, *args):
        pass  # Temporary login tokens and private query strings are never logged.


def metadata_target(value):
    target = Path(value).resolve()
    temporary = (ROOT / ".tmp").resolve()
    if not target.is_relative_to(temporary) or target.suffix != ".json" or target.exists():
        raise RuntimeError("Los metadatos deben ser un JSON nuevo dentro de .tmp.")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def serve(fixture, files, comparison, target):
    app, token, stopped = fixture.app, secrets.token_urlsafe(32), threading.Event()
    before = operational_snapshot()
    with fixture.clients["admin"].session_transaction() as current:
        cookies = dict(current)

    @app.get("/__isolated_real2026/login/<secret>")
    def login(secret):
        if not secrets.compare_digest(secret, token):
            return "No disponible", 404
        session.clear()
        session.update(cookies)
        return redirect("/historical-2026")

    @app.get("/__isolated_real2026/check")
    def check():
        if not secrets.compare_digest(request.headers.get("X-Isolated-Key", ""), token):
            return jsonify(error="No disponible"), 404
        result = {"isolated": True, "operational_and_2025_tables_unchanged": before == operational_snapshot(),
                  "batches": Real2026Import.query.count(), "products": Real2026Product.query.count(),
                  "daily_observations": Real2026Daily.query.count()}
        if request.args.get("finish") == "1":
            stopped.set()
        return jsonify(result)

    server = make_server("127.0.0.1", 0, app, threaded=False, request_handler=QuietHandler)
    origin = f"http://127.0.0.1:{server.server_port}"
    if urlparse(origin).port in (5000, 5001):
        server.server_close()
        raise RuntimeError("Puerto operativo rechazado.")
    metadata = {"origin": origin, "login_url": origin + "/__isolated_real2026/login/" + token,
                "isolated_key": token, "source_status": comparison["status"],
                "files": {name: str((Path(fixture.app.instance_path) / "browser_input" / name).resolve()) for name in files}}
    folder = Path(fixture.app.instance_path) / "browser_input"
    folder.mkdir()
    for name, content in files.items():
        (folder / name).write_bytes(content)
    target.write_text(json.dumps(metadata), encoding="utf-8")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({"isolated_server": origin, "metadata_file": str(target.relative_to(ROOT)),
                      "source_status": comparison["status"]}), flush=True)
    try:
        while not stopped.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    if before != operational_snapshot():
        raise RuntimeError("La demostración cambió datos operativos de la base desechable.")
    print("PASS: servidor desechable finalizado; tablas operativas y 2025 intactas.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--normalized-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--serve", action="store_true")
    parser.add_argument("--metadata-file")
    args = parser.parse_args()
    if args.serve and not args.metadata_file:
        parser.error("--serve requiere --metadata-file .tmp/<archivo-nuevo>.json")
    target = metadata_target(args.metadata_file) if args.serve else None
    files, comparison = load_files(args.source_dir, args.normalized_dir)
    fixture = isolated_case()
    try:
        if args.verify:
            print(json.dumps(verify(fixture, files, comparison), ensure_ascii=False))
        else:
            serve(fixture, files, comparison, target)
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    main()
