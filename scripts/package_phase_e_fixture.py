"""Allowlisted local code bundle: excludes environment, private data and caches."""
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "approval_test_support.py", "migrate_reconciliation_cuts.py", "reconciliation_file_bridge.py",
    "test_reconciliation_automatic_sqlite.py", "test_reconciliation_core.py",
    "test_reconciliation_file_bridge.py", "test_reconciliation_intake.py",
    "test_reconciliation_automatic_mariadb.py", "test_phase_e_reconciliation.py",
    "serve_phase_e_reconciliation.py", "phase_e_fixture.py", "verify_phase_e_restart.py",
)


def main():
    output = ROOT / ".tmp/phase-e-20261009/source.tar"
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [path for path in (ROOT / "app").rglob("*")
             if path.is_file() and not path.is_symlink()
             and path.suffix in (".py", ".html", ".css", ".js")]
    files.append(ROOT / "app/static/img/logo_oficial_el_conejo.png")
    files.append(ROOT / "docker_support/scheduler.py")
    files.extend(ROOT / "scripts" / name for name in SCRIPTS if (ROOT / "scripts" / name).is_file())
    with tarfile.open(output, "w") as archive:
        for path in sorted(files):
            assert not any(part in ("instance", "uploads", "private_imports", "__pycache__")
                           for part in path.relative_to(ROOT).parts)
            archive.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
    print(f"Code-only fixture bundle: {len(files)} files; no environment or private data.")


if __name__ == "__main__":
    main()
