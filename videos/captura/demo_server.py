"""Servidor de demostración para los videos: el arnés E2E (SQLite temporal) con datos de demo_seed.py.

Uso: .venv/Scripts/python -B videos/captura/demo_server.py  (puerto E2E_PORT, por defecto 5099)
"""
import logging
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "e2e" / "harness"))
sys.path.insert(0, str(HERE))

import server  # noqa: E402
from demo_seed import seed  # noqa: E402

server.seed = seed

if __name__ == "__main__":
    port = int(os.environ.get("E2E_PORT", "5099"))
    application = server.make_app()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    print(f"Demo lista en http://127.0.0.1:{port}", flush=True)
    application.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
