"""El Asistente propone; el Administrador decide sin editar la propuesta."""
from datetime import datetime
from decimal import Decimal

from flask_login import current_user

from app.extensions import db
from app.models import Product, User
from app.models.product_change import ProductChange
from app.services import product_service, product_state_history_service
from app.services.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.rbac_service import require_operational_permission
from app.utils.catalog_currency import PRICE_FIELDS, require_currency, validate_price_pairs


def locked_product(product_id):
    product = Product.query.filter_by(id=product_id).populate_existing().with_for_update().first()
    if product is None:
        raise NotFoundError("El producto no existe.")
    return product


def _text(value, field, maximum=None, *, required=False):
    if value is not None and not isinstance(value, str):
        raise ValidationError(f"El campo '{field}' debe ser texto.")
    value = (value or "").strip()
    if required and not value:
        raise ValidationError(f"El campo '{field}' no puede estar vacío.")
    if maximum and len(value) > maximum:
        raise ValidationError(f"El campo '{field}' admite hasta {maximum} caracteres.")
    return value or None


def _price(field, value):
    if value is None:
        raise ValidationError(f"El campo '{field}' debe ser un número.")
    value = product_service._non_negative_price({field: value}, field)
    if value >= Decimal("10000000000") or value != value.quantize(Decimal("0.01")):
        raise ValidationError(f"El campo '{field}' admite hasta diez enteros y dos decimales.")
    return str(value.quantize(Decimal("0.01")))


def _minimum(value):
    normalized = product_service._non_negative_int({"minimum_stock": value}, "minimum_stock")
    if isinstance(value, bool) or str(value).strip() != str(normalized) or normalized > 2147483647:
        raise ValidationError("El stock mínimo debe ser un entero no negativo válido.")
    return normalized


def _normalize_field(product, field, value):
    required_text = {"code": 50, "name": 150, "unit": 30}
    optional_text = {"description": None, "image_url": 255, "brand": 100}
    if field in required_text:
        cleaned = _text(value, field, required_text[field], required=True)
        return product_service._validate_code(cleaned, exclude_id=product.id) if field == "code" else cleaned
    if field == "brand" and value is None:
        # Contrato existente: null conserva la marca; "" la vacía explícitamente.
        return product.brand
    if field in optional_text:
        return _text(value, field, optional_text[field])
    if field == "category_id":
        return product_service._validate_category(value)
    if field in ("purchase_price", "sale_price"):
        return _price(field, value)
    if field in ("purchase_price_currency", "sale_price_currency"):
        return require_currency(value, field)
    if field == "minimum_stock":
        return _minimum(value)
    if type(value) is not bool:
        raise ValidationError("El campo 'is_active' debe ser booleano.")
    return value


def normalize_changes(product, data):
    if not isinstance(data, dict) or not data:
        raise ValidationError("Indique los campos que desea proponer.")
    if "current_stock" in data:
        raise ValidationError("El stock solo cambia mediante movimientos de inventario.")
    unknown = set(data) - product_service._UPDATABLE_FIELDS
    if unknown:
        raise ValidationError(f"Campos no permitidos: {', '.join(sorted(unknown))}.")
    validate_price_pairs(data)
    return {field: _normalize_field(product, field, value) for field, value in data.items()}


def _value(product, field):
    value = getattr(product, field)
    return str(Decimal(value).quantize(Decimal("0.01"))) if field in ("purchase_price", "sale_price") else value


def propose_change(product_id, data):
    require_operational_permission("products:write")
    try:
        product = locked_product(product_id)
        if product.approval_status != "approved":
            raise ConflictError("Solo se proponen modificaciones de productos aprobados.")
        if ProductChange.query.filter_by(product_id=product.id, status="pending").with_for_update().first():
            raise ConflictError("El producto ya tiene una propuesta pendiente de revisión.")
        normalized = normalize_changes(product, data)
        changes = {key: value for key, value in normalized.items() if _value(product, key) != value}
        # Keep both halves and their previous values even when just one changed.
        for price in PRICE_FIELDS:
            currency = price + "_currency"
            if price in changes or currency in changes:
                changes.update({price: normalized[price], currency: normalized[currency]})
        if not changes:
            raise ValidationError("La propuesta no contiene cambios respecto al producto vigente.")
        proposal = ProductChange(product_id=product.id, proposed_by_user_id=current_user.id,
                                 changes=changes, previous_values={key: _value(product, key) for key in changes})
        db.session.add(proposal)
        db.session.commit()
        return product
    except Exception:
        db.session.rollback()
        raise


