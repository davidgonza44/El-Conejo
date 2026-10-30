"""Carga y confirmación de versiones históricas; nunca toca operación ni stock."""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from flask import current_app
from flask_login import current_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models.real_2026 import Real2026Daily, Real2026Import, Real2026Product
from app.services import real_2026_source_service as source
from app.services.exceptions import ApiError, ConflictError, NotFoundError, ValidationError
from app.utils.permissions import role_has_permission

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,200}\Z")
_PRODUCT_FIELDS = ("code", "name", "unit", "cohort", "identity_status", "unit_status",
                   "is_service", "included", "reason")
_CHUNK_SIZE = 10_000
_NO_ACTOR = object()


def _require(action, actor_id=_NO_ACTOR):
    if not current_user.is_authenticated:
        raise ValidationError("Autenticación requerida.", status_code=401)
    if not role_has_permission(current_user.role, "historical_imports:" + action):
        raise ValidationError("No tiene permiso para esta operación histórica.", status_code=403)
    if actor_id is not _NO_ACTOR and (type(actor_id) is not int or actor_id != current_user.id):
        raise ValidationError("El responsable no coincide con la sesión autenticada.", status_code=403)


def _public_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise NotFoundError("Lote histórico 2026 no encontrado.") from None


def get_batch(public_id, *, lock=False):
    _require("read")
    query = Real2026Import.query.filter_by(public_id=_public_id(public_id))
    if lock:
        query = query.populate_existing().with_for_update()
    batch = query.first()
    if batch is None:
        raise NotFoundError("Lote histórico 2026 no encontrado.")
    return batch


def _revision_parent(public_id, reason):
    if not public_id:
        if reason:
            raise ValidationError("La justificación de revisión requiere un lote anterior.")
        return None, None
    _require("review")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 2000:
        raise ValidationError("Indique el motivo de la revisión, de hasta 2000 caracteres.")
    parent = get_batch(public_id)
    if parent.status != "confirmed":
        raise ConflictError("Solo una versión confirmada vigente puede ser revisada.")
    return parent, reason.strip()


def _check_anchors(parsed, parent):
    if parent is None:
        return
    if any(parsed["source_hashes"].get(name) != parent.source_hashes.get(name)
           for name in source.ANCHOR_FILES):
        raise ConflictError("La revisión debe conservar el catálogo, el calendario y el manifiesto originales.")


def _directory(public_id):
    base = Path(current_app.instance_path).resolve()
    root = base / "real_2026"
    target = root / _public_id(public_id)
    if root.is_symlink() or target.is_symlink() or target.resolve().parent != root:
        raise ValidationError("El almacenamiento histórico no está disponible.")
    return target


def _safe_files(files):
    if not isinstance(files, dict) or not files:
        raise ValidationError("Adjunte los archivos del paquete histórico 2026.")
    for name, content in files.items():
        if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name) or ".." in name:
            raise ValidationError("El paquete contiene un nombre de archivo no permitido.")
        if not isinstance(content, bytes):
            raise ValidationError("El paquete debe contener archivos completos en formato binario.")


def _archive(public_id, files):
    folder = _directory(public_id)
    try:
        folder.mkdir(parents=True, exist_ok=False)
        for name, content in files.items():
            with (folder / name).open("xb") as stream:
                stream.write(content)
    except OSError:
        raise ApiError("No se pudo conservar la evidencia privada del paquete histórico.", 500) from None


def _verify_archive(batch):
    folder = _directory(batch.public_id)
    try:
        for name, expected in batch.source_hashes.items():
            if not _SAFE_NAME.fullmatch(name) or ".." in name:
                raise ConflictError("La referencia de evidencia histórica requiere revisión.")
            path = folder / name
            if path.is_symlink() or path.resolve().parent != folder:
                raise ConflictError("La evidencia histórica no coincide con el paquete validado.")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if not hmac.compare_digest(digest.hexdigest(), expected):
                raise ConflictError("La evidencia histórica cambió; la confirmación fue bloqueada.")
    except OSError:
        raise ConflictError("La evidencia privada del paquete no está disponible para confirmar.") from None


