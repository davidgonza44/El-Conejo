"""Backfill idempotente de Category.color por nombre (solo UPDATE).

Uso (sin detener la app es seguro; pide bloqueo de asesoramiento):
    python scripts/backfill_category_colors.py --database ferreteria_conejo --dry-run
    python scripts/backfill_category_colors.py --database ferreteria_conejo --apply

Garantías:
- Solo actualiza categories.color en filas existentes cuyo color difiera del objetivo.
- Nunca crea ni elimina categorías; no lee ni escribe productos, movimientos,
  ventas, compras, notas de entrega, usuarios ni ninguna otra tabla.
- Idempotente: re-ejecutar deja el mismo estado (segunda corrida: 0 cambios).
- El mapa nombre->tono se deriva de scripts/seed_tesis.py (FAMILIES + FAMILY_COLORS);
  cualquier deriva (longitud o tono fuera de CATEGORY_COLORS) aborta antes de escribir.
- Abortos seguros sin escrituras: columna color ausente, base conectada distinta de
  la solicitada, nombres duplicados en la tabla, bloqueo no obtenido o cualquier
  error (una sola transacción con rollback).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import pymysql  # noqa: E402

from app.config import Config  # noqa: E402
from app.models.category import CATEGORY_COLORS  # noqa: E402
from seed_tesis import FAMILY_COLORS  # noqa: E402
from seed_tesis_data import FAMILIES  # noqa: E402

LOCK_SUFFIX = "category_colors_backfill"


def target_mapping() -> dict:
    """Nombre exacto -> tono objetivo; aborta si el seed y el mapa derivan."""
    if len(FAMILIES) != len(FAMILY_COLORS):
        raise RuntimeError(
            f"Deriva seed/backfill: {len(FAMILIES)} familias vs {len(FAMILY_COLORS)} tonos."
        )
    mapping = {family[0]: tone for family, tone in zip(FAMILIES, FAMILY_COLORS)}
    unknown = sorted(set(mapping.values()) - set(CATEGORY_COLORS))
    if unknown:
        raise RuntimeError(f"Tonos fuera de CATEGORY_COLORS: {', '.join(unknown)}.")
    return mapping


def build_plan(current: dict, mapping: dict) -> dict:
    """Plan puro: update/ok/missing/extra. Re-ejecutar sobre el resultado da 0 updates."""
    update = [(n, current[n], mapping[n]) for n in mapping if n in current and current[n] != mapping[n]]
    return {
        "update": sorted(update),
        "ok": sorted(n for n in mapping if current.get(n) == mapping[n]),
        "missing": sorted(n for n in mapping if n not in current),
        "extra": sorted(n for n in current if n not in mapping),
    }


def load_current(cursor) -> dict:
    cursor.execute("SELECT name, color FROM categories")
    return {name: color for name, color in cursor.fetchall()}


def find_duplicates(cursor) -> list:
    cursor.execute("SELECT name FROM categories GROUP BY name HAVING COUNT(*) > 1")
    return sorted(row[0] for row in cursor.fetchall())


def column_exists(cursor, database) -> bool:
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='categories' AND COLUMN_NAME='color'",
        (database,),
    )
    return cursor.fetchone()[0] > 0


def run(connection, database: str, dry_run: bool) -> dict:
    mapping = target_mapping()
    lock = database + ":" + LOCK_SUFFIX
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        if cursor.fetchone()[0] != database:
            raise RuntimeError("La base conectada no coincide con la base solicitada.")
        if not column_exists(cursor, database):
            raise RuntimeError("La columna categories.color no existe; aborte.")
        cursor.execute("SELECT GET_LOCK(%s, 10)", (lock,))
        if cursor.fetchone()[0] != 1:
            raise RuntimeError("No se pudo obtener el bloqueo; otra corrida en curso.")
        try:
            duplicates = find_duplicates(cursor)
            if duplicates:
                raise RuntimeError(f"Nombres duplicados, corrija a mano: {', '.join(duplicates)}.")
            plan = build_plan(load_current(cursor), mapping)
            if not dry_run and plan["update"]:
                for name, _old, new in plan["update"]:
                    cursor.execute(
                        "UPDATE categories SET color=%s WHERE BINARY name=BINARY %s AND color!=%s",
                        (new, name, new),
                    )
            connection.commit()
            return plan
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (lock,))


def report(plan: dict, dry_run: bool) -> str:
    mode = "DRY-RUN (sin escrituras)" if dry_run else "APLICADO"
    lines = [f"{mode}: {len(plan['update'])} por actualizar, "
             f"{len(plan['ok'])} ya correctas."]
    lines += [f"  UPDATE {n}: {old} -> {new}" for n, old, new in plan["update"]]
    if plan["missing"]:
        lines.append("  No existen (no se crean): " + ", ".join(plan["missing"]))
    if plan["extra"]:
        lines.append("  Existentes no mapeadas (no se tocan): " + ", ".join(plan["extra"]))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, choices=("ferreteria_conejo", "ferreteria_conejo_tesis", "ferreteria_conejo_e2e"))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True, dest="dry_run")
    group.add_argument("--apply", action="store_false", dest="dry_run")
    args = parser.parse_args()
    connection = pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_USER,
                                 password=Config.DB_PASSWORD, database=args.database, charset="utf8mb4")
    try:
        print(report(run(connection, args.database, args.dry_run), args.dry_run))
        return 0
    except RuntimeError as error:
        print(f"ABORTADO sin escrituras: {error}")
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
