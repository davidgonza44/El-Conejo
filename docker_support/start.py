"""Verify identity and migration head on every start, including direct restart."""
import os
from docker_support.guard import wait_database


def main():
    wait_database()
    from docker_support.bootstrap import verify_installation
    verify_installation()
    if os.environ.get("DOCKER_ENV_KIND") == "demo":
        from app import create_app
        from app.services import rbac_service
        app = create_app()
        with app.app_context():
            removed = rbac_service.revoke_withdrawn_customer_sales()
        if removed:
            print(f"DEMO: withdrawn customer-sales permission links removed: {removed}")
    os.execvp("gunicorn", ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1",
        "--threads", "4", "--timeout", "180", "--access-logfile", "-", "app:create_app()"])


if __name__ == "__main__":
    main()
