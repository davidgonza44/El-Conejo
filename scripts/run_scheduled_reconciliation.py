"""Task Scheduler entry point. Deployment must create four capture triggers and a five-minute ingest trigger.

This script never installs tasks or connects to the legacy system. Capture accepts a
single fixed slot, and the service rejects calls outside its two-minute window.
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.services import reconciliation_automation_service as automation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--capture", choices=tuple(automation.TIMES), help="One nominal Caracas cut")
    group.add_argument("--ingest", action="store_true", help="Inspect published CSVs; never capture late stock")
    args = parser.parse_args()
    if os.environ.get("CONEJO_CONTAINER") == "1":
        from docker_support.guard import wait_database
        from docker_support.bootstrap import verify_installation
        wait_database()
        verify_installation()
    app = create_app()
    with app.app_context():
        if args.capture:
            today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Caracas")).date()
            cut = automation.capture_cut(today, args.capture)
            print(f"Cut {cut.id}: {cut.snapshot_status}")
            return 0 if cut.snapshot_status == "captured" else 1
        if not app.config.get("RECONCILIATION_AUTO_INGEST_ENABLED", True):
            parser.error("La recepción automática está desactivada; cargue el CSV desde Cortes y conciliaciones.")
        automation.ingest_due()
        print("Ingestion review finished.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
