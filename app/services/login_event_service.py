"""Historial de inicios de sesión (éxitos y fallos) con motivo seguro, IP y dispositivo.

Nunca guarda credenciales, tokens ni códigos. Un umbral de fallos consecutivos por identificador
genera un evento de seguridad para los administradores.
"""
from datetime import datetime, timedelta

from flask import current_app, has_request_context, request

from app.extensions import db
from app.models import LoginEvent, User
from app.utils.schema import table_ready

FAILED_THRESHOLD = 5
FAILED_WINDOW_MINUTES = 15
_PAGE_MAX = 50

REASON_LABELS = {
    None: "Acceso correcto",
    "ok": "Acceso correcto",
    "invalid_credentials": "Credenciales inválidas",
    "inactive": "Usuario inactivo",
    "invalid_token": "Código inválido o vencido",
    "mfa_pending": "Pendiente de verificación 2FA",
    "mfa_failed": "Código 2FA incorrecto",
    "mfa_ok": "Verificación 2FA correcta",
    "expired_password": "Contraseña expirada",
}


def is_available() -> bool:
    return table_ready(LoginEvent.__tablename__)


def request_meta() -> tuple[str | None, str | None]:
    if not has_request_context():
        return None, None
    # IP observada, no verificada. La cabecera X-Forwarded-For la puede escribir el cliente: solo se considera
    # cuando TRUST_PROXY_HEADERS indica que un proxy autorizado la reescribe.
    forwarded = ""
    if current_app.config.get("TRUST_PROXY_HEADERS"):
        forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    ip = forwarded or request.remote_addr
    agent = (request.headers.get("User-Agent") or "")[:255] or None
    return (ip[:45] if ip else None), agent


def device_label(user_agent: str | None) -> str:
    """Etiqueta legible tipo "Windows / Chrome" a partir del User-Agent (sin GeoIP ni dependencias)."""
    ua = user_agent or ""
    low = ua.lower()
    os_name = next((label for needle, label in (("windows", "Windows"), ("android", "Android"), ("iphone", "iPhone"),
                                                ("ipad", "iPad"), ("mac os", "macOS"), ("macintosh", "macOS"),
                                                ("linux", "Linux"), ("cros", "ChromeOS")) if needle in low), "Sistema desconocido")
    if "edg/" in low:
        browser = "Edge"
    elif "opr/" in low or "opera" in low:
        browser = "Opera"
    elif "firefox/" in low:
        browser = "Firefox"
    elif "chrome/" in low or "crios/" in low:
        browser = "Chrome"
    elif "safari/" in low:
        browser = "Safari"
    elif "python-requests" in low or "playwright" in low or "headless" in low:
        browser = "Cliente automatizado"
    else:
        browser = "Navegador desconocido"
    return f"{os_name} / {browser}"


def record(identifier: str, success: bool, *, reason: str | None = None, user: User | None = None,
           provider: str = "local") -> None:
    """Registra el evento en su propia transacción (persiste aunque el flujo lance un error después)."""
    if not is_available():
        return
    ip, agent = request_meta()
    event = LoginEvent(user_id=user.id if user is not None else None, identifier=(identifier or "")[:120],
                       provider=provider, success=success, reason=reason, ip_address=ip, user_agent=agent,
                       device_label=device_label(agent))
    db.session.add(event)
    db.session.commit()
    if not success:
        _check_failed_threshold(identifier)


def _check_failed_threshold(identifier: str) -> None:
    since = datetime.utcnow() - timedelta(minutes=FAILED_WINDOW_MINUTES)
    failures = LoginEvent.query.filter(LoginEvent.identifier == identifier, LoginEvent.success.is_(False),
                                       LoginEvent.created_at >= since).count()
    if failures != FAILED_THRESHOLD:
        return
    from app.services import notification_service

    notification_service.security_event(
        "Intentos de acceso fallidos",
        f"Se registraron {failures} intentos fallidos para '{identifier}' en {FAILED_WINDOW_MINUTES} minutos.",
        severity="warning", dedupe_key=f"login_failures:{identifier}:{datetime.utcnow():%Y%m%d%H}")


def list_events(user: User, page: int = 1, per_page: int = 10, filters=None) -> dict:
    """Only events attributed to this account; an editable email is not ownership."""
    per_page = max(1, min(per_page, _PAGE_MAX))
    page = max(1, page)
    if not is_available():
        return {"items": [], "page": 1, "pages": 0, "total": 0, "available": False}
    query = LoginEvent.query.filter(LoginEvent.user_id == user.id)
    query = _filter_events(query, filters or {})
    query = query.order_by(LoginEvent.created_at.desc(), LoginEvent.id.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    if page > max(1, pagination.pages):
        pagination = query.paginate(page=max(1, pagination.pages), per_page=per_page, error_out=False)
    items = []
    for event in pagination.items:
        item = event.to_dict()
        item["reason_label"] = REASON_LABELS.get(event.reason, "Intento de inicio de sesión fallido")
        item["description"] = ("Inicio de sesión exitoso desde " + item["device"] if event.success
                               else "Intento de inicio de sesión fallido")
        item["date_label"] = event.created_at.strftime("%d/%m/%Y %H:%M")
        items.append(item)
    return {"items": items, "page": pagination.page, "pages": pagination.pages, "total": pagination.total,
            "available": True}


def _filter_events(query, filters):
    if filters.get("type") not in (None, "", "login"):
        return query.filter(db.false())
    status = filters.get("status")
    if status in ("success", "failed"):
        query = query.filter(LoginEvent.success.is_(status == "success"))
    for key in ("date_from", "date_to"):
        if not filters.get(key):
            continue
        try:
            day = datetime.strptime(filters[key], "%Y-%m-%d")
            boundary = day + timedelta(days=1) if key == "date_to" else day
        except (ValueError, OverflowError):
            return query.filter(db.false())
        query = query.filter(LoginEvent.created_at < boundary if key == "date_to" else LoginEvent.created_at >= boundary)
    search = (filters.get("search") or "").strip()[:120]
    if search:
        term = search.lower()
        matches = [LoginEvent.device_label.ilike("%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", escape="\\")]
        if term in "inicio de sesión":
            matches.append(db.true())
        if term in "inicio de sesión exitoso desde":
            matches.append(LoginEvent.success.is_(True))
        if term in "intento de inicio de sesión fallido":
            matches.append(LoginEvent.success.is_(False))
        reasons = [reason for reason, label in REASON_LABELS.items() if reason and term in label.lower()]
        matches.append(LoginEvent.reason.in_(reasons))
        query = query.filter(db.or_(*matches))
    return query
