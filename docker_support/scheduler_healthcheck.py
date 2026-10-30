"""Read worker liveness without importing the application or touching its state."""
from datetime import datetime, timezone
import json
from pathlib import Path


# Flask(__name__) in app/__init__.py uses this sibling instance directory.
# In the portable image it is /app/instance, the existing volume mount point.
HEARTBEAT_PATH = Path(__file__).resolve().parents[1] / "instance" / "scheduler" / "worker.json"


def main():
    try:
        record = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
        value = record["heartbeat"]
        if not isinstance(value, str):
            return 1
        heartbeat = datetime.fromisoformat(value)
        if heartbeat.tzinfo is None or heartbeat.utcoffset() is None:
            return 1
        age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
    except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError):
        # No payload, paths or exception details reach Docker's health log.
        return 1
    return 0 if 0 <= age < 60 else 1


if __name__ == "__main__":
    raise SystemExit(main())