def _token():
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse(files, *, revision):
    try:
        return source.parse_package(files, allow_revision=revision)
    except ValueError as error:
        raise ValidationError(str(error)) from None


def _persist_products(batch_id, products):
    rows = [dict({key: product[key] for key in _PRODUCT_FIELDS}, batch_id=batch_id)
            for product in products]
    db.session.execute(Real2026Product.__table__.insert(), rows)
    ids = dict(db.session.query(Real2026Product.code, Real2026Product.id).filter_by(batch_id=batch_id).all())
    daily = []
    for product in products:
        if not product["included"]:
            continue
        for day, quantity in product["points"]:
            daily.append(dict(product_id=ids[product["code"]], event_date=day, quantity=quantity))
            if len(daily) == _CHUNK_SIZE:
                db.session.execute(Real2026Daily.__table__.insert(), daily)
                daily.clear()
    if daily:
        db.session.execute(Real2026Daily.__table__.insert(), daily)


def _duplicate(batch, parent, parsed):
    if batch.supersedes_import_id != (parent.id if parent else None):
        raise ConflictError("Este paquete ya pertenece a otra cadena de revisión.")
    if batch.source_hashes != parsed["source_hashes"]:
        raise ConflictError("El paquete ya existe con otra evidencia adjunta; no se reemplaza el original.")
    if batch.status != "preview":
        return batch, None, False
    token, batch.preview_token_hash = _token()
    batch.preview_token_expires_at = datetime.utcnow() + timedelta(minutes=15)
    db.session.commit()
    return batch, token, False


def preview(files, actor_id, supersedes_public_id=None, revision_reason=None):
    """Valida primero; conserva un paquete y sus series sin activar resultados."""
    _require("upload", actor_id)
    _safe_files(files)
    parent, reason = _revision_parent(supersedes_public_id, revision_reason)
    parsed = _parse(files, revision=parent is not None)
    _check_anchors(parsed, parent)
    try:
        existing = Real2026Import.query.filter_by(package_hash=parsed["package_hash"]).with_for_update().first()
        if existing:
            return _duplicate(existing, parent, parsed)
        token, token_hash = _token()
        batch = Real2026Import(
            public_id=str(uuid4()), package_hash=parsed["package_hash"],
            coverage_start=parsed["coverage_start"], coverage_end=parsed["coverage_end"],
            calendar=[day.isoformat() for day in parsed["calendar"]], summary=parsed["summary"],
            source_hashes=parsed["source_hashes"], provenance=parsed["provenance"],
            created_by_user_id=actor_id, supersedes_import_id=parent.id if parent else None,
            revision_reason=reason, preview_token_hash=token_hash,
            preview_token_expires_at=datetime.utcnow() + timedelta(minutes=15),
        )
        _archive(batch.public_id, files)
        db.session.add(batch)
        db.session.flush()
        _persist_products(batch.id, parsed["products"])
        db.session.commit()
        return batch, token, True
    except (IntegrityError, StaleDataError):
        db.session.rollback()
        raise ConflictError("Otra solicitud registró o cambió este paquete; vuelva a consultar el histórico.") from None
    except SQLAlchemyError:
        db.session.rollback()
        raise ApiError("No se pudo conservar la previsualización histórica 2026.", 500) from None
    except ApiError:
        db.session.rollback()
        raise


def _validate_token(batch, token):
    if not isinstance(token, str) or not 20 <= len(token) <= 200:
        raise ConflictError("El token de confirmación no es válido; genere una previsualización.")
    actual = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual, batch.preview_token_hash):
        raise ConflictError("El token no coincide con la previsualización vigente.")


def _confirm_parent(batch):
    if batch.supersedes_import_id is None:
        if Real2026Import.query.filter_by(status="confirmed").first():
            raise ConflictError("Ya existe una versión confirmada; indique una revisión explícita.")
        return
    parent = (Real2026Import.query.filter_by(id=batch.supersedes_import_id)
              .populate_existing().with_for_update().one())
    if parent.status != "confirmed":
        raise ConflictError("Otra revisión sustituyó el lote anterior; prepare nuevamente la revisión.")
    _check_anchors(dict(source_hashes=batch.source_hashes), parent)
    parent.status, parent.confirmed_slot = "superseded", None
    db.session.flush()  # Release the unique current slot before assigning it to its successor.


