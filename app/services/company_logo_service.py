"""Decoded, normalized company images; only generated application-owned paths are used."""
import io
import re
import secrets
import warnings
from pathlib import Path

from flask import current_app, has_request_context, request, url_for
from PIL import Image, ImageOps, UnidentifiedImageError

from app.services.exceptions import ValidationError

MAX_BYTES = 2 * 1024 * 1024
_NAME = re.compile(r"company-logo-[a-f0-9]{32}\.webp")
_FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}
_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def validate_setting(value):
    if value == "" or isinstance(value, str) and _NAME.fullmatch(value):
        return value
    raise ValidationError("Logo de empresa no válido.")


def storage_root():
    return Path(current_app.config.get("COMPANY_LOGO_DIR") or
                Path(current_app.instance_path) / "uploads" / "company").resolve()


def owned_path(name):
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        return None
    root = storage_root()
    path = root / name
    if path.is_symlink() or path.resolve().parent != root:
        return None
    return path


def get_company_logo_path():
    from app.services import settings_service

    if has_request_context() and request.args.get("ref") == "1":
        return None
    path = owned_path(settings_service.get(settings_service.COMPANY_LOGO))
    return path if path and path.is_file() else None


def get_company_logo_url():
    path = get_company_logo_path()
    return url_for("settings.company_logo", version=path.name) if path else None


def normalize(upload):
    data = upload.stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValidationError("El archivo supera el límite de 2 MB.")
    expected = _FORMATS.get((upload.filename or "").rsplit(".", 1)[-1].lower())
    if not expected or upload.mimetype != _MIMES[expected]:
        raise ValidationError("Formato de imagen no permitido.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format != expected:
                    raise ValidationError("Formato de imagen no permitido.")
                if source.width * source.height > 16_000_000:
                    raise ValidationError("El archivo no es una imagen válida.")
                source.verify()
            with Image.open(io.BytesIO(data)) as source:
                source.load()
                normalized = ImageOps.exif_transpose(source)
                normalized = normalized.convert("RGBA" if "A" in normalized.getbands() or
                                                "transparency" in normalized.info else "RGB")
                normalized.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                normalized.save(output, "WEBP", lossless=True, method=4)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError("El archivo no es una imagen válida.") from exc


def store(data):
    name = f"company-logo-{secrets.token_hex(16)}.webp"
    path = owned_path(name)
    if path is None:
        raise OSError("Invalid application-owned logo path")
    created = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as output:
            created = True
            output.write(data)
    except OSError:
        if created:
            remove(name)
        raise
    return name


def remove(name):
    path = owned_path(name)
    if path:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # A committed replacement remains usable even if obsolete-file cleanup fails.
            current_app.logger.warning("No se pudo limpiar un logo de empresa obsoleto.")
