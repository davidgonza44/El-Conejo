"""Notificaciones persistentes (bandeja/campana) y despacho por canales externos según la matriz.

Reglas:
- Un evento se genera solo ante una condición real (stock, ventas, respaldos, seguridad).
- ``dedupe_key`` evita repetir una notificación mientras la anterior siga sin leer o tenga < 24 h.
- Los canales externos se despachan una vez por evento hacia el destinatario configurado; el correo
  respeta la frecuencia (inmediato aquí, diario/semanal vía scripts/send_notification_digest.py).
- Los fallos de notificación nunca interrumpen la operación de negocio que los originó.
"""
import logging
from datetime import datetime, timedelta

from flask import current_app, render_template, url_for
from sqlalchemy import func

from app.extensions import db
from app.models import Notification, NotificationDelivery, Product, Sale, SaleLine, User
from app.models.notification import (CHANNEL_APP, CHANNEL_EMAIL, EVENT_CRITICAL, EVENT_LABELS, EVENT_LOW_STOCK,
                                     EVENT_MAINTENANCE, EVENT_RECONCILIATION, EVENT_SALES, EVENT_SECURITY, EVENTS,
                                     EXTERNAL_CHANNELS)
from app.models.user import ROLE_VENDEDOR
from app.services import notification_providers, settings_service
from app.services.exceptions import NotFoundError, ValidationError
from app.utils.permissions import PRODUCTS_READ, SALES_READ, SETTINGS_READ, role_has_permission
from app.utils.schema import table_ready

logger = logging.getLogger(__name__)
_DEDUPE_HOURS = 24
_PAGE_MAX = 50
_BELL_LIMIT = 8
_RECENT_SALES_DAYS = 30


def is_available() -> bool:
    return table_ready(Notification.__tablename__) and table_ready(NotificationDelivery.__tablename__)


def _settings() -> dict:
    return settings_service.get_all()


def _channel_on(event: str, channel: str, settings: dict) -> bool:
    return bool(settings[settings_service.CHANNEL_ENABLED_KEYS[channel]]
                and settings[settings_service.matrix_key(event, channel)])


def _users_with(permission: str) -> list[User]:
    return [u for u in User.query.filter(User.is_active.is_(True)).all() if role_has_permission(u.role, permission)]


def _deduped(user_id: int, dedupe_key: str) -> bool:
    since = datetime.utcnow() - timedelta(hours=_DEDUPE_HOURS)
    return db.session.query(Notification.id).filter(
        Notification.user_id == user_id, Notification.dedupe_key == dedupe_key,
        db.or_(Notification.read_at.is_(None), Notification.created_at >= since)).first() is not None


def emit(event: str, title: str, message: str, *, severity: str = "info", recipients: list[User] | None = None,
         permission: str | None = None, entity_type: str | None = None, entity_id: int | None = None,
         href: str | None = None, dedupe_key: str | None = None) -> list[Notification]:
    """Crea la notificación para cada destinatario (si algún canal del evento está activo) y despacha externos."""
    if event not in EVENTS or not is_available():
        return []
    settings = _settings()
    active_channels = [c for c in (CHANNEL_APP, *EXTERNAL_CHANNELS) if _channel_on(event, c, settings)]
    if not active_channels:
        return []
    if recipients is None:
        recipients = _users_with(permission or SETTINGS_READ)
    created = []
    now = datetime.utcnow()
    for user in {u.id: u for u in recipients if u is not None and u.is_active}.values():
        if dedupe_key and _deduped(user.id, dedupe_key):
            continue
        row = Notification(user_id=user.id, event=event, severity=severity, title=title[:160], message=message[:500],
                           entity_type=entity_type, entity_id=entity_id, href=href, dedupe_key=dedupe_key,
                           read_at=None if CHANNEL_APP in active_channels else now)
        db.session.add(row)
        created.append(row)
    if not created:
        return []
    db.session.flush()
    _dispatch_external(created[0], [c for c in active_channels if c != CHANNEL_APP], settings)
    db.session.commit()
    return created


