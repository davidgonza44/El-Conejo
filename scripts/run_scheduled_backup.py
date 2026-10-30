"""Respaldo automático: lo invoca la tarea programada de Windows registrada desde Configuración > Respaldo.

Secuencia (usa la configuración guardada en app_settings):
1. Si la frecuencia es 'off' no hace nada.
2. Crea el respaldo del tipo configurado (backup.auto_kind) a nombre del primer administrador activo.
3. Aplica la retención (elimina solo respaldos dentro de BACKUP_DIR más antiguos que backup.retention_days).
4. Si la ubicación incluye Google Drive y hay conexión, sube el archivo.
5. Genera una notificación de mantenimiento con el resultado (éxito o fallo).

Registrar manualmente (o use el botón "Registrar tarea" en la pantalla):
    schtasks /Create /TN "FerreteriaConejo-RespaldoAutomatico" /SC DAILY /ST 02:00 ^
        /TR "\"<ruta>\\.venv\\Scripts\\python.exe\" \"<ruta>\\scripts\\run_scheduled_backup.py\"" /F
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.models import User
from app.services import backup_service, google_drive_service, notification_service, settings_service
from app.services.exceptions import ApiError
from app.utils.permissions import BACKUPS_CREATE, role_has_permission


def run() -> int:
    frequency = settings_service.get(settings_service.BACKUP_FREQUENCY)
    if frequency == "off":
        print("[SKIP] Respaldo automático desactivado (backup.auto_frequency=off).")
        return 0
    actor = next((u for u in User.query.filter(User.is_active.is_(True)).order_by(User.id) if role_has_permission(u.role, BACKUPS_CREATE)), None)
    if actor is None:
        print("[FAIL] No existe ningún usuario activo con permiso backups:create.")
        return 1
    try:
        record = backup_service.create_backup(actor, kind=settings_service.get(settings_service.BACKUP_KIND), source="scheduled")
    except ApiError as exc:
        notification_service.maintenance_event("Respaldo automático fallido", exc.message, severity="critical")
        print(f"[FAIL] {exc.message}")
        return 1
    removed = backup_service.apply_retention()
    detail = f"{record.filename} ({record.byte_size} bytes); retención: {removed} eliminado(s)."
    if settings_service.get(settings_service.BACKUP_LOCATION) == "local_drive" and google_drive_service.status()["connected"]:
        try:
            google_drive_service.upload_backup(record)
            detail += " Copia en Google Drive: subida."
        except ApiError as exc:
            detail += f" Copia en Google Drive: falló ({exc.message})."
    notification_service.maintenance_event("Respaldo automático completado", detail, severity="success")
    print(f"[OK] {detail}")
    return 0


if __name__ == "__main__":
    app = create_app()
    with app.app_context(), app.test_request_context():
        sys.exit(run())
