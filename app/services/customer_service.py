"""Reglas de negocio del directorio de clientes.

- RIF normalizado a ``L-DDDDDDDD-D`` (letra V/E/J/P/G, 6-9 dígitos, dígito verificador).
  Se valida el formato y la unicidad; no se verifica el dígito de control en v1.
- Correo normalizado en minúsculas y único cuando existe. Teléfono opcional y no único,
  con el mismo formato que exigen las notas de entrega.
- El cliente ``is_walk_in`` no se renombra, desactiva, borra ni duplica.
- Un cliente con ventas, cotizaciones, pedidos o notas de entrega se desactiva; uno sin uso puede borrarse.
"""
import re

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import DeliveryNote, Order, Quote, Sale
from app.models.customer import WALK_IN_NAME, Customer
from app.services.exceptions import ConflictError, NotFoundError, ValidationError

_RIF_RE = re.compile(r"^([VEJPG])-?(\d{6,9})-?(\d)$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Compartido con las notas de entrega: un solo formato de teléfono en todo el sistema.
PHONE_RE = re.compile(r"\+?[0-9][0-9 ()\-.]{6,29}")
PHONE_MESSAGE = "El teléfono solo admite dígitos, espacios, paréntesis, guiones y un + inicial (p. ej. 0414-1234567)."
_DUPLICATE = "Ya existe un cliente con ese RIF o correo."
_DEFAULT_PER_PAGE = 8
_MAX_PER_PAGE = 100
SORT_COLUMNS = {"name": Customer.name, "created_at": Customer.created_at, "tax_id": Customer.tax_id}


def normalize_tax_id(value) -> str | None:
    """``j123456789`` / ``J-12345678-9`` / ``J 12345678 9`` -> ``J-12345678-9``. Vacío -> None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("El campo 'tax_id' (RIF) debe ser texto.")
    compact = re.sub(r"[\s.]", "", value).upper()
    if not compact:
        return None
    match = _RIF_RE.fullmatch(compact)
    if not match:
        raise ValidationError("El RIF debe tener el formato J-12345678-9 (letra V, E, J, P o G).")
    letter, digits, check = match.groups()
    return f"{letter}-{digits}-{check}"


def _text(data: dict, field: str, max_length: int, required: bool = False) -> str | None:
    value = data.get(field)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"El campo '{field}' debe ser texto.")
    value = value.strip()
    if required and not value:
        raise ValidationError(f"El campo '{field}' es obligatorio.")
    if len(value) > max_length:
        raise ValidationError(f"El campo '{field}' no puede superar {max_length} caracteres.")
    return value or None


def _email(data: dict) -> str | None:
    value = _text(data, "email", 120)
    if value is None:
        return None
    value = value.lower()
    if not _EMAIL_RE.fullmatch(value):
        raise ValidationError("El correo no tiene un formato válido.")
    return value


def _unique(column, value, exclude_id, label):
    if value is None:
        return
    query = Customer.query.filter(column == value)
    if exclude_id is not None:
        query = query.filter(Customer.id != exclude_id)
    if query.first() is not None:
        raise ConflictError(f"Ya existe un cliente con el {label} '{value}'.")


def _apply_fields(customer: Customer, data: dict, exclude_id=None) -> None:
    if "name" in data or exclude_id is None:
        customer.name = _text(data, "name", 150, required=True)
    if "tax_id" in data or exclude_id is None:
        tax_id = normalize_tax_id(data.get("tax_id"))
        _unique(Customer.tax_id, tax_id, exclude_id, "RIF")
        customer.tax_id = tax_id
    if "email" in data or exclude_id is None:
        email = _email(data)
        _unique(Customer.email, email, exclude_id, "correo")
        customer.email = email
    if "phone" in data or exclude_id is None:
        phone = _text(data, "phone", 30)
        if phone and not PHONE_RE.fullmatch(phone):
            raise ValidationError(PHONE_MESSAGE)
        customer.phone = phone
    if "is_active" in data:
        if not isinstance(data["is_active"], bool):
            raise ValidationError("El campo 'is_active' debe ser booleano.")
        customer.is_active = data["is_active"]


def _reject_walk_in_fields(data: dict) -> None:
    if data.get("is_walk_in"):
        raise ValidationError("El cliente 'Público en general' ya existe y no puede duplicarse.")
    if (data.get("name") or "").strip().casefold() == WALK_IN_NAME.casefold():
        raise ConflictError(f"'{WALK_IN_NAME}' es el cliente genérico protegido; ya existe.")


def get_customer(customer_id) -> Customer:
    customer = db.session.get(Customer, customer_id)
    if customer is None:
        raise NotFoundError(f"El cliente con id {customer_id} no existe.")
    return customer


def walk_in_customer() -> Customer | None:
    return Customer.query.filter(Customer.is_walk_in.is_(True)).first()


def _commit(conflict_message: str) -> None:
    """Confirma; si la base rechaza por unicidad o vínculo (p. ej. alta simultánea), responde 409."""
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ConflictError(conflict_message) from None
    except Exception:
        db.session.rollback()
        raise


def create_customer(data: dict) -> Customer:
    _reject_walk_in_fields(data)
    customer = Customer()
    _apply_fields(customer, data)
    db.session.add(customer)
    _commit(_DUPLICATE)
    return customer


def update_customer(customer_id, data: dict) -> Customer:
    customer = get_customer(customer_id)
    if customer.is_walk_in:
        forbidden = {"name", "tax_id", "is_active", "is_walk_in"} & set(data)
        if forbidden:
            raise ConflictError(
                f"'{WALK_IN_NAME}' es el cliente genérico: no se renombra, desactiva ni cambia de RIF."
            )
    else:
        _reject_walk_in_fields({k: v for k, v in data.items() if k in ("name", "is_walk_in")})
    _apply_fields(customer, data, exclude_id=customer.id)
    _commit(_DUPLICATE)
    return customer


def _has_documents(customer_id: int) -> bool:
    """Ventas, cotizaciones, pedidos y notas de entrega apuntan al cliente: borrarlo rompería su histórico."""
    return any(db.session.query(model.id).filter(model.customer_id == customer_id).first() is not None
               for model in (Sale, Quote, Order, DeliveryNote))


def delete_customer(customer_id) -> dict:
    """Con documentos asociados: desactiva (histórico intacto). Sin uso: borra la fila."""
    customer = get_customer(customer_id)
    if customer.is_walk_in:
        raise ConflictError(f"'{WALK_IN_NAME}' no puede eliminarse ni desactivarse.")
    if _has_documents(customer.id):
        customer.is_active = False
        action = "deactivated"
    else:
        db.session.delete(customer)
        action = "deleted"
    _commit("El cliente tiene documentos asociados y no puede borrarse; desactívelo.")
    return {"id": customer_id, "action": action}


def _positive_int(value, field):
    if isinstance(value, bool) or not (isinstance(value, int) or isinstance(value, str) and value.strip().isdigit()):
        raise ValidationError(f"El parámetro '{field}' debe ser un entero positivo.")
    value = int(value)
    if value < 1:
        raise ValidationError(f"El parámetro '{field}' debe ser un entero positivo.")
    return value


def list_customers(params: dict) -> dict:
    """Búsqueda por nombre/RIF/teléfono/correo, estado, orden y paginación en servidor."""
    query = Customer.query
    search = (params.get("search") or "").strip()
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        term = f"%{escaped}%"
        query = query.filter(or_(*[
            column.ilike(term, escape="\\")
            for column in (Customer.name, Customer.tax_id, Customer.phone, Customer.email)
        ]))
    status = (params.get("status") or "").strip().lower()
    if status == "active":
        query = query.filter(Customer.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(Customer.is_active.is_(False))
    elif status:
        raise ValidationError("Estado inválido. Valores permitidos: active, inactive.")

    sort = params.get("sort") or "name"
    column = SORT_COLUMNS.get(sort.lstrip("-"))
    if column is None or sort.startswith("--"):
        raise ValidationError("Orden de clientes no permitido.")
    order = (column.desc(), Customer.id.desc()) if sort.startswith("-") else (column.asc(), Customer.id.asc())
    # El cliente genérico siempre encabeza la lista para que sea fácil de elegir.
    query = query.order_by(Customer.is_walk_in.desc(), *order)

    total = query.order_by(None).count()
    page = _positive_int(params.get("page", 1), "page")
    per_page = _positive_int(params.get("per_page", _DEFAULT_PER_PAGE), "per_page")
    if per_page > _MAX_PER_PAGE:
        raise ValidationError(f"per_page debe estar entre 1 y {_MAX_PER_PAGE}.")
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "count": len(items),
        "items": [c.to_dict() for c in items],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }
