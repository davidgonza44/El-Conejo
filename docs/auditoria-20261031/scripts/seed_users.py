"""Usuarios de auditoría con contraseña conocida, solo en el laboratorio."""
import os
from datetime import datetime
assert os.environ.get("AUDIT_LAB_DB") and os.environ.get("DB_NAME") == os.environ["AUDIT_LAB_DB"], "solo el laboratorio: defina AUDIT_LAB_DB con el nombre de la base aislada"
from app import create_app
from app.extensions import db
from app.models import User
app = create_app()
PASSWORD = os.environ["AUDIT_PASSWORD"]
with app.app_context():
    for role, name in (("admin", "Ana Auditora (Admin)"), ("admin", "Bruno Revisor (Admin 2)"),
                       ("asistente", "Carla Compras (Asistente)"), ("inventario", "Diego Almacén (Inventario)"),
                       ("vendedor", "Elena Ventas (Vendedor)"), ("soporte_ti", "Félix Soporte (TI)")):
        username = "aud_" + ("admin2" if "Admin 2" in name else role)
        user = User.query.filter_by(username=username).first() or User(username=username, email=username + "@example.invalid")
        user.name, user.role, user.is_active, user.email_verified = name, role, True, True
        user.set_password(PASSWORD)
        user.password_changed_at = datetime.utcnow()
        db.session.add(user)
        print(username, role)
    db.session.commit()
