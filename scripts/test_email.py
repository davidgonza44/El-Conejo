"""Envía un correo de prueba real usando la configuración SMTP del .env.

Reutilizable para verificar la configuración de correo en cualquier momento.

Uso:
    python scripts/test_email.py destinatario@correo.com
    python scripts/test_email.py            (usa MAIL_USERNAME como destino)

Requiere MAIL_ENABLED=true y las variables MAIL_* completas en .env.
Nunca imprime MAIL_PASSWORD.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.services import email_service


def main() -> None:
    app = create_app()
    with app.app_context():
        if not app.config.get("MAIL_ENABLED"):
            print("[ERROR] MAIL_ENABLED=false. Active el envio en .env para esta prueba.")
            sys.exit(1)

        to = sys.argv[1] if len(sys.argv) > 1 else app.config.get("MAIL_USERNAME")
        if not to:
            print("[ERROR] Indique el destinatario: python scripts/test_email.py correo@destino.com")
            sys.exit(1)

        print(f"[INFO] Enviando correo de prueba a {email_service.mask_email(to)} ...")
        try:
            email_service.send_email(
                to=to,
                subject="Correo de prueba - Ferreteria El Conejo",
                html_body=(
                    "<h2>Ferretería El Conejo</h2>"
                    "<p>Este es un <strong>correo de prueba</strong> del sistema de inventario.</p>"
                    "<p>Si lo recibió, la configuración SMTP es correcta.</p>"
                ),
                text_body=(
                    "Ferretería El Conejo\n\n"
                    "Este es un correo de prueba del sistema de inventario.\n"
                    "Si lo recibió, la configuración SMTP es correcta.\n"
                ),
            )
        except email_service.EmailError as error:
            print(f"[ERROR] {error.message}")
            sys.exit(1)

        print("[OK] Correo aceptado por el servidor SMTP. Revise la bandeja de entrada (y spam).")


if __name__ == "__main__":
    main()
