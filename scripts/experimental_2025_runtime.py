"""Offline service loader and fail-closed execution guard; no Flask factory."""
import importlib
import os
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def offline_guard(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "sqlite3.connect"}:
        raise RuntimeError("Network and database access prohibited")
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if path.name.lower().startswith(".env") or any(
            part.lower() in {"instance", "uploads", "private_imports"}
            for part in path.parts
        ):
            raise RuntimeError("Private runtime/configuration access prohibited")


def activate_guard():
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    sys.addaudithook(offline_guard)


def service(name):
    """Relative imports resolve in an isolated namespace, never app/__init__.py."""
    package = "experimental2025_offline"
    if package not in sys.modules:
        module = types.ModuleType(package)
        module.__path__ = [str(ROOT / "app" / "services")]
        sys.modules[package] = module
    return importlib.import_module(package + "." + name)