def upsert_reconciliation_cut(cut, title: str, message: str, *, severity: str = "info") -> None:
    """One app-only summary per cut/authorized user; caller owns the transaction and cut lock."""
    if not is_available() or not settings_service.get(settings_service.APP_NOTIFICATIONS):
        return
    key = f"reconciliation-cut:{cut.id}"
    href = f"/reconciliation?detail={cut.run_id}" if cut.run_id else f"/reconciliation?cut={cut.id}"
    now = datetime.utcnow()
    for user in _users_with("reconciliation:read"):
        row = Notification.query.filter_by(user_id=user.id, event=EVENT_RECONCILIATION, dedupe_key=key).first()
        if row is None:
            db.session.add(Notification(user_id=user.id, event=EVENT_RECONCILIATION, dedupe_key=key,
                                        entity_type="reconciliation_cut", entity_id=cut.id, href=href,
                                        title=title[:160], message=message[:500], severity=severity))
        elif (row.title, row.message, row.severity, row.href) != (title[:160], message[:500], severity, href):
            row.title, row.message, row.severity, row.href = title[:160], message[:500], severity, href
            row.read_at, row.created_at = None, now


def _dispatch_external(carrier: Notification, channels: list[str], settings: dict) -> None:
    for channel in channels:
        if channel == CHANNEL_EMAIL and settings[settings_service.DIGEST_FREQUENCY] != "immediate":
            db.session.add(NotificationDelivery(notification_id=carrier.id, channel=channel, status="queued",
                                                detail="Pendiente del resumen programado"))
            continue
        db.session.add(_deliver(carrier, channel, settings))


def _deliver(notification: Notification, channel: str, settings: dict) -> NotificationDelivery:
    to = notification_providers.destination(channel, settings)
    text = f"{notification.title}\n{notification.message}"
    html = None
    if channel == CHANNEL_EMAIL and settings[settings_service.EMAIL_FORMAT] == "html":
        html = render_template("emails/notification_event.html", notification=notification, app_name=_app_name(),
                               event_label=EVENT_LABELS.get(notification.event, notification.event))
    try:
        detail = notification_providers.send(channel, to, subject=f"[{_app_name()}] {notification.title}", text=text, html=html)
        return NotificationDelivery(notification_id=notification.id, channel=channel, status="sent", detail=detail[:255])
    except notification_providers.ProviderError as exc:
        return NotificationDelivery(notification_id=notification.id, channel=channel, status="failed", detail=exc.message[:255])


def _app_name() -> str:
    return current_app.config.get("MAIL_FROM_NAME") or "Ferretería El Conejo"


