"""HTTP suite against scripts/serve_settings_e2e.py (ferreteria_conejo_e2e ONLY).

Checks the real MariaDB path: app_settings roundtrip (JSON column), user management on the
live users API, and a real manual backup with mysqldump of the E2E database (file, sha256, size,
history row). Never touches ferreteria_conejo; the only database dumped is ferreteria_conejo_e2e.

Uso: python scripts/test_settings_e2e.py  (harness running on SETTINGS_E2E_PORT, default 5026)
"""
import hashlib
import os
import sys
from pathlib import Path

import requests

BASE = f"http://127.0.0.1:{os.environ.get('SETTINGS_E2E_PORT', '5026')}"
FAILURES = 0


def check(name, condition, detail=""):
    global FAILURES
    FAILURES += 0 if condition else 1
    print(f"[{'OK' if condition else 'FALLO'}] {name}{f' — {detail}' if detail else ''}")


def main() -> int:
    ctx = requests.get(f"{BASE}/__settings_e2e__/context", timeout=10).json()
    assert ctx["database"] == "ferreteria_conejo_e2e"
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"identifier": ctx["username"], "password": ctx["password"]}, timeout=10)
    check("login admin E2E", r.status_code == 200, str(r.status_code))

    r = s.get(f"{BASE}/api/settings", timeout=10).json()
    check("app_settings disponible en MariaDB", r["available"] is True)
    r = s.put(f"{BASE}/api/settings", json={"company.name": "Ferretería E2E C.A.", "alerts.low_stock_enabled": False,
                                          "security.password_min_length": 8}, timeout=10)
    check("PUT settings en MariaDB (columna JSON)", r.status_code == 200, r.text[:200])
    got = s.get(f"{BASE}/api/settings", timeout=10).json()["settings"]
    check("roundtrip JSON conserva tipos", got["company.name"] == "Ferretería E2E C.A." and got["alerts.low_stock_enabled"] is False
          and got["security.password_min_length"] == 8)
    alerts = s.get(f"{BASE}/api/shell/alerts", timeout=10).json()
    check("campana desactivada por configuración", alerts["count"] == 0 and alerts.get("disabled") is True)
    s.put(f"{BASE}/api/settings", json={"alerts.low_stock_enabled": True}, timeout=10)
    alerts = s.get(f"{BASE}/api/shell/alerts", timeout=10).json()
    check("campana vuelve a evaluar stock real (incluye el producto E2E en el conteo)",
          alerts.get("disabled") is not True and alerts["count"] == alerts["low_stock"] + alerts["out_of_stock"] >= 1 and alerts["items"])
    r = s.post(f"{BASE}/api/users", json={"name": "Corto", "email": f"corto_{ctx['run']}@example.invalid",
                                        "username": f"settings_e2e_{ctx['run']}_c", "password": "Solo7c", "role": "vendedor"}, timeout=10)
    check("alta de usuario respeta mínimo 8 en MariaDB", r.status_code == 400, str(r.status_code))
    s.put(f"{BASE}/api/settings", json={"security.password_min_length": 6}, timeout=10)

    before = s.get(f"{BASE}/api/settings/backups", timeout=10).json()
    check("respaldo disponible (tabla + mysqldump + MySQL)", before["available"] is True, str({k: before[k] for k in ('table_ready', 'mysqldump_configured')}))
    r = s.post(f"{BASE}/api/settings/backups", timeout=660)
    check("POST respaldo manual -> 201", r.status_code == 201, r.text[:300])
    if r.status_code == 201:
        backup = r.json()["backup"]
        path = Path(ctx["backup_dir"]) / backup["filename"]
        check("archivo creado en el directorio configurado", path.is_file(), str(path))
        check("solo se respaldó ferreteria_conejo_e2e", backup["database_name"] == "ferreteria_conejo_e2e"
              and backup["filename"].startswith("ferreteria_conejo_e2e_") and backup["filename"].endswith(".sql"))
        data = path.read_bytes()
        check("sha256 y tamaño coinciden", hashlib.sha256(data).hexdigest() == backup["sha256"] and len(data) == backup["byte_size"] > 0)
        check("el volcado contiene CREATE TABLE y sin contraseñas", b"CREATE TABLE" in data and b"MYSQL_PWD" not in data)
        check("kind/status honestos", backup["kind"] == "database" and backup["status"] == "completed" and backup["created_by"])
        after = s.get(f"{BASE}/api/settings/backups", timeout=10).json()
        check("historial real crece en 1", after["count"] == before["count"] + 1 and after["last"]["id"] == backup["id"])
        check("payload de respaldo sin rutas absolutas ni secretos", "\\" not in backup["filename"] and "password" not in r.text.lower())
    for route in ("/api/settings/backups/restore", "/api/settings/restore", "/api/backups/restore"):
        check(f"no existe {route}", s.post(f"{BASE}{route}", timeout=10).status_code in (404, 405))
    page = s.get(f"{BASE}/settings?tab=backup", timeout=10).text
    check("pestaña respaldo muestra historial real", "st-backup-history" in page and "ferreteria_conejo_e2e_" in page)
    print(f"\n{'PASS' if not FAILURES else 'FAIL'}: {FAILURES} fallos")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
