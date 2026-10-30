"""La imagen completa un alta pendiente propia o se propone para aprobación.

Las imágenes vigentes nunca se borran al proponer su reemplazo o retiro.
"""
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.services import product_service, upload_service
from flask_login import current_user
from app.services.rbac_service import require_operational_permission
from app.services.exceptions import ConflictError, ValidationError

_MEDIA_PREFIX = "/media/products/"


def _target(product_id):
    require_operational_permission("products:write")
    from app.services.product_change_service import locked_product

    product = locked_product(product_id)
    if product.approval_status != "approved" and not (
        product.approval_status == "pending" and product.proposed_by_user_id == current_user.id
    ):
        raise ValidationError("Solo puede completar su propia propuesta de alta o proponer cambios de un producto aprobado.", status_code=403)
    return product


def _save_reference(product, image_url, changes=None):
    if product.approval_status == "approved":
        return product_service.update_product(product.id, {**(changes or {}), "image_url": image_url})
    if changes:
        raise ConflictError("El alta debe revisarse antes de proponer modificaciones de sus datos.")
    # La imagen completa el alta propia todavía inactiva; se aprueba junto con ella.
    product.image_url = image_url
    db.session.commit()
    return product


def set_image(product_id: int, file: FileStorage | None, changes=None):
    """Una imagen de un producto aprobado forma parte de la misma propuesta de datos."""
    product = _target(product_id)
    filename = upload_service.save_image(
        file, upload_service.PRODUCTS_FOLDER, prefix=f"p{product.id}"
    )
    try:
        return _save_reference(product, f"{_MEDIA_PREFIX}{filename}", changes)
    except Exception:
        db.session.rollback()
        upload_service.delete_image(upload_service.PRODUCTS_FOLDER, filename)
        raise


def remove_image(product_id: int):
    """Propone retirar la imagen; no borra el archivo vigente ni modifica datos activos."""
    return _save_reference(_target(product_id), None)
