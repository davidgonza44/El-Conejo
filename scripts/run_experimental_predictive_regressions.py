"""Run inspected synthetic-only predictive regressions with DB/.env/network blocked."""
import os
from pathlib import Path
import sys
import unittest

SUITES = (
    "test_experimental_demand",
    "test_forecast_advanced",
    "test_predictive_operational",
    "test_empirical_2026_backtest_runner",
    "test_empirical_2026_final_batch",
)


def offline_guard(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "sqlite3.connect"):
        raise RuntimeError("Database/network prohibited in synthetic regressions")
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        forbidden = {"instance", "uploads", "private_imports"}
        if path.name.lower().startswith(".env") or forbidden.intersection(part.lower() for part in path.parts):
            raise RuntimeError("Private/configuration file access prohibited")


def main():
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[variable] = "1"
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "scripts"))
    sys.addaudithook(offline_guard)
    suite = unittest.defaultTestLoader.loadTestsFromNames(SUITES)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