def pending_product_ids(product_ids):
    return {row[0] for row in db.session.query(ProductChange.product_id).filter(
        ProductChange.product_id.in_(product_ids), ProductChange.status == "pending").all()} if product_ids else set()


def detail_payload(product):
    payload = product.to_dict()
    payload.update(proposed_by_user_id=product.proposed_by_user_id,
                   approved_by_user_id=product.approved_by_user_id,
                   approved_at=product.approved_at.isoformat() if product.approved_at else None,
                   rejection_reason=product.rejection_reason)
    latest = ProductChange.query.filter_by(product_id=product.id).order_by(ProductChange.id.desc()).first()
    payload["pending_change"] = latest.to_dict() if latest and latest.status == "pending" else None
    payload["last_change"] = latest.to_dict() if latest else None
    proposer_id = latest.proposed_by_user_id if latest else product.proposed_by_user_id
    proposer = db.session.get(User, proposer_id) if proposer_id else None
    payload["proposal_author"] = proposer.name if proposer else None
    payload["can_review_change"] = bool(latest and latest.status == "pending" and latest.proposed_by_user_id != current_user.id)
    payload["can_review_creation"] = product.approval_status == "pending" and product.proposed_by_user_id != current_user.id
    return payload


def _apply(product, proposal):
    if any(_value(product, key) != value for key, value in proposal.previous_values.items()):
        raise ConflictError("Los datos vigentes cambiaron. Rechace la propuesta y solicite una nueva.")
    data = dict(proposal.changes)
    if "brand" in data and data["brand"] is None:
        # El null almacenado procede de un borrado explícito con "". La regla
        # «null conserva la marca» solo se aplica al JSON entrante de la fuente.
        data["brand"] = ""
    changes = normalize_changes(product, data)
    previous_minimum, previous_active = product.minimum_stock, bool(product.is_active)
    for field, value in changes.items():
        setattr(product, field, Decimal(value) if field in ("purchase_price", "sale_price") else value)
    if previous_minimum != product.minimum_stock or previous_active != bool(product.is_active):
        product_state_history_service.record_change(
            product.id, previous_minimum, product.minimum_stock,
            previous_active, bool(product.is_active), datetime.utcnow())


def decide_change(product_id, proposal_id, *, approve, reason=None):
    require_operational_permission("products:approve")
    if type(proposal_id) is not int:
        raise ValidationError("Indique el ID de la propuesta que está revisando.")
    try:
        product = locked_product(product_id)
        proposal = ProductChange.query.filter_by(id=proposal_id, product_id=product.id).populate_existing().with_for_update().first()
        if proposal is None:
            raise NotFoundError("La propuesta no pertenece al producto.")
        if proposal.proposed_by_user_id == current_user.id:
            raise ValidationError("Quien propone no puede aprobar ni rechazar su propio cambio.", status_code=403)
        if proposal.status != "pending":
            raise ConflictError("La propuesta ya fue revisada.")
        if approve:
            if product.approval_status != "approved":
                raise ConflictError("El producto ya no está aprobado.")
            _apply(product, proposal)
        else:
            reason = _text(reason, "reason", 1000, required=True)
        proposal.status = "approved" if approve else "rejected"
        proposal.reviewed_by_user_id = current_user.id
        proposal.reviewed_at = datetime.utcnow()
        proposal.rejection_reason = None if approve else reason
        db.session.commit()
        return product
    except Exception:
        db.session.rollback()
        raise
