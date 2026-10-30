"""Envía el resumen de notificaciones por correo (frecuencia diaria/semanal de Configuración > Notificaciones).

Pensado para el Programador de tareas de Windows (una ejecución diaria a la hora configurada):
    schtasks /Create /TN "FerreteriaConejo-ResumenNotificaciones" /SC DAILY /ST 08:00 ^
        /TR "\"D:\\TESIS CODIGO\\.venv\\Scripts\\python.exe\" \"D:\\TESIS CODIGO\\scripts\\send_notification_digest.py\""

Comportamiento:
- Toma las entregas de correo en estado "queued" (eventos ocurridos con frecuencia diaria/semanal).
- Con frecuencia semanal solo envía los lunes (salvo --force). Con "immediate" no hace nada.
- Envía UN correo al destinatario configurado y marca las entregas como sent/failed. Nunca imprime secretos.
- --dry-run: muestra cuántos eventos se enviarían sin enviar ni modificar nada.
"""
import argparse
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flask import current_app, render_template

from app import create_app
from app.extensions import db
from app.models import Notification, NotificationDelivery
from app.models.notification import CHANNEL_EMAIL
from app.services import notification_providers, settings_service


def _queued():
    return (NotificationDelivery.query.filter_by(channel=CHANNEL_EMAIL, status="queued")
            .join(Notification).order_by(Notification.created_at.asc()).all())


def run(dry_run: bool, force: bool) -> int:
    settings = settings_service.get_all()
    frequency = settings[settings_service.DIGEST_FREQUENCY]
    if frequency == "immediate":
        print("[SKIP] La frecuencia configurada es 'Inmediato': los correos ya se envían al ocurrir el evento.")
        return 0
    if frequency == "weekly" and date.today().weekday() != 0 and not force:
        print("[SKIP] Frecuencia semanal: el resumen se envía los lunes (use --force para enviarlo hoy).")
        return 0
    deliveries = _queued()
    if not deliveries:
        print("[OK] No hay notificaciones pendientes de resumen.")
        return 0
    to = notification_providers.destination(CHANNEL_EMAIL, settings)
    items = [d.notification for d in deliveries]
    print(f"[INFO] {len(items)} evento(s) pendientes para el destinatario configurado.")
    if dry_run:
        print("[DRY-RUN] No se envía ni se modifica nada.")
        return 0
    app_name = current_app.config.get("MAIL_FROM_NAME") or "Ferretería El Conejo"
    text = render_template("emails/notification_digest.txt", items=items, frequency=frequency, app_name=app_name)
    html = None
    if settings[settings_service.EMAIL_FORMAT] == "html":
        html = render_template("emails/notification_digest.html", items=items, frequency=frequency, app_name=app_name)
    try:
        detail = notification_providers.send(CHANNEL_EMAIL, to, subject=f"[{app_name}] Resumen de notificaciones",
                                             text=text, html=html)
        status = "sent"
    except notification_providers.ProviderError as exc:
        detail, status = exc.message, "failed"
    for delivery in deliveries:
        delivery.status, delivery.detail, delivery.created_at = status, detail[:255], datetime.utcnow()
    db.session.commit()
    print(f"[{'OK' if status == 'sent' else 'FAIL'}] {detail}")
    return 0 if status == "sent" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Envía aunque no sea el día del resumen semanal.")
    args = parser.parse_args()
    app = create_app()
    with app.app_context(), app.test_request_context():
        sys.exit(run(args.dry_run, args.force))


if __name__ == "__main__":
    main()
