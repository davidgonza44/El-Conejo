"""Alinea la descripción persistida de los roles del sistema con SYSTEM_ROLE_DESCRIPTIONS.

Cambio de datos puntual y reversible: solo actualiza roles.description de los cinco roles del sistema
(is_system=1). No toca permisos, usuarios, roles personalizados ni ninguna otra tabla.

    python -B scripts/migrate_role_descriptions_20261018.py                      # solo muestra el plan
    python -B scripts/migrate_role_descriptions_20261018.py --apply --rollback-file ARCHIVO.json
    python -B scripts/migrate_role_descriptions_20261018.py --revert ARCHIVO.json

--apply y --revert se ejecutan solo dentro del entorno DEMO validado (docker_support.guard) y requieren
autorización expresa. El archivo de reversión guarda los textos anteriores; --revert solo los restaura si
la descripción actual sigue siendo la aplicada por esta migración.
"""
import argparse
import json
import os
import sys
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

_SELECT = "SELECT slug, description FROM roles WHERE is_system = 1 AND slug = :slug"
_UPDATE = "UPDATE roles SET description = :new WHERE is_system = 1 AND slug = :slug AND description {cond}"


def plan(connection, descriptions: dict[str, str]) -> list[dict]:
    """Roles del sistema cuya descripción difiere de la vigente en el código."""
    changes = []
    for slug, new in sorted(descriptions.items()):
        row = connection.execute(text(_SELECT), {"slug": slug}).first()
        if row is not None and row.description != new:
            changes.append({"slug": slug, "old": row.description, "new": new})
    return changes


def _set(connection, slug: str, new: str, expected) -> int:
    params = {"slug": slug, "new": new}
    if expected is not None:
        params["expected"] = expected
    cond = "IS NULL" if expected is None else "= :expected"
    return connection.execute(text(_UPDATE.format(cond=cond)), params).rowcount


def apply_changes(connection, changes: list[dict]) -> int:
    """Actualiza solo si el texto sigue siendo el observado en el plan (sin pisar ediciones concurrentes)."""
    updated = sum(_set(connection, c["slug"], c["new"], c["old"]) for c in changes)
    if updated != len(changes):
        raise RuntimeError("Una descripción cambió durante la migración; se revierte todo.")
    return updated


def revert_changes(connection, changes: list[dict]) -> int:
    """Restaura el texto anterior solo donde sigue la descripción aplicada por esta migración."""
    return sum(_set(connection, c["slug"], c["old"], c["new"]) for c in changes)


def _engine():
    from docker_support.guard import validate_demo

    validate_demo()  # antes de importar configuración o abrir la primera conexión
    from docker_support.bootstrap import engine

    return engine()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--revert", metavar="ARCHIVO")
    parser.add_argument("--rollback-file", metavar="ARCHIVO")
    args = parser.parse_args()
    from app.services.rbac_service import SYSTEM_ROLE_DESCRIPTIONS

    if not args.apply and not args.revert:
        print("SOLO PLAN: se alinearían con el código las descripciones de:", ", ".join(sorted(SYSTEM_ROLE_DESCRIPTIONS)))
        print("Textos vigentes en el código:")
        for slug, value in sorted(SYSTEM_ROLE_DESCRIPTIONS.items()):
            print(f"  {slug}: {value}")
        print("No se abrió ninguna conexión.")
        return
    if args.apply and not args.rollback_file:
        parser.error("--apply requiere --rollback-file")
    target = _engine()
    try:
        with target.begin() as connection:
            if args.revert:
                changes = json.loads(Path(args.revert).read_text(encoding="utf-8"))["changes"]
                print(f"Descripciones restauradas: {revert_changes(connection, changes)} de {len(changes)}.")
                return
            changes = plan(connection, SYSTEM_ROLE_DESCRIPTIONS)
            rollback = Path(args.rollback_file)
            if rollback.exists():
                raise RuntimeError("El archivo de reversión ya existe; indique otro para no sobrescribirlo.")
            rollback.write_text(json.dumps({"migration": "role_descriptions_20261018", "changes": changes},
                                           ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Descripciones actualizadas: {apply_changes(connection, changes)}. Reversión: {rollback}")
    finally:
        target.dispose()


if __name__ == "__main__":
    main()
