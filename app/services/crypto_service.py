"""Cifrado simétrico (Fernet) de secretos en reposo: TOTP y tokens OAuth.

La clave viene de SECRETS_ENCRYPTION_KEY (Fernet urlsafe-base64). Si falta, se deriva de SECRET_KEY
para desarrollo; en producción configure una clave dedicada (`python -c "from cryptography.fernet import
Fernet; print(Fernet.generate_key().decode())"`). Nunca se registra ni se devuelve.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from app.services.exceptions import ApiError


def _fernet() -> Fernet:
    configured = (current_app.config.get("SECRETS_ENCRYPTION_KEY") or "").strip()
    if configured:
        return Fernet(configured.encode("utf-8"))
    derived = hashlib.sha256(current_app.config["SECRET_KEY"].encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        raise ApiError("No fue posible descifrar un secreto almacenado: revise SECRETS_ENCRYPTION_KEY.",
                       status_code=500)
