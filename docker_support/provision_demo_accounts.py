"""DEMO-only account provisioning; JSON secrets arrive on stdin, never argv/logs.

Used by provision_demo_credentials.ps1 after storing CurrentUser DPAPI secrets.
Existing role assignments and administrator credentials are never changed.
"""
import json
import os
import sys

from sqlalchemy import text

from docker_support.bootstrap import verify_installation
from docker_support.guard import validate_demo, validate_demo_url

ACCOUNTS = {
    "inventario": "demo_inventario",
    "asistente": "demo_asistente",
    "vendedor": "demo_vendedor",
    "soporte_ti": "demo_soporte",
}
SEED_ACCOUNTS = {"demo_compras": "inventario", "demo_ventas": "vendedor"}


def require(condition):
    if not condition:
        raise RuntimeError("DEMO identity/account precondition failed")


def preflight(payload):
    validate_demo()
    require(payload["environment_id"] == os.environ["ENVIRONMENT_ID"])
    verify_installation()
    from app import create_app
    from app.extensions import db

    app = create_app()
    with app.app_context():
        validate_demo_url(db.engine.url)
        require(app.config["APP_ENV"] == "demo")
    return app


def check_accounts(payload):
    from app.extensions import db
    from app.models import AuthIdentity, Role, User

    roles = {r.slug: sorted(r.permission_codes) for r in Role.query.filter_by(is_active=True)}
    require(set(roles) == {"admin", *ACCOUNTS})
    admin_id = db.session.execute(text("SELECT admin_id FROM container_installation WHERE id=1")).scalar_one()
    admin = db.session.get(User, admin_id)
    require(admin and admin.role == "admin" and admin.is_active)
    require(admin.username == payload["admin_username"] and admin.check_password(payload["admin_password"]))
    permitted_names = {admin.username, *SEED_ACCOUNTS, *ACCOUNTS.values()}
    users = User.query.all()
    require(all(u.username in permitted_names and u.is_active and u.email.endswith("@example.invalid") for u in users))
    require(len({u.role for u in users}) == len(users))
    for user in users:
        require(AuthIdentity.query.filter_by(user_id=user.id, provider="local").count() == 1)
        if user.id != admin.id:
            expected = SEED_ACCOUNTS[user.username] if user.username in SEED_ACCOUNTS else next(
                role for role, name in ACCOUNTS.items() if name == user.username)
            require(user.role == expected)
    return admin, roles


def provision(payload):
    from app.extensions import db
    from app.models import Role, User
    from app.services import auth_service, password_policy_service

    admin, roles_before = check_accounts(payload)
    admin_before = (admin.id, admin.username, admin.password_hash, admin.role)
    for role in ACCOUNTS:
        auth_service.validate_password(payload["passwords"][role])
    # Validate every collision before changing anything. Only the original seed
    # actors' unrecoverable random passwords may be replaced on first provisioning.
    for role, name in ACCOUNTS.items():
        user = User.query.filter_by(role=role).one_or_none()
        if user and user.username not in SEED_ACCOUNTS:
            require(user.username == name and user.check_password(payload["passwords"][role]))
    for role, name in ACCOUNTS.items():
        password = payload["passwords"][role]
        user = User.query.filter_by(role=role).one_or_none()
        if user is None:
            auth_service.register(dict(name="DEMO " + role, username=name,
                email=name + "@example.invalid", role=role, password=password), allowed_roles=(role,))
        elif user.username in SEED_ACCOUNTS:
            password_policy_service.apply(user, auth_service.validate_password(password, user))
            user.username, user.name = name, "DEMO " + role
            db.session.commit()
    require(admin_before == (admin.id, admin.username, admin.password_hash, admin.role))
    require(roles_before == {r.slug: sorted(r.permission_codes) for r in Role.query.filter_by(is_active=True)})
    require(User.query.count() == 5 and User.query.filter_by(is_active=True).count() == 5)


def main():
    payload = json.load(sys.stdin)
    app = preflight(payload)
    with app.app_context():
        if payload["action"] == "preflight":
            check_accounts(payload)
        elif payload["action"] == "provision":
            provision(payload)
        else:
            raise ValueError("Unsupported action")
    print("PASS: DEMO accounts verified; no credentials logged.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never relay SQLAlchemy parameters, payloads or connection details.
        print("DEMO provisioning refused. Verify installation, account state and credential ownership.")
        sys.exit(1)
