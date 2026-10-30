"""Portable worker: backups, daily closes and separately activated inventory cuts."""
from datetime import date, datetime, timedelta, timezone
from threading import Event, Thread

from docker_support.guard import wait_database
from docker_support.bootstrap import verify_installation


def heartbeat(app, stopped):
    from app.services import portable_scheduler_service as state
    with app.app_context():
        while not stopped.is_set():
            record = state.read("worker.json")
            record["heartbeat"] = datetime.now(state.ZONE).isoformat()
            state.write("worker.json", record)
            stopped.wait(20)


def tick():
    from app.services import portable_scheduler_service as state
    from scripts.run_scheduled_backup import run
    schedule = state.read("schedule.json")
    if schedule.get("frequency", "off") == "off":
        return
    now = datetime.now(state.ZONE)
    history = state.read("execution.json")
    last = max(schedule["registered_at"], history.get("last_slot", schedule["registered_at"]))
    due = state.next_run(schedule, datetime.fromisoformat(last))
    if due is None or due > now:
        return
    # Like a native scheduled task, missed runs are not replayed in a burst.
    if (now - due).total_seconds() > 60:
        state.write("execution.json", {"last_slot": now.isoformat(), "result": "missed"})
        return
    state.write("execution.json", {"last_slot": due.isoformat(), "result": "running"})
    result = run()
    state.write("execution.json", {"last_slot": due.isoformat(), "result": result})


def reconciliation_tick(now=None):
    """Fixed Caracas slots; durable cut identity survives worker restart.

    Empty activation disables capture. Manual CSV mode never polls the inbox
    or discovers missed historical slots; only pending captures can expire.
    """
    from flask import current_app
    from app.services import reconciliation_automation_service as automation
    if not current_app.config.get("RECONCILIATION_ACTIVATED_ON"):
        return
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("El reloj del planificador debe incluir zona horaria")
    local = now.astimezone(automation.CARACAS)
    if local.date() < date.fromisoformat(current_app.config["RECONCILIATION_ACTIVATED_ON"]):
        return
    utc = now.astimezone(timezone.utc).replace(tzinfo=None)
    for slot in automation.TIMES:
        scheduled = automation.scheduled_at(local.date(), slot)
        if automation.slot_enabled(scheduled) and scheduled <= utc < scheduled + automation.PUNCTUALITY:
            automation.capture_cut(local.date(), slot)
    if not current_app.config.get("RECONCILIATION_AUTO_INGEST_ENABLED", True):
        automation.expire_pending_captures(now)
        return
    last = current_app.extensions.get("reconciliation_last_ingest")
    if last is None or now >= last + timedelta(minutes=5):
        automation.ingest_due(now)
        current_app.extensions["reconciliation_last_ingest"] = now


def daily_snapshot_tick(now=None):
    from scripts.take_inventory_snapshot import run
    return run(now)


def main():
    wait_database()
    verify_installation()
    from app import create_app
    from app.services import portable_scheduler_service as state
    from app.services.predictive_job_service import process_lock
    app, stopped = create_app(), Event()
    with app.app_context():
        state.directory().mkdir(parents=True, exist_ok=True)
        with process_lock(state.directory(), stopped):
            Thread(target=heartbeat, args=(app, stopped), daemon=True).start()
            while True:
                try:
                    with app.test_request_context():
                        result = daily_snapshot_tick()
                        if result.get("snapshot_date") and not result["skipped"]:
                            print(f"Daily close saved: {result['snapshot_date']}, {result['inserted']} products.")
                except Exception as error:
                    print(f"Daily close failed ({type(error).__name__}); will retry within its slot.")
                try:
                    with app.test_request_context():
                        reconciliation_tick()
                        tick()
                except Exception as error:
                    print(f"Scheduled task failed ({type(error).__name__}); no private details logged.")
                stopped.wait(20)


if __name__ == "__main__":
    main()
