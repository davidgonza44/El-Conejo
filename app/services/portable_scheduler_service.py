"""Portable backup schedule registry, shared with a single locked worker.

Schedule registration remains explicit. Existing backup settings/services retain
their meaning; Monday weekly and day 1 monthly match the Windows task contract.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
import secrets
from zoneinfo import ZoneInfo

from flask import current_app
from app.services import settings_service as settings

ZONE = ZoneInfo("America/Caracas")


def directory():
    return Path(current_app.instance_path) / "scheduler"


def read(name):
    path = directory() / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write(name, data):
    folder = directory()
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / (name + "." + secrets.token_hex(6) + ".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(folder / name)


def next_run(schedule, after):
    if schedule.get("frequency", "off") == "off":
        return None
    hour, minute = map(int, schedule["time"].split(":"))
    for offset in range(33):
        candidate = (after + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        allowed = (schedule["frequency"] == "daily"
            or schedule["frequency"] == "weekly" and candidate.weekday() == 0
            or schedule["frequency"] == "monthly" and candidate.day == 1)
        if allowed and candidate > after:
            return candidate
    raise ValueError("Invalid backup frequency")


def register():
    frequency = settings.get(settings.BACKUP_FREQUENCY)
    now = datetime.now(ZONE)
    schedule = {"frequency": frequency, "time": settings.get(settings.BACKUP_TIME),
                "registered_at": now.isoformat()}
    write("schedule.json", schedule)
    return {"action": "removed" if frequency == "off" else "created", "task": status()}


def status():
    now = datetime.now(ZONE)
    schedule, worker = read("schedule.json"), read("worker.json")
    execution = read("execution.json")
    registered = schedule.get("frequency", "off") != "off"
    heartbeat = datetime.fromisoformat(worker["heartbeat"]) if worker.get("heartbeat") else None
    alive = heartbeat is not None and (now - heartbeat).total_seconds() < 90
    upcoming = next_run(schedule, now) if registered else None
    state = "Configurado" if alive else "Planificador no disponible"
    return {"supported": True, "registered": registered, "state": state if registered else "No configurado",
        "next_run": upcoming.isoformat() if upcoming else None, "last_run": execution.get("last_slot"),
        "last_result": execution.get("result"),
        "error": None if alive or not registered else "El proceso del planificador no responde."}
