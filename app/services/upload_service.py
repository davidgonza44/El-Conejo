"""Servicio de subida de imágenes (productos y fotos de perfil).

Reglas de seguridad:
- Extensiones permitidas: .jpg, .jpeg, .png, .webp (nunca .svg, .exe, .php...).
- Se valida el contenido real del archivo (magic bytes), no solo la extensión.
- Tamaño máximo: 2 MB.
- El nombre original del usuario NUNCA se usa para guardar: se genera un
  nombre único y seguro (prefijo + token aleatorio + extensión normalizada).
- Los archivos viven fuera de app/static, en <raíz>/uploads/<subcarpeta>/,
  y solo se sirven mediante rutas controladas (/media/...).
"""
import os
import secrets

from flask import current_app
from werkzeug.datastructures import FileStorage

from app.services.exceptions import ValidationError

MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

# Subcarpetas de uploads permitidas (nunca rutas arbitrarias).
PRODUCTS_FOLDER = "products"
USERS_FOLDER = "users"
_ALLOWED_FOLDERS = {PRODUCTS_FOLDER, USERS_FOLDER}


def uploads_root() -> str:
    """Carpeta <raíz del proyecto>/uploads (junto a app/, no dentro de static)."""
    project_root = os.path.dirname(current_app.root_path)
    return os.path.join(project_root, "uploads")


def folder_path(subfolder: str) -> str:
    if subfolder not in _ALLOWED_FOLDERS:
        raise ValidationError("Carpeta de uploads no permitida.")
    path = os.path.join(uploads_root(), subfolder)
    os.makedirs(path, exist_ok=True)
    return path


def _detect_image_type(header: bytes) -> str | None:
    """Identifica el tipo real de imagen por sus primeros bytes."""
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    return None


def validate_image(file: FileStorage | None) -> str:
    """Valida extensión, contenido real y tamaño. Devuelve la extensión final."""
    if file is None or not (file.filename or "").strip():
        raise ValidationError("Debe adjuntar un archivo de imagen en el campo 'image'.")

    original = file.filename.strip()
    if "." not in original:
        raise ValidationError(
            "El archivo no tiene extensión. Formatos permitidos: jpg, jpeg, png, webp."
        )
    ext = original.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"Formato '.{ext}' no permitido. Formatos permitidos: jpg, jpeg, png, webp."
        )

    # Tamaño real del stream (no se confía en Content-Length).
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size == 0:
        raise ValidationError("El archivo de imagen está vacío.")
    if size > MAX_IMAGE_BYTES:
        raise ValidationError(
            f"La imagen pesa {size / (1024 * 1024):.1f} MB y el máximo permitido es 2 MB."
        )

    # Contenido real: los magic bytes deben corresponder a un formato permitido.
    header = file.stream.read(12)
    file.stream.seek(0)
    detected = _detect_image_type(header)
    if detected is None:
        raise ValidationError(
            "El contenido del archivo no es una imagen válida (jpg, png o webp)."
        )

    # jpg y jpeg son el mismo formato: se normaliza la extensión detectada.
    if detected == "jpg" and ext in ("jpg", "jpeg"):
        return ext
    if detected != ext:
        raise ValidationError(
            f"El contenido del archivo ({detected}) no coincide con la extensión .{ext}."
        )
    return ext


def save_image(file: FileStorage, subfolder: str, prefix: str) -> str:
    """Guarda la imagen con nombre único y devuelve el nombre de archivo."""
    ext = validate_image(file)
    filename = f"{prefix}_{secrets.token_hex(8)}.{ext}"
    file.save(os.path.join(folder_path(subfolder), filename))
    return filename


def delete_image(subfolder: str, filename: str | None) -> None:
    """Borra un archivo de la carpeta permitida. Silencioso si no existe."""
    if not filename:
        return
    # Solo nombres simples: nunca rutas con separadores o '..'.
    if os.path.basename(filename) != filename:
        return
    path = os.path.join(folder_path(subfolder), filename)
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            current_app.logger.warning("No se pudo borrar el archivo %s", filename)