def confirm(public_id, token, actor_id):
    """Activa una sola versión; repetir el mismo token no duplica sus series."""
    _require("confirm", actor_id)
    try:
        batch = get_batch(public_id, lock=True)
        _validate_token(batch, token)
        if batch.status == "confirmed":
            return batch, False
        if batch.status != "preview":
            raise ConflictError("Una versión sustituida no puede confirmarse nuevamente.")
        if batch.preview_token_expires_at < datetime.utcnow():
            raise ConflictError("La previsualización expiró; cargue el mismo paquete para renovarla.")
        _verify_archive(batch)
        _confirm_parent(batch)
        batch.status, batch.confirmed_slot = "confirmed", 1
        batch.confirmed_by_user_id, batch.confirmed_at = actor_id, datetime.utcnow()
        db.session.commit()
        return batch, True
    except (IntegrityError, StaleDataError):
        db.session.rollback()
        raise ConflictError("Otra confirmación cambió la versión vigente; vuelva a consultar los lotes.") from None
    except SQLAlchemyError:
        db.session.rollback()
        raise ApiError("No se pudo confirmar el histórico 2026.", 500) from None
    except ApiError:
        db.session.rollback()
        raise


def _reversion_parent(batch):
    if batch.supersedes_import_id is None:
        return None
    parent = (Real2026Import.query.filter_by(id=batch.supersedes_import_id)
              .populate_existing().with_for_update().one())
    if parent.status != "superseded":
        raise ConflictError("El estado de la versión anterior cambió; la reversión requiere revisión.")
    _check_anchors(dict(source_hashes=batch.source_hashes), parent)
    _verify_archive(parent)
    return parent


def revert(public_id, reason, actor_id):
    """Retira solo la versión vigente y conserva datos, evidencia y responsable."""
    _require("revert", actor_id)
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 2000:
        raise ValidationError("Indique el motivo de la reversión, de hasta 2000 caracteres.")
    try:
        batch = get_batch(public_id, lock=True)
        if batch.status == "reverted":
            return batch, False
        if batch.status != "confirmed":
            raise ConflictError("Solo puede revertirse la versión confirmada vigente.")
        parent = _reversion_parent(batch)
        batch.status, batch.confirmed_slot = "reverted", None
        batch.reverted_by_user_id, batch.reverted_at = actor_id, datetime.utcnow()
        batch.reversion_reason = reason.strip()
        db.session.flush()
        if parent is not None:
            parent.status, parent.confirmed_slot = "confirmed", 1
        db.session.commit()
        return batch, True
    except (IntegrityError, StaleDataError):
        db.session.rollback()
        raise ConflictError("Otra operación cambió la versión vigente; vuelva a consultar los lotes.") from None
    except SQLAlchemyError:
        db.session.rollback()
        raise ApiError("No se pudo revertir el histórico 2026.", 500) from None
    except ApiError:
        db.session.rollback()
        raise


def serialize(batch):
    """Solo metadatos permitidos: ninguna ruta privada ni token de confirmación."""
    return dict(
        id=batch.public_id, status=batch.status, package_hash=batch.package_hash,
        coverage_start=batch.coverage_start.isoformat(), coverage_end=batch.coverage_end.isoformat(),
        summary=batch.summary, source_hashes=batch.source_hashes, provenance=batch.provenance,
        calendar_days=len(batch.calendar), created_at=batch.created_at.isoformat(),
        confirmed_at=batch.confirmed_at.isoformat() if batch.confirmed_at else None,
        reverted_at=batch.reverted_at.isoformat() if batch.reverted_at else None,
        reversion_reason=batch.reversion_reason,
        supersedes_import_id=batch.supersedes_import_id, revision_reason=batch.revision_reason,
        lock_version=batch.lock_version,
    )


def listing():
    _require("read")
    return [serialize(batch) for batch in Real2026Import.query.order_by(Real2026Import.id.desc()).limit(20)]
