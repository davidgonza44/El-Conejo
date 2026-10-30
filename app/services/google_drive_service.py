"""Copia de respaldos en Google Drive con OAuth dedicado (GOOGLE_DRIVE_*), separado del login OIDC.

Flujo: Conectar -> consentimiento (scope drive.file) -> callback -> tokens cifrados en
integration_connections -> subir respaldos a una carpeta propia -> Desconectar (revoca el token).
Ningún endpoint devuelve tokens. Sin credenciales configuradas el estado es "Proveedor no configurado".
"""
import json
import time
from datetime import datetime

import requests
from flask import current_app

from app.extensions import db, oauth
from app.models import Backup, IntegrationConnection
from app.models.integration_connection import PROVIDER_GOOGLE_DRIVE
from app.services import crypto_service
from app.services.exceptions import ApiError, ConflictError
from app.utils.schema import table_ready

_SCOPE = "openid email https://www.googleapis.com/auth/drive.file"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
_FOLDER_NAME = "Respaldos Ferretería El Conejo"
_TIMEOUT = 60
_REQUIRED = ("GOOGLE_DRIVE_CLIENT_ID", "GOOGLE_DRIVE_CLIENT_SECRET", "GOOGLE_DRIVE_REDIRECT_URI")


def register_oauth_client(app) -> None:
    oauth.register(
        name="google_drive",
        client_id=app.config.get("GOOGLE_DRIVE_CLIENT_ID"),
        client_secret=app.config.get("GOOGLE_DRIVE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": _SCOPE},
    )


def is_available() -> bool:
    return table_ready(IntegrationConnection.__tablename__)


def missing() -> list[str]:
    return [name for name in _REQUIRED if not current_app.config.get(name)]


def configured() -> bool:
    return not missing()


def ensure_configured() -> None:
    if missing():
        raise ApiError("Google Drive no configurado. Faltan variables en .env: " + ", ".join(missing()), status_code=503)


def connection() -> IntegrationConnection | None:
    if not is_available():
        return None
    return IntegrationConnection.query.filter_by(provider=PROVIDER_GOOGLE_DRIVE).first()


def status() -> dict:
    row = connection()
    return {"available": is_available(), "configured": configured(), "missing": missing(),
            "connected": row is not None and row.status == "connected",
            "connection": row.to_dict() if row is not None else None}


def authorize_redirect():
    ensure_configured()
    return oauth.google_drive.authorize_redirect(current_app.config["GOOGLE_DRIVE_REDIRECT_URI"],
                                                 access_type="offline", prompt="consent")


def handle_callback(user) -> IntegrationConnection:
    """Intercambia el code, guarda tokens cifrados y la cuenta conectada. Nunca devuelve el token."""
    ensure_configured()
    if not is_available():
        raise ApiError("La tabla integration_connections no existe. Ejecute scripts/migrate_settings_full.py.", status_code=503)
    token = oauth.google_drive.authorize_access_token()
    if not token.get("refresh_token") and connection() is not None:
        token["refresh_token"] = _load_token(connection()).get("refresh_token")
    if not token.get("refresh_token"):
        raise ApiError("Google no devolvió un refresh_token; repita la conexión concediendo acceso sin conexión.", status_code=502)
    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email")
    row = connection() or IntegrationConnection(provider=PROVIDER_GOOGLE_DRIVE)
    row.token_encrypted = crypto_service.encrypt(json.dumps({k: token.get(k) for k in ("access_token", "refresh_token",
                                                                                          "expires_at", "token_type")}))
    row.account_email, row.scope, row.status, row.status_detail = email, token.get("scope"), "connected", None
    row.connected_by_user_id = user.id
    db.session.add(row)
    db.session.commit()
    return row


def disconnect() -> None:
    row = connection()
    if row is None:
        raise ConflictError("Google Drive no está conectado.")
    token = _load_token(row)
    for key in ("refresh_token", "access_token"):
        if token.get(key):
            try:
                requests.post(_REVOKE_URL, params={"token": token[key]}, timeout=_TIMEOUT)
            except requests.RequestException:  # la desconexión local procede igual
                pass
            break
    db.session.delete(row)
    db.session.commit()


