"""Recomendaciones de reabastecimiento a partir de pronóstico, stock y compras en camino.

    reorder_point       = demanda prevista durante el lead time + umbral de stock mínimo
    inventory_position  = stock actual + unidades pendientes de órdenes pending/approved/partial
    net_requirement     = reorder_point - inventory_position
    suggested_quantity  = max(0, ceil(net_requirement))

``Product.minimum_stock`` se usa como umbral actual de reposición (no es un
safety stock estadístico). El lead time de 7 días es un supuesto centralizado
(``DEFAULT_LEAD_TIME_DAYS``) hasta que exista un dato real de proveedor.
Órdenes canceladas y completadas no aportan supply; las recepciones parciales
descuentan lo ya recibido. El análisis NUNCA crea órdenes: ``order_payload``
solo arma la solicitud que el usuario confirma y que ejecuta
``purchase_service.create_order`` con los permisos reales de Compras.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models import Product
from app.models.purchase import PurchaseOrder, PurchaseOrderLine
from app.services.exceptions import ValidationError

PRIORITY_HIGH = "high"
PRIORITY_MEDIUM = "medium"
PRIORITY_LOW = "low"
PRIORITY_LABELS = {PRIORITY_HIGH: "Alta", PRIORITY_MEDIUM: "Media", PRIORITY_LOW: "Baja"}
PRIORITY_TONES = {PRIORITY_HIGH: "red", PRIORITY_MEDIUM: "orange", PRIORITY_LOW: "green"}
PRIORITY_RANK = {PRIORITY_HIGH: 0, PRIORITY_MEDIUM: 1, PRIORITY_LOW: 2}
SUPPLY_STATUSES = ("approved",)
DEFAULT_LEAD_TIME_DAYS = 7  # supuesto centralizado; no es lead time histórico de proveedor
LEAD_TIME_ASSUMED = "assumed"
LEAD_TIME_PROVIDED = "provided"


@dataclass(frozen=True)
class ReplenishmentConfig:
    default_lead_time_days: int = DEFAULT_LEAD_TIME_DAYS
    medium_margin_days: int = DEFAULT_LEAD_TIME_DAYS  # cobertura ≤ lead time + margen => prioridad media


DEFAULT_CONFIG = ReplenishmentConfig()


@dataclass(frozen=True)
class Recommendation:
    lead_time_days: int
    lead_time_demand: float
    safety_stock: int  # copia de Product.minimum_stock; umbral de reposición, no cálculo estadístico
    reorder_point: float
    pending_supply: int
    inventory_position: float
    net_requirement: float
    suggested_quantity: int
    priority: str
    recommended_date: date
    cover_days: float | None
    unit_cost: Decimal
    reasons: tuple[str, ...]
    lead_time_source: str = LEAD_TIME_ASSUMED

    @property
    def needed(self) -> bool:
        return self.suggested_quantity > 0

    @property
    def estimated_cost(self) -> Decimal:
        return (self.unit_cost * self.suggested_quantity).quantize(Decimal("0.01"))

    @property
    def priority_label(self) -> str:
        return PRIORITY_LABELS[self.priority]

    @property
    def priority_tone(self) -> str:
        return PRIORITY_TONES[self.priority]


def pending_supply_by_product() -> dict[int, int]:
    """Unidades en camino por producto: ordered - received de órdenes pending/approved/partial."""
    rows = (db.session.query(PurchaseOrderLine.product_id,
                             func.sum(PurchaseOrderLine.quantity_ordered - PurchaseOrderLine.quantity_received))
            .join(PurchaseOrder, PurchaseOrderLine.purchase_order_id == PurchaseOrder.id)
            .filter(PurchaseOrder.status.in_(SUPPLY_STATUSES))
            .group_by(PurchaseOrderLine.product_id).all())
    return {int(product_id): int(pending or 0) for product_id, pending in rows if pending and pending > 0}


def compute(current_stock: int, minimum_stock: int, expected_daily_demand: float, pending_supply: int,
            unit_cost: Decimal, today: date, lead_time_days: int | None = None,
            config: ReplenishmentConfig = DEFAULT_CONFIG, *, is_service: bool = False,
            rate_unit: str = "calendar_day") -> Recommendation | None:
    # No implicit conversion from operational demand rates to civil supplier lead time.
    # A future supplier/calendar adapter must convert explicitly before this boundary.
    if is_service:
        return None
    if rate_unit != "calendar_day":
        raise ValueError("Replenishment requires a civil-day rate; explicit conversion is required")
    assumed = lead_time_days is None
    lead_time = lead_time_days or config.default_lead_time_days
    daily = max(0.0, expected_daily_demand or 0.0)
    lead_demand = daily * lead_time
    reorder_point = lead_demand + minimum_stock
    position = current_stock + pending_supply
    net = reorder_point - position
    suggested = max(0, math.ceil(net - 1e-9)) if net > 0 else 0
    cover = (position / daily) if daily > 0 else None
    reasons: list[str] = []
    if current_stock <= 0:
        priority, reason = PRIORITY_HIGH, "Sin stock"
    elif cover is not None and cover <= lead_time:
        priority, reason = PRIORITY_HIGH, f"Se agotaría antes de la reposición ({lead_time} días de lead time)"
    elif cover is not None and cover <= lead_time + config.medium_margin_days:
        priority, reason = PRIORITY_MEDIUM, "Cobertura cercana al punto de reorden"
    else:
        priority, reason = PRIORITY_LOW, "Por debajo del punto de reorden" if net > 0 else "Sin necesidad de compra"
    reasons.append(reason)
    if pending_supply:
        reasons.append(f"{pending_supply} uds. en órdenes pendientes ya descontadas")
    if priority == PRIORITY_HIGH or cover is None:
        recommended = today
    else:
        recommended = today + timedelta(days=max(0, math.floor(cover - lead_time)))
    return Recommendation(lead_time, lead_demand, minimum_stock, reorder_point, pending_supply, position, net,
                          suggested, priority, recommended, cover, Decimal(unit_cost or 0), tuple(reasons),
                          LEAD_TIME_ASSUMED if assumed else LEAD_TIME_PROVIDED)


def order_payload(items: list[dict], supplier_id, estimated_delivery_date=None, notes: str = "",
                  *, service_product_ids=()) -> dict:
    """Arma la solicitud de ``purchase_service.create_order`` con el costo de catálogo.

    ``items``: [{"product_id", "quantity", "unit_cost"?}]. Valida existencia/actividad y
    cantidad > 0; el resto de reglas (proveedor, límites, duplicados) las aplica Compras.
    """
    if not isinstance(items, list) or not items:
        raise ValidationError("Seleccione al menos una recomendación para generar la orden.")
    ids = []
    for item in items:
        try:
            ids.append(int(item.get("product_id")))
        except (TypeError, ValueError, AttributeError):
            raise ValidationError("Producto inválido en la selección.")
    # Trusted input-layer designations are additive; no cross-request persistence is implied.
    # Client flags can deny a stock order, never override a known service designation.
    products = {p.id: p for p in Product.query.filter(Product.id.in_(ids)).all()}
    lines = []
    for item in items:
        product = products.get(int(item["product_id"]))
        if product is None or not product.is_active:
            raise ValidationError("Todos los productos seleccionados deben existir y estar activos.")
        if (product.id in service_product_ids or getattr(product, "is_service", False)
                or item.get("is_service", False)):
            raise ValidationError("Los servicios no generan \u00f3rdenes de compra de stock.")
        try:
            quantity = int(item.get("quantity"))
        except (TypeError, ValueError):
            raise ValidationError(f"{product.name}: cantidad inválida.")
        if quantity <= 0:
            raise ValidationError(f"{product.name}: la cantidad debe ser mayor que cero.")
        unit_cost = item.get("unit_cost")
        lines.append({"product_id": product.id, "quantity": quantity,
                      "unit_cost": str(unit_cost if unit_cost not in (None, "") else f"{Decimal(product.purchase_price):.2f}")})
    payload = {"supplier_id": supplier_id, "items": lines, "notes": notes or ""}
    if estimated_delivery_date:
        payload["estimated_delivery_date"] = estimated_delivery_date
    return payload