# ---- eventos de dominio (condiciones reales) ----
def _safe(fn, *args, **kwargs):
    """Las notificaciones jamás rompen la operación que las origina."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 - se registra y se sigue
        db.session.rollback()
        logger.exception("Fallo al generar notificación (%s).", getattr(fn, "__name__", fn))
        return None


def _recently_sold(product_id: int) -> bool:
    since = datetime.utcnow() - timedelta(days=_RECENT_SALES_DAYS)
    return db.session.query(func.count(SaleLine.id)).join(Sale, Sale.id == SaleLine.sale_id).filter(
        SaleLine.product_id == product_id, Sale.sold_at >= since).scalar() > 0


def product_stock_changed(product: Product) -> None:
    """Stock bajo/agotado y producto crítico (agotado con ventas en los últimos 30 días)."""
    _safe(_product_stock_changed, product)


def _product_stock_changed(product: Product) -> None:
    if not product.is_active:
        return
    status = product.stock_status
    href = url_for("pages.products", search=product.name)
    recipients = _users_with(PRODUCTS_READ)
    if status == "agotado":
        emit(EVENT_LOW_STOCK, f"Producto agotado: {product.name}", f"{product.name} ({product.code}) quedó sin existencias.",
             severity="critical", recipients=recipients, entity_type="product", entity_id=product.id, href=href,
             dedupe_key=f"stock:{product.id}:agotado")
        if _recently_sold(product.id):
            emit(EVENT_CRITICAL, f"Producto crítico: {product.name}",
                 f"{product.name} está agotado y registró ventas en los últimos {_RECENT_SALES_DAYS} días.",
                 severity="critical", recipients=recipients, entity_type="product", entity_id=product.id, href=href,
                 dedupe_key=f"critical:{product.id}")
    elif status == "bajo_stock":
        emit(EVENT_LOW_STOCK, f"Stock bajo: {product.name}",
             f"{product.name} tiene {product.current_stock} unidades (mínimo {product.minimum_stock}).",
             severity="warning", recipients=recipients, entity_type="product", entity_id=product.id, href=href,
             dedupe_key=f"stock:{product.id}:bajo")


def sync_stock_alerts() -> None:
    """Refleja en la bandeja el estado actual de stock (misma consulta de Product Core que usa la campana)."""
    from app.services import product_service

    def run():
        summary = product_service.products_summary()
        ids = [item["id"] for item in summary["inventory_alerts"]]
        for product in Product.query.filter(Product.id.in_(ids)).all() if ids else []:
            _product_stock_changed(product)
    _safe(run)


def _sale_created_recipients(sale: Sale) -> list[User]:
    """Dueño de la venta, más admin e inventario (sales:read que no es otro vendedor)."""
    recipients = {user.id: user for user in _users_with(SALES_READ) if user.role != ROLE_VENDEDOR}
    owner = db.session.get(User, sale.seller_id)
    if owner is not None and owner.is_active:
        recipients[owner.id] = owner
    return list(recipients.values())


def sale_created(sale: Sale) -> None:
    def run():
        emit(EVENT_SALES, f"Nueva venta {sale.sale_number}", f"Venta a {sale.customer.name} por {sale.total:.2f}.",
             severity="success", recipients=_sale_created_recipients(sale), entity_type="sale", entity_id=sale.id,
             href=url_for("pages.sale_detail", sale_number=sale.sale_number), dedupe_key=f"sale:{sale.id}")
        for line in sale.lines:
            _product_stock_changed(line.product)
    _safe(run)


def order_event(order, title: str, message: str) -> None:
    _safe(emit, EVENT_SALES, title, message, severity="info", recipients=_users_with(SALES_READ), entity_type="order",
          entity_id=order.id, href=url_for("pages.sales_orders"), dedupe_key=f"order:{order.id}:{title}")


def maintenance_event(title: str, message: str, *, severity: str = "info", dedupe_key: str | None = None) -> None:
    _safe(emit, EVENT_MAINTENANCE, title, message, severity=severity, permission=SETTINGS_READ, dedupe_key=dedupe_key)


def security_event(title: str, message: str, *, severity: str = "info", actor: User | None = None,
                   recipients: list[User] | None = None, dedupe_key: str | None = None) -> None:
    """Por defecto llega a quienes ven Configuración; ``recipients`` restringe (p. ej. solo la cuenta afectada)."""
    def run():
        targets = list(recipients) if recipients is not None else _users_with(SETTINGS_READ)
        if actor is not None and getattr(actor, "id", None) is not None and all(t.id != actor.id for t in targets):
            targets.append(actor)
        emit(EVENT_SECURITY, title, message, severity=severity, recipients=targets, dedupe_key=dedupe_key)
    _safe(run)


# ---- bandeja del usuario ----
def unread_count(user: User) -> int:
    if not is_available():
        return 0
    return Notification.query.filter_by(user_id=user.id, read_at=None).count()


def bell(user: User) -> dict:
    """Campana: no leídas primero. Sincroniza antes el estado real de stock para no perder alertas."""
    if not is_available():
        return {"available": False, "unread": 0, "items": []}
    if settings_service.get(settings_service.APP_NOTIFICATIONS):
        sync_stock_alerts()
    rows = (Notification.query.filter_by(user_id=user.id)
            .order_by(Notification.read_at.isnot(None), Notification.created_at.desc(), Notification.id.desc())
            .limit(_BELL_LIMIT).all())
    return {"available": True, "unread": unread_count(user), "items": [r.to_dict() for r in rows],
            "app_enabled": settings_service.get(settings_service.APP_NOTIFICATIONS)}


def list_for_user(user: User, page: int = 1, per_page: int = 6, event: str | None = None) -> dict:
    if not is_available():
        return {"items": [], "page": 1, "pages": 0, "total": 0, "unread": 0, "available": False}
    query = Notification.query.filter_by(user_id=user.id)
    if event:
        if event not in (*EVENTS, EVENT_RECONCILIATION):
            raise ValidationError("Evento desconocido.")
        query = query.filter_by(event=event)
    pagination = (query.order_by(Notification.created_at.desc(), Notification.id.desc())
                  .paginate(page=max(1, page), per_page=max(1, min(per_page, _PAGE_MAX)), error_out=False))
    return {"items": [r.to_dict() for r in pagination.items], "page": pagination.page, "pages": pagination.pages,
            "total": pagination.total, "unread": unread_count(user), "available": True}


def get_for_user(user: User, notification_id: int) -> Notification:
    row = Notification.query.filter_by(user_id=user.id, id=notification_id).first() if is_available() else None
    if row is None:
        raise NotFoundError("La notificación no existe.")
    return row


def detail(user: User, notification_id: int) -> dict:
    row = get_for_user(user, notification_id)
    data = row.to_dict()
    data["deliveries"] = [{"channel": d.channel, "status": d.status, "detail": d.detail, "created_at": d.created_at.isoformat()}
                          for d in row.deliveries]
    return data


def mark_read(user: User, notification_id: int) -> Notification:
    row = get_for_user(user, notification_id)
    if row.read_at is None:
        row.read_at = datetime.utcnow()
        db.session.commit()
    return row


def mark_all_read(user: User) -> int:
    if not is_available():
        return 0
    count = Notification.query.filter_by(user_id=user.id, read_at=None).update({Notification.read_at: datetime.utcnow()})
    db.session.commit()
    return count


# ---- prueba desde Configuración ----
def send_test(user: User, channels: list) -> list[dict]:
    """Envía una notificación de prueba real por cada canal solicitado y devuelve el resultado por canal."""
    if not isinstance(channels, list) or not channels:
        raise ValidationError("Seleccione al menos un canal.")
    settings = _settings()
    results = []
    for channel in channels:
        if channel not in (CHANNEL_APP, *EXTERNAL_CHANNELS):
            raise ValidationError(f"Canal desconocido: {channel}.")
        if not settings[settings_service.CHANNEL_ENABLED_KEYS[channel]]:
            results.append({"channel": channel, "ok": False, "detail": "Canal desactivado en la configuración."})
            continue
        if channel == CHANNEL_APP:
            results.append(_test_app(user))
            continue
        try:
            detail_text = notification_providers.send(
                channel, notification_providers.destination(channel, settings),
                subject=f"[{_app_name()}] Prueba de notificaciones",
                text=f"Prueba de notificaciones enviada por {user.name} desde Configuración.")
            results.append({"channel": channel, "ok": True, "detail": detail_text})
        except notification_providers.ProviderError as exc:
            results.append({"channel": channel, "ok": False, "detail": exc.message})
    return results


def _test_app(user: User) -> dict:
    if not is_available():
        return {"channel": CHANNEL_APP, "ok": False, "detail": "La tabla notifications no existe (migración pendiente)."}
    row = Notification(user_id=user.id, event=EVENT_MAINTENANCE, severity="info", title="Prueba de notificaciones",
                       message=f"Notificación de prueba generada por {user.name} desde Configuración.",
                       href=url_for("pages.settings", tab="notifications"))
    db.session.add(row)
    db.session.commit()
    return {"channel": CHANNEL_APP, "ok": True, "detail": "Notificación creada en la campana."}
