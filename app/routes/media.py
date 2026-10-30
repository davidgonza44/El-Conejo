"""Sirve las imágenes subidas (uploads/) de forma controlada.

Solo se sirven archivos de las subcarpetas permitidas (products, users) y
send_from_directory garantiza que no se pueda escapar de la carpeta
(rechaza '..' y rutas absolutas). Requiere sesión activa.
"""
from flask import Blueprint, send_from_directory

from app.services import upload_service
from app.utils.auth_decorators import login_required

media_bp = Blueprint("media", __name__, url_prefix="/media")


@media_bp.get("/products/<path:filename>")
@login_required
def product_image(filename: str):
    return send_from_directory(
        upload_service.folder_path(upload_service.PRODUCTS_FOLDER),
        filename,
        max_age=3600,
    )


@media_bp.get("/users/<path:filename>")
@login_required
def user_photo(filename: str):
    return send_from_directory(
        upload_service.folder_path(upload_service.USERS_FOLDER),
        filename,
        max_age=3600,
    )
