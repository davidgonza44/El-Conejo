"""Respaldo automático mediante schtasks o el worker portable del contenedor.

Ambos ejecutan el servicio existente con la frecuencia/hora/retención guardadas.
"""
import platform
import re
import subprocess
import sys
from pathlib import Path

from flask import current_app

from app.services import settings_service
from app.services.exceptions import ApiError, ValidationError

_TIMEOUT = 30
_FREQUENCY_ARGS = {"daily": ["/SC", "DAILY"], "weekly": ["/SC", "WEEKLY", "/D", "MON"], "monthly": ["/SC", "MONTHLY", "/D", "1"]}


def supported() -> bool:
    return _portable() or platform.system() == "Windows"


def _portable() -> bool:
    return current_app.config.get("BACKUP_SCHEDULER") == "portable"


def task_name() -> str:
    return current_app.config.get("BACKUP_TASK_NAME") or "FerreteriaConejo-RespaldoAutomatico"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["schtasks", *args], capture_output=True, text=True, timeout=_TIMEOUT, shell=False, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ApiError(f"No fue posible ejecutar schtasks ({type(exc).__name__}).", status_code=500)


def _parse_list(output: str) -> dict:
    values = {}
    for line in output.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            values[key.strip().lower()] = value.strip()
    return values


def task_status() -> dict:
    """Consulta real de la tarea: registrada, estado, próxima y última ejecución (sin inventar valores)."""
    if _portable():
        from app.services import portable_scheduler_service
        return portable_scheduler_service.status()
    if not supported():
        return {"supported": False, "registered": False, "state": "No soportado en este sistema", "next_run": None,
                "last_run": None, "last_result": None, "error": None}
    completed = _run(["/Query", "/TN", task_name(), "/FO", "LIST", "/V"])
    if completed.returncode != 0:
        return {"supported": True, "registered": False, "state": "No configurado", "next_run": None, "last_run": None,
                "last_result": None, "error": None}
    values = _parse_list(completed.stdout)
    next_run = _first(values, ("next run time", "próxima ejecución", "hora próxima ejecución"))
    last_result = _first(values, ("last result", "último resultado"))
    state = _first(values, ("status", "estado")) or "Configurado"
    error = None
    if last_result not in (None, "", "0", "267011", "267009"):  # 267011: nunca ejecutada; 267009: en ejecución
        error = f"Último resultado de la tarea: {last_result}"
    return {"supported": True, "registered": True, "state": state, "next_run": next_run,
            "last_run": _first(values, ("last run time", "última ejecución", "hora última ejecución")),
            "last_result": last_result, "error": error}


def _first(values: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key in values and values[key]:
            return values[key]
    return None


def schedule_status() -> dict:
    s = settings_service
    return {"frequency": s.get(s.BACKUP_FREQUENCY), "time": s.get(s.BACKUP_TIME), "kind": s.get(s.BACKUP_KIND),
            "retention_days": s.get(s.BACKUP_RETENTION_DAYS), "task_name": task_name(), "task": task_status()}


def _command() -> str:
    script = Path(current_app.root_path).parent / "scripts" / "run_scheduled_backup.py"
    return f'"{sys.executable}" "{script}"'


def apply_schedule() -> dict:
    """Crea/actualiza la tarea según la configuración guardada; con frecuencia 'off' la elimina."""
    if _portable():
        from app.services import portable_scheduler_service
        return portable_scheduler_service.register()
    if not supported():
        raise ValidationError("El planificador de respaldos no está disponible en este servidor.", status_code=503)
    frequency = settings_service.get(settings_service.BACKUP_FREQUENCY)
    if frequency == "off":
        completed = _run(["/Delete", "/TN", task_name(), "/F"])
        removed = completed.returncode == 0
        return {"action": "removed" if removed else "absent", "task": task_status()}
    hour = settings_service.get(settings_service.BACKUP_TIME)
    if not re.fullmatch(r"\d{2}:\d{2}", hour):
        raise ValidationError("Hora de respaldo inválida.")
    completed = _run(["/Create", "/TN", task_name(), *_FREQUENCY_ARGS[frequency], "/ST", hour, "/TR", _command(), "/F"])
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise ApiError("No fue posible registrar la tarea programada: " + (detail[-1] if detail else "error de schtasks."),
                       status_code=500)
    return {"action": "created", "task": task_status()}
