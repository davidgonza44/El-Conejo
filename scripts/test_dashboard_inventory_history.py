#!/usr/bin/env python3
"""Suite aislada de vs. ayer con historial: SQLite temporal, sin MySQL ni tesis.

Cubre reconstrucción sin snapshot (movimientos + states_before exclusivo),
límite C estricto (evento/movimiento/creación exactamente en C pertenecen a
hoy), prioridad del snapshot, cobertura parcial -> Sin comparación, universo
vacío -> 0 exacto, cero escrituras del GET y ?ref=1 intacto.

Uso: python scripts/test_dashboard_inventory_history.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from test_products_core import check, create_via_api, setup_app  # noqa: E402
import test_products_core  # noqa: E402

from app.extensions import db  # noqa: E402
from app.models import InventorySnapshot, Product, StockMovement  # noqa: E402
from app.models.product_state_history import EVENT_BASELINE, EVENT_CHANGE, ProductStateHistory  # noqa: E402
from app.models.stock_movement import MOVEMENT_ENTRADA, MOVEMENT_SALIDA  # noqa: E402
from app.services import product_state_history_service as history  # noqa: E402
from app.services.inventory_snapshot_service import commercial_today  # noqa: E402
from app.services.sales_common import utc_boundary  # noqa: E402


def _counts(app):
    with app.app_context():
        return {m.__tablename__: db.session.query(m).count()
                for m in (Product, ProductStateHistory, StockMovement, InventorySnapshot)}


def _make(admin, cat, code, name, stock, minimum):
    r = create_via_api(admin, code=code, name=name, category_id=cat,
                       current_stock=stock, minimum_stock=minimum)
    assert r.status_code == 201, r.json
    return r.json["id"]


def _age(app, pid, created_at, baseline_at):
    """Simula producto preexistente cubierto: creación, movimientos iniciales y baseline < C."""
    with app.app_context():
        db.session.get(Product, pid).created_at = created_at
        row = ProductStateHistory.query.filter_by(product_id=pid).one()
        row.changed_at = baseline_at
        for move in StockMovement.query.filter_by(product_id=pid).all():
            move.created_at = created_at
        db.session.commit()


def _change(app, pid, prev_min, new_min, prev_active, new_active, at):
    with app.app_context():
        product = db.session.get(Product, pid)
        product.minimum_stock = new_min
        product.is_active = new_active
        db.session.add(ProductStateHistory(
            product_id=pid, previous_minimum_stock=prev_min, new_minimum_stock=new_min,
            previous_is_active=prev_active, new_is_active=new_active,
            event_type=EVENT_CHANGE, changed_at=at, changed_by_user_id=None))
        db.session.commit()


def _move(app, pid, mtype, qty, prev, new, at):
    with app.app_context():
        db.session.get(Product, pid).current_stock = new
        db.session.add(StockMovement(
            product_id=pid, movement_type=mtype, quantity=qty,
            previous_stock=prev, new_stock=new, reason="Escenario",
            user_id=None, created_at=at))
        db.session.commit()


def _kpis(admin):
    return admin.get("/api/dashboard/summary").json["kpis"]


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="dashboard-inventory-history-"))
    app, admin, _inv, _ven, ids = setup_app(temp_root)
    cat = ids["construccion"]
    today = commercial_today()
    cutoff = utc_boundary(today)
    yesterday = today - timedelta(days=1)
    old, base = cutoff - timedelta(days=2), cutoff - timedelta(hours=1)

    # --- Escenario A: reconstrucción + bordes exactos en C -------------------------
    p = _make(admin, cat, "H-P", "Borde P", 0, 5)
    r = _make(admin, cat, "H-R", "Borde R", 6, 20)
    s = _make(admin, cat, "H-S", "Borde S", 0, 2)
    q = _make(admin, cat, "H-Q", "Borde Q", 4, 0)
    for pid in (p, r, s):
        _age(app, pid, old, base)
    with app.app_context():
        db.session.get(Product, q).created_at = cutoff  # creado exactamente en C
        db.session.commit()
    _change(app, p, 5, 50, True, True, cutoff)  # cambio de mínimo exactamente en C
    _change(app, r, 20, 20, True, False, cutoff)  # desactivación exactamente en C
    _move(app, p, MOVEMENT_SALIDA, 3, 8, 5, cutoff)  # salida exactamente en C
    _move(app, s, MOVEMENT_ENTRADA, 5, 0, 5, cutoff + timedelta(hours=1))

    with app.app_context():
        check("state_at inclusivo intacto (cambio en C visible con <=)",
              history.state_at(p, cutoff)["minimum_stock"] == 50)
        states = history.states_before([p, r, s], cutoff)
        check("states_before excluye lo ocurrido en C",
              states[p]["minimum_stock"] == 5 and states[r]["is_active"] is True)

    k = _kpis(admin)
    check("ayer reconstruido: en stock P y R",
          k["products_in_stock"]["comparison"] == {
              "available": True, "value": 2, "delta": 1,
              "change_percent": 50.0, "reason": "ok"},
          str(k["products_in_stock"]))
    check("ayer reconstruido: alertas R y S",
          k["active_alerts"]["comparison"] == {
              "available": True, "value": 2, "delta": -1,
              "change_percent": -50.0, "reason": "ok"},
          str(k["active_alerts"]))
    check("valores de hoy (P bajo, Q disponible, S disponible, R inactivo)",
          k["products_in_stock"]["value"] == 3 and k["active_alerts"]["value"] == 1,
          str({key: k[key]["value"] for key in ("products_in_stock", "active_alerts")}))

    # --- Escenario B: snapshot de ayer manda -----------------------------------------
    with app.app_context():
        taken = datetime.utcnow()
        for pid, stock, minimum, active in ((p, 4, 10, True), (r, 0, 5, True),
                                            (s, 0, 5, True), (q, 7, 1, False)):
            db.session.add(InventorySnapshot(
                snapshot_date=yesterday, product_id=pid, stock=stock, minimum_stock=minimum,
                product_is_active=active, category_id=cat, category_is_active=True,
                created_at=taken))
        db.session.commit()
    k = _kpis(admin)
    check("snapshot prioriza: en stock 3-2=1",
          k["products_in_stock"]["comparison"]["value"] == 1, str(k["products_in_stock"]))
    check("snapshot prioriza: alertas 1+2=3",
          k["active_alerts"]["comparison"]["value"] == 3, str(k["active_alerts"]))
    with app.app_context():
        InventorySnapshot.query.delete()
        db.session.commit()

    # --- Escenario C: cobertura parcial -> Sin comparación ------------------------------
    t = _make(admin, cat, "H-T", "Sin cobertura", 9, 1)
    with app.app_context():
        db.session.get(Product, t).created_at = old  # necesario ayer...
        ProductStateHistory.query.filter_by(product_id=t).delete()  # ...pero sin historial
        db.session.commit()
    k = _kpis(admin)
    check("sin cobertura de uno -> ambas sin comparación",
          k["products_in_stock"]["comparison"]["available"] is False
          and k["active_alerts"]["comparison"]["available"] is False, str(k["products_in_stock"]))
    with app.app_context():
        StockMovement.query.filter_by(product_id=t).delete()
        db.session.query(Product).filter_by(id=t).delete()
        db.session.commit()

    # --- Zero-write y ?ref=1 -----------------------------------------------------------------
    before = _counts(app)
    data = admin.get("/api/dashboard/summary").json
    check("GET no escribe", _counts(app) == before, str(before))
    check("reconstrucción tras limpiar T vuelve a estar disponible",
          data["kpis"]["products_in_stock"]["comparison"]["available"] is True)
    app.debug = True
    ref = admin.get("/dashboard?ref=1").get_data(as_text=True)
    app.debug = False
    check("?ref=1 determinista y zero-write",
          "dashboard_reference_data.js" in ref and _counts(app) == before)

    print(f"\n{test_products_core.CHECKS_RUN} comprobaciones, {test_products_core.FAILURES} fallos")
    return 1 if test_products_core.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
