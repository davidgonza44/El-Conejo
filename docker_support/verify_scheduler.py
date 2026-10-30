"""Exercise the real portable scheduler and backup binary on the disposable DB."""
from datetime import datetime, timedelta
import os
import time

from sqlalchemy import text
from docker_support.guard import wait_database


def main():
    wait_database()
    if os.environ["DOCKER_ENV_KIND"] != "disposable":
        raise RuntimeError("Disposable database required")
    from app import create_app
    from app.extensions import db
    from app.models import User, Backup
    from app.services import portable_scheduler_service as state, settings_service as settings, scheduler_service
    app = create_app()
    with app.app_context():
        admin_id = db.session.execute(text("SELECT admin_id FROM container_installation WHERE id=1")).scalar_one()
        admin = db.session.get(User, admin_id)
        upcoming = (datetime.now(state.ZONE) + timedelta(minutes=1)).replace(second=0, microsecond=0)
        previous = Backup.query.filter_by(source="scheduled").count()
        settings.update({settings.BACKUP_FREQUENCY: "daily", settings.BACKUP_TIME: upcoming.strftime("%H:%M"),
            settings.BACKUP_KIND: "database", settings.BACKUP_RETENTION_DAYS: 0}, admin)
        try:
            scheduler_service.apply_schedule()
            deadline = time.monotonic() + 110
            while time.monotonic() < deadline:
                db.session.remove()
                rows = Backup.query.filter_by(source="scheduled").order_by(Backup.id).all()
                if len(rows) > previous and rows[-1].status == "completed":
                    break
                time.sleep(3)
            else:
                raise AssertionError("Portable scheduler did not complete its scheduled database backup")
            assert len(rows) == previous + 1
            assert rows[-1].byte_size > 0
            print("PASS: planificador portable ejecutó un respaldo real con mariadb-dump", flush=True)
            # An additional tick in the same slot must not produce a second backup.
            from docker_support.scheduler import tick
            with app.test_request_context():
                tick()
            db.session.remove()
            assert Backup.query.filter_by(source="scheduled").count() == previous + 1
            print("PASS: marca persistida del turno impide respaldo duplicado", flush=True)
        finally:
            db.session.remove()
            settings.update({settings.BACKUP_FREQUENCY: "off"}, db.session.get(User, admin_id))
            scheduler_service.apply_schedule()
            print("Planificación de prueba desactivada; respaldo sintético conservado en su volumen.")


if __name__ == "__main__":
    main()