def _load_token(row: IntegrationConnection) -> dict:
    return json.loads(crypto_service.decrypt(row.token_encrypted))


def _mark(row: IntegrationConnection, status_value: str, detail: str) -> None:
    row.status, row.status_detail = status_value, detail[:255]
    db.session.commit()


def _access_token(row: IntegrationConnection) -> str:
    token = _load_token(row)
    if token.get("access_token") and (token.get("expires_at") or 0) > time.time() + 60:
        return token["access_token"]
    try:
        response = requests.post(_TOKEN_URL, data={
            "client_id": current_app.config["GOOGLE_DRIVE_CLIENT_ID"],
            "client_secret": current_app.config["GOOGLE_DRIVE_CLIENT_SECRET"],
            "refresh_token": token.get("refresh_token"), "grant_type": "refresh_token"}, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise ApiError(f"Google Drive: error de red al renovar el acceso ({type(exc).__name__}).", status_code=502)
    if response.status_code in (400, 401):
        _mark(row, "revoked", "El acceso fue revocado desde Google; vuelva a conectar.")
        raise ApiError("El acceso a Google Drive fue revocado. Vuelva a conectar la cuenta.", status_code=409)
    if response.status_code >= 400:
        raise ApiError(f"Google Drive respondió {response.status_code} al renovar el acceso.", status_code=502)
    data = response.json()
    token["access_token"] = data["access_token"]
    token["expires_at"] = int(time.time()) + int(data.get("expires_in", 3600))
    row.token_encrypted = crypto_service.encrypt(json.dumps(token))
    db.session.commit()
    return token["access_token"]


def _request(row: IntegrationConnection, method: str, url: str, **kwargs) -> requests.Response:
    """Una petición autenticada con un reintento ante 5xx/red; 401 marca la conexión como revocada."""
    last_error = None
    for attempt in range(2):
        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {_access_token(row)}"}
        try:
            response = requests.request(method, url, headers=headers, timeout=_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            last_error = f"error de red ({type(exc).__name__})"
            continue
        if response.status_code == 401:
            _mark(row, "revoked", "Google rechazó el token; vuelva a conectar.")
            raise ApiError("El acceso a Google Drive fue revocado. Vuelva a conectar la cuenta.", status_code=409)
        if response.status_code >= 500 and attempt == 0:
            last_error = f"Google respondió {response.status_code}"
            continue
        return response
    raise ApiError(f"Google Drive: {last_error}.", status_code=502)


def _folder_id(row: IntegrationConnection) -> str:
    if row.remote_folder_id:
        return row.remote_folder_id
    query = f"name = '{_FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    response = _request(row, "GET", _FILES_URL, params={"q": query, "fields": "files(id)", "spaces": "drive"})
    files = response.json().get("files", []) if response.ok else []
    if files:
        folder_id = files[0]["id"]
    else:
        created = _request(row, "POST", _FILES_URL, json={"name": _FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"})
        if not created.ok:
            raise ApiError(f"Google Drive no permitió crear la carpeta ({created.status_code}).", status_code=502)
        folder_id = created.json()["id"]
    row.remote_folder_id = folder_id
    db.session.commit()
    return folder_id


def upload_backup(record: Backup) -> Backup:
    """Sube el archivo del respaldo a la carpeta de Drive y guarda el id remoto/estado en el registro."""
    from app.services import backup_service

    row = connection()
    if row is None or row.status != "connected":
        raise ConflictError("Google Drive no está conectado.")
    path = backup_service.download_path(record)
    metadata = json.dumps({"name": record.filename, "parents": [_folder_id(row)]})
    with path.open("rb") as handle:
        response = _request(row, "POST", _UPLOAD_URL, files={
            "metadata": ("metadata", metadata, "application/json; charset=UTF-8"),
            "file": (record.filename, handle, "application/octet-stream")})
    if not response.ok:
        record.remote_status = "failed"
        db.session.commit()
        raise ApiError(f"La subida a Google Drive falló ({response.status_code}).", status_code=502)
    record.remote_file_id, record.remote_status = response.json().get("id"), "uploaded"
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return record
