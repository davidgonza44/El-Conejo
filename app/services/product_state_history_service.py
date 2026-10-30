"""Historial tipado de Product.minimum_stock e is_active (solo lectura/escritura local).

Cobertura: cada producto cubierto tiene exactamente un baseline (previous == new)
como primer evento; los change posteriores son la única fuente para
``state_at``. Antes del baseline no se afirma nada (available=False).
"""

from datetime import datetime

from flask import has_request_context
from flask_login import current_user

from app.extensions import db
from app.models import Product
from app.models.product_state_history import (
    EVENT_BASELINE,
    EVENT_CHANGE,
    EVENT_TYPES,
    ProductStateHistory,
)
from app.services.exceptions import ValidationError


def actor_id() -> int | None:
    """Responsable autenticado si lo hay; None para operaciones de sistema."""
    if has_request_context() and current_user.is_authenticated:
        return current_user.id
    return None


def _add_event(product_id, prev_min, new_min, prev_active, new_active, event_type, changed_at, actor) -> ProductStateHistory:
    if event_type not in EVENT_TYPES:
        raise ValidationError(f"event_type inválido: '{event_type}'.")
    if event_type == EVENT_BASELINE and (prev_min != new_min or prev_active != new_active):
        raise ValidationError("El baseline exige previous == new en ambos atributos.")
    row = ProductStateHistory(
        product_id=product_id,
        previous_minimum_stock=int(prev_min),
        new_minimum_stock=int(new_min),
        previous_is_active=bool(prev_active),
        new_is_active=bool(new_active),
        event_type=event_type,
        changed_at=changed_at,
        changed_by_user_id=actor,
    )
    db.session.add(row)
    return row


def record_baseline(product: Product, changed_at, actor=None) -> ProductStateHistory:
    """Baseline inicial: previous == new == estado actual del producto."""
    if actor is None:
        actor = actor_id()
    return _add_event(product.id, product.minimum_stock, product.minimum_stock,
                      product.is_active, product.is_active, EVENT_BASELINE, changed_at, actor)


def record_change(product_id, prev_min, new_min, prev_active, new_active, changed_at, actor=None) -> ProductStateHistory:
    if actor is None:
        actor = actor_id()
    return _add_event(product_id, prev_min, new_min, prev_active, new_active,
                      EVENT_CHANGE, changed_at, actor)


def state_at(product_id: int, ts: datetime) -> dict:
    """Estado válido en ``ts`` (UTC naive). Solo lectura, sin escrituras."""
    rows = (
        db.session.query(ProductStateHistory)
        .filter(ProductStateHistory.product_id == product_id,
                ProductStateHistory.changed_at <= ts)
        .order_by(ProductStateHistory.changed_at.desc(), ProductStateHistory.id.desc())
        .limit(1)
        .all()
    )
    if not rows:
        return {"available": False, "reason": "before_coverage",
                "minimum_stock": None, "is_active": None,
                "event_type": None, "changed_at": None}
    row = rows[0]
    return {"available": True, "reason": "ok",
            "minimum_stock": row.new_minimum_stock, "is_active": row.new_is_active,
            "event_type": row.event_type,
            "changed_at": row.changed_at.isoformat() if row.changed_at else None}


def states_before(product_ids: list[int], ts: datetime) -> dict[int, dict | None]:
    """Estado válido estrictamente antes de ``ts`` (``changed_at < ts``), por producto.

    Límite exclusivo para el cierre comercial: un evento exactamente en ``ts``
    pertenece al día que empieza, no al que cierra. Solo lectura. No cambia la
    semántica inclusiva de ``state_at``. Sin fila previa devuelve None
    (cobertura ausente, no inventar).
    """
    wanted = sorted(set(product_ids))
    if not wanted:
        return {}
    rows = (
        db.session.query(ProductStateHistory)
        .filter(ProductStateHistory.product_id.in_(wanted),
                ProductStateHistory.changed_at < ts)
        .order_by(ProductStateHistory.product_id.asc(),
                  ProductStateHistory.changed_at.asc(),
                  ProductStateHistory.id.asc())
        .all()
    )
    latest: dict[int, ProductStateHistory] = {}
    for row in rows:
        latest[row.product_id] = row
    result: dict[int, dict | None] = {}
    for pid in wanted:
        row = latest.get(pid)
        result[pid] = None if row is None else {
            "minimum_stock": row.new_minimum_stock, "is_active": bool(row.new_is_active),
            "event_type": row.event_type,
            "changed_at": row.changed_at.isoformat() if row.changed_at else None}
    return result


def validate_invariants() -> dict:
    """Detecta estados ambiguos sin escribir. Listas de product_id afectados."""
    rows = (
        db.session.query(ProductStateHistory)
        .order_by(ProductStateHistory.product_id.asc(),
                  ProductStateHistory.changed_at.asc(),
                  ProductStateHistory.id.asc())
        .all()
    )
    by_product: dict[int, list] = {}
    for row in rows:
        by_product.setdefault(row.product_id, []).append(row)
    change_without_baseline, multiple_baselines = [], []
    inconsistent_baselines, baseline_not_first = [], []
    for pid, items in by_product.items():
        baselines = [r for r in items if r.event_type == EVENT_BASELINE]
        if not baselines and any(r.event_type == EVENT_CHANGE for r in items):
            change_without_baseline.append(pid)
        if len(baselines) > 1:
            multiple_baselines.append(pid)
        for row in baselines:
            if row.previous_minimum_stock != row.new_minimum_stock or \
                    bool(row.previous_is_active) != bool(row.new_is_active):
                inconsistent_baselines.append(pid)
                break
        if items and items[0].event_type != EVENT_BASELINE:
            baseline_not_first.append(pid)
    return {"change_without_baseline": sorted(change_without_baseline),
            "multiple_baselines": sorted(multiple_baselines),
            "inconsistent_baselines": sorted(inconsistent_baselines),
            "baseline_not_first": sorted(baseline_not_first)}


def missing_baseline_product_ids() -> list[int]:
    """Productos sin ninguna fila histórica (candidatos a baseline)."""
    covered = {pid for (pid,) in db.session.query(ProductStateHistory.product_id).distinct().all()}
    return sorted(pid for (pid,) in db.session.query(Product.id).all() if pid not in covered)


def plan_baseline(coverage_at: datetime) -> list[dict]:
    """Filas baseline que se crearían con ``coverage_at`` (sin escribir)."""
    anomalies = validate_invariants()
    if any(anomalies.values()):
        raise ValidationError(f"Historial ambiguo, baseline abortado: {anomalies}.")
    missing = set(missing_baseline_product_ids())
    if not missing:
        return []
    products = db.session.query(Product).filter(Product.id.in_(sorted(missing))).all()
    return [{"product_id": p.id, "minimum_stock": p.minimum_stock,
             "is_active": bool(p.is_active), "changed_at": coverage_at} for p in products]


def apply_baseline(coverage_at: datetime, actor=None) -> dict:
    """Inserta baselines pendientes en una sola transacción todo-o-nada."""
    planned = plan_baseline(coverage_at)
    if actor is None:
        actor = actor_id()
    try:
        for item in planned:
            _add_event(item["product_id"], item["minimum_stock"], item["minimum_stock"],
                       item["is_active"], item["is_active"], EVENT_BASELINE,
                       item["changed_at"], actor)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return {"coverage_started_at": coverage_at.isoformat(), "inserted": len(planned)}
