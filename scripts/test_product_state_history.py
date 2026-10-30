#!/usr/bin/env python3
"""Suite aislada del historial tipado minimum_stock/is_active: SQLite temporal.

Cubre baseline automático, change solo-si-cambia, atomicidad todo-o-nada,
propuestas sin historial hasta aprobación, actor autenticado vs. sistema (NULL),
state_at antes/entre/después, empate de
timestamp por id, validación de baseline ambiguo, dry-run sin escrituras y
?ref=1 zero-write. Sin MySQL ni ferreteria_conejo_tesis.

Uso: python scripts/test_product_state_history.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from test_products_core import check, create_via_api, modify_via_api, setup_app  # noqa: E402
import test_products_core  # noqa: E402

from app.extensions import db  # noqa: E402
from app.models import Category, Product  # noqa: E402
from app.models.product_state_history import EVENT_BASELINE, EVENT_CHANGE, ProductStateHistory  # noqa: E402
from app.services import product_service, product_state_history_service as history  # noqa: E402
from app.services.exceptions import ValidationError  # noqa: E402


def _counts(app):
    with app.app_context():
        return {"products": db.session.query(Product).count(),
                "history": db.session.query(ProductStateHistory).count()}


def _rows(app, pid):
    with app.app_context():
        return (ProductStateHistory.query.filter_by(product_id=pid)
                .order_by(ProductStateHistory.changed_at.asc(), ProductStateHistory.id.asc()).all())


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="product-state-history-"))
    app, admin, _inventario, _vendedor, ids = setup_app(temp_root)
    cat_a = ids["construccion"]
    admin_id = ids["admin_id"]
    assistant = app.core_roles["asistente"]

    # --- Creación con baseline -------------------------------------------------
    created = create_via_api(admin, code="H-001", name="Hist uno", category_id=cat_a,
                             current_stock=0, minimum_stock=5)
    check("crear vía API", created.status_code == 201)
    pid = created.json["id"]
    rows = _rows(app, pid)
    check("baseline y activación aprobada", len(rows) == 2 and rows[0].event_type == EVENT_BASELINE
          and rows[1].event_type == EVENT_CHANGE, str(len(rows)))
    check("baseline previous == new", rows[0].previous_minimum_stock == rows[0].new_minimum_stock == 5
          and rows[0].previous_is_active is False and rows[0].new_is_active is False)
    check("actores de propuesta y aprobación registrados", rows[0].changed_by_user_id == ids["asistente_id"]
          and rows[1].changed_by_user_id == admin_id)
    baseline_at = rows[0].changed_at

    # --- Cambio solo minimum_stock ---------------------------------------------
    proposed = assistant.put(f"/api/products/{pid}", json={"minimum_stock": 10})
    check("propuesta no escribe historial", proposed.status_code == 200 and proposed.json["minimum_stock"] == 5
          and len(_rows(app, pid)) == 2)
    upd = admin.post(f"/api/products/{pid}/approve", json={"proposal_id": proposed.json["pending_change"]["id"]})
    check("mínimo al aprobar", upd.status_code == 200 and upd.json["minimum_stock"] == 10)
    rows = _rows(app, pid)
    check("una fila change 5→10", len(rows) == 3 and rows[2].event_type == EVENT_CHANGE
          and (rows[2].previous_minimum_stock, rows[2].new_minimum_stock) == (5, 10)
          and (rows[2].previous_is_active, rows[2].new_is_active) == (True, True), str(len(rows)))

    # --- Cambio solo is_active ---------------------------------------------------
    other = create_via_api(admin, code="H-002", name="Hist dos", category_id=cat_a, minimum_stock=3)
    pid2 = other.json["id"]
    deact = assistant.delete(f"/api/products/{pid2}")
    approved = admin.post(f"/api/products/{pid2}/approve", json={"proposal_id": deact.json["product"]["pending_change"]["id"]})
    check("DELETE propone y aprobación desactiva", deact.status_code == 200 and approved.status_code == 200)
    rows = _rows(app, pid2)
    check("change solo is_active true→false", len(rows) == 3
          and (rows[2].previous_minimum_stock, rows[2].new_minimum_stock) == (3, 3)
          and (rows[2].previous_is_active, rows[2].new_is_active) == (True, False))

    # --- Ambos en una operación = una fila ---------------------------------------
    both = create_via_api(admin, code="H-003", name="Hist tres", category_id=cat_a, minimum_stock=4)
    pid3 = both.json["id"]
    upd = modify_via_api(admin, pid3, {"minimum_stock": 9, "is_active": False})
    check("aprobación de ambos campos", upd.status_code == 200)
    rows = _rows(app, pid3)
    check("ambos en una sola transición", len(rows) == 3
          and (rows[2].previous_minimum_stock, rows[2].new_minimum_stock) == (4, 9)
          and (rows[2].previous_is_active, rows[2].new_is_active) == (True, False))

    # --- Otros campos y mismo valor no crean --------------------------------------
    before = len(_rows(app, pid3))
    renamed = modify_via_api(admin, pid3, {"name": "Hist tres renombrado"})
    check("nombre aprobado", renamed.status_code == 200)
    check("solo nombre no crea fila", len(_rows(app, pid3)) == before)
    same = assistant.put(f"/api/products/{pid3}", json={"minimum_stock": 9})
    check("mismo valor rechazado sin fila", same.status_code == 400 and len(_rows(app, pid3)) == before)

    # --- Rollback si falla la actualización ----------------------------------------
    before_rows, before_prod = len(_rows(app, pid)), dict(minimum_stock=10)
    bad = assistant.put(f"/api/products/{pid}", json={"minimum_stock": -1})
    check("mínimo negativo rechazado", bad.status_code == 400)
    with app.app_context():
        prod = db.session.get(Product, pid)
        check("producto intacto tras fallo", prod.minimum_stock == before_prod["minimum_stock"])
    check("sin fila tras fallo de update", len(_rows(app, pid)) == before_rows)

    # --- Rollback si falla el historial ----------------------------------------------
    before_rows = len(_rows(app, pid))
    pending = assistant.put(f"/api/products/{pid}", json={"minimum_stock": 11})
    with patch.object(history, "record_change", side_effect=RuntimeError("forced-history-fail")):
        boom = admin.post(f"/api/products/{pid}/approve", json={"proposal_id": pending.json["pending_change"]["id"]})
    check("error de historial no deja estado parcial", boom.status_code >= 500)
    with app.app_context():
        prod = db.session.get(Product, pid)
        check("producto intacto tras fallo de historial", prod.minimum_stock == 10)
    check("sin fila tras fallo de historial", len(_rows(app, pid)) == before_rows)
    intact = admin.get(f"/api/products/{pid}")
    check("fallo de aprobación conserva propuesta pendiente", intact.json["pending_change"]["id"] == pending.json["pending_change"]["id"])
    admin.post(f"/api/products/{pid}/reject", json={"proposal_id": pending.json["pending_change"]["id"], "reason": "Prueba de rollback"})

    # --- Servicio de producto sin usuario bloqueado; historial de fixture -> NULL ------
    with app.test_request_context():
        try:
            product_service.update_product(pid, {"minimum_stock": 12})
        except ValidationError as error:
            check("servicio sin sesión no edita", error.status_code == 403)
        else:
            check("servicio sin sesión no edita", False)
    with app.app_context():
        # Solo fixture SQLite: verifica el servicio de historial independiente.
        db.session.get(Product, pid).minimum_stock = 12
        history.record_change(pid, 10, 12, True, True, datetime.utcnow())
        db.session.commit()
        row = (ProductStateHistory.query.filter_by(product_id=pid)
               .order_by(ProductStateHistory.id.desc()).first())
        check("sistema sin sesión deja NULL", row.changed_by_user_id is None
              and (row.previous_minimum_stock, row.new_minimum_stock) == (10, 12))

    # --- Dos cambios + reconstrucción ------------------------------------------------------
    with app.app_context():
        t0 = ProductStateHistory.query.filter_by(product_id=pid).order_by(
            ProductStateHistory.id.asc()).first().changed_at
    with app.app_context():
        first_change_at = ProductStateHistory.query.filter_by(
            product_id=pid, event_type=EVENT_CHANGE, new_minimum_stock=10).order_by(
            ProductStateHistory.id.asc()).first().changed_at
        last = ProductStateHistory.query.filter_by(product_id=pid).order_by(
            ProductStateHistory.id.desc()).first()
    with app.app_context():
        check("antes del baseline no disponible",
              history.state_at(pid, t0 - timedelta(seconds=1)) == {
                  "available": False, "reason": "before_coverage", "minimum_stock": None,
                  "is_active": None, "event_type": None, "changed_at": None})
        mid = history.state_at(pid, first_change_at)
        check("entre cambios devuelve el primero válido",
              mid["available"] is True and mid["minimum_stock"] == 10, str(mid))
        now_state = history.state_at(pid, datetime.utcnow())
        check("después devuelve el último", now_state["available"] is True
              and now_state["minimum_stock"] == 12, str(now_state))
        check("wrapper product_state_at delega",
              product_service.product_state_at(pid, last.changed_at)["minimum_stock"] == 12)

    # --- Empate de timestamp resuelto por id --------------------------------------------------
    tie = create_via_api(admin, code="H-TIE", name="Empate", category_id=cat_a, minimum_stock=1)
    tie_id = tie.json["id"]
    with app.app_context():
        stamp = datetime.utcnow()
        db.session.add(ProductStateHistory(product_id=tie_id, previous_minimum_stock=1, new_minimum_stock=2,
                                           previous_is_active=True, new_is_active=True,
                                           event_type=EVENT_CHANGE, changed_at=stamp,
                                           changed_by_user_id=admin_id))
        db.session.add(ProductStateHistory(product_id=tie_id, previous_minimum_stock=2, new_minimum_stock=7,
                                           previous_is_active=True, new_is_active=True,
                                           event_type=EVENT_CHANGE, changed_at=stamp,
                                           changed_by_user_id=admin_id))
        db.session.commit()
        got = history.state_at(tie_id, stamp)
        check("timestamp idéntico gana el id mayor", got["available"] is True and got["minimum_stock"] == 7, str(got))

    # --- Baseline: dry-run no escribe, apply todo-o-nada con timestamp único -------------------
    with app.app_context():
        cat = db.session.get(Category, cat_a)
        legacy_a = Product(code="LEG-A", name="Legado A", category_id=cat.id, current_stock=0,
                           minimum_stock=6, purchase_price=1, sale_price=2, is_active=True)
        legacy_b = Product(code="LEG-B", name="Legado B", category_id=cat.id, current_stock=0,
                           minimum_stock=2, purchase_price=1, sale_price=2, is_active=False)
        db.session.add_all([legacy_a, legacy_b])
        db.session.commit()
        leg_ids = [legacy_a.id, legacy_b.id]
    counts_before = _counts(app)
    coverage = datetime.utcnow()
    with app.app_context():
        planned = history.plan_baseline(coverage)
        planned_ids = sorted(p["product_id"] for p in planned)
        check("dry-run planifica solo sin historial", planned_ids == sorted(leg_ids), str(planned_ids))
    check("dry-run sin escrituras", _counts(app) == counts_before, str(_counts(app)))
    with app.app_context():
        result = history.apply_baseline(coverage)
        check("apply inserta todo-o-nada", result["inserted"] == 2
              and result["coverage_started_at"] == coverage.isoformat(), str(result))
        stamps = {r.changed_at for r in ProductStateHistory.query.filter(
            ProductStateHistory.product_id.in_(leg_ids)).all()}
        check("timestamp único de cobertura", len(stamps) == 1 and stamps.pop() == coverage)
        check("invariantes limpias tras baseline", not any(history.validate_invariants().values()),
              str(history.validate_invariants()))

    # --- Ambiguo aborta sin escribir --------------------------------------------------------------
    with app.app_context():
        cat = db.session.get(Category, cat_a)
        rogue = Product(code="ROGUE", name="Sin baseline", category_id=cat.id, current_stock=0,
                        minimum_stock=1, purchase_price=1, sale_price=2, is_active=True)
        db.session.add(rogue)
        db.session.flush()
        rogue_id = rogue.id
        db.session.add(ProductStateHistory(product_id=rogue_id, previous_minimum_stock=1, new_minimum_stock=9,
                                           previous_is_active=True, new_is_active=True,
                                           event_type=EVENT_CHANGE, changed_at=datetime.utcnow(),
                                           changed_by_user_id=None))
        db.session.commit()
    counts_before = _counts(app)
    with app.app_context():
        anomalies = history.validate_invariants()
        check("detecta change sin baseline", rogue_id in anomalies["change_without_baseline"], str(anomalies))
        failed = False
        try:
            history.apply_baseline(datetime.utcnow())
        except Exception:
            failed = True
        check("ambiguo aborta sin escrituras", failed and _counts(app) == counts_before)
    with app.app_context():
        db.session.query(ProductStateHistory).filter_by(product_id=rogue_id).delete()
        db.session.query(Product).filter_by(id=rogue_id).delete()
        db.session.commit()

    # --- ?ref=1 zero-write ---------------------------------------------------------------------------
    counts_before = _counts(app)
    app.debug = True
    ref = admin.get("/products?ref=1")
    app.debug = False
    check("?ref=1 responde sin tocar datos", ref.status_code == 200 and _counts(app) == counts_before)

    print(f"\n{test_products_core.CHECKS_RUN} comprobaciones, {test_products_core.FAILURES} fallos")
    return 1 if test_products_core.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
