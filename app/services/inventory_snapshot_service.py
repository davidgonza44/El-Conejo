"""Cierre diario real a las 00:05 de Caracas, sin reconstrucción del pasado.

snapshot_date identifica el día comercial recién terminado; created_at conserva
la hora UTC real de la captura. Los cambios entre medianoche y la captura están
incluidos. Una ejecución perdida queda sin cierre, nunca se rellena después.
"""
from datetime import datetime, time, timedelta, timezone
import hashlib

from sqlalchemy import func, select, text

from app.extensions import db
from app.models import Category, InventorySnapshot, Product
from app.services.inventory_service import COMMERCIAL_ZONE

CLOSING_TIME = time(0, 5)
CAPTURE_WINDOW = timedelta(minutes=1)


def commercial_today(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(COMMERCIAL_ZONE).date()


def capture_slot(now=None):
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("El reloj del cierre debe incluir zona horaria.")
    local = instant.astimezone(COMMERCIAL_ZONE)
    scheduled = datetime.combine(local.date(), CLOSING_TIME, COMMERCIAL_ZONE)
    return local, scheduled, scheduled <= local < scheduled + CAPTURE_WINDOW


def _skipped(day, reason, existing=0):
    return {"snapshot_date": day.isoformat(), "inserted": 0, "existing": existing,
            "skipped": True, "reason": reason}


def _verify_engines(connection):
    engines = dict(connection.execute(text(
        "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN "
        "('products','categories','inventory_snapshots')")).all())
    if any(engines.get(name, "").upper() != "INNODB"
           for name in ("products", "categories", "inventory_snapshots")):
        raise RuntimeError("El cierre requiere las tres tablas existentes en InnoDB.")
    connection.commit()


def _capture(connection, now):
    with connection.begin():
        if connection.dialect.name == "mysql":
            connection.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        captured = datetime.now(timezone.utc)
        # Recheck after both lock and transaction startup, with one real timestamp.
        local, _, punctual = capture_slot(now or captured)
        day = local.date() - timedelta(days=1)
        if not punctual:
            return _skipped(day, "outside_capture_window")
        taken_at = captured.replace(tzinfo=None)
        existing = connection.execute(select(func.count()).select_from(InventorySnapshot)
                                      .where(InventorySnapshot.snapshot_date == day)).scalar_one()
        if existing:
            return {"snapshot_date": day.isoformat(), "inserted": 0,
                    "existing": existing, "skipped": True}
        rows = connection.execute(select(
            Product.id, Product.current_stock, Product.minimum_stock, Product.is_active,
            Product.category_id, Category.is_active,
        ).join(Category, Category.id == Product.category_id).order_by(Product.id)).all()
        if not rows:
            return _skipped(day, "empty_catalog")
        connection.execute(InventorySnapshot.__table__.insert(), [
            dict(snapshot_date=day, product_id=pid, stock=stock, minimum_stock=minimum,
                 product_is_active=bool(active), category_id=category_id,
                 category_is_active=bool(category_active), created_at=taken_at)
            for pid, stock, minimum, active, category_id, category_active in rows
        ])
    return {"snapshot_date": day.isoformat(), "inserted": len(rows), "existing": 0, "skipped": False}


def take_snapshot(now=None) -> dict:
    """Una captura atómica por día; reintentos y reinicios consultan la tabla.

    ``now`` permite ensayar el reloj en bases desechables; los ejecutores públicos
    nunca aceptan fechas. La hora de captura se obtiene siempre del reloj real.
    """
    local, _, punctual = capture_slot(now)
    day = local.date() - timedelta(days=1)
    if not punctual:
        return _skipped(day, "outside_capture_window")
    db.session.rollback()  # El job es dueño de la transacción.
    with db.engine.connect() as connection:
        if connection.dialect.name != "mysql":
            return _capture(connection, now)
        connection = connection.execution_options(isolation_level="REPEATABLE READ")
        _verify_engines(connection)
        database_key = hashlib.sha256(connection.engine.url.database.encode()).hexdigest()[:12]
        lock_name = "conejo.daily-inventory." + database_key + "." + day.isoformat()
        acquired = connection.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": lock_name}).scalar()
        connection.commit()
        if acquired != 1:
            return _skipped(day, "capture_busy")
        try:
            return _capture(connection, now)
        finally:
            connection.rollback()
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
            connection.commit()
