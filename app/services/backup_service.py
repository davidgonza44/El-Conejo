"""Respaldos (base de datos, archivos o completos), historial, retención y restauración.

- BD: mysqldump (MYSQLDUMP_BIN); la contraseña viaja por MYSQL_PWD del subproceso, nunca en argumentos ni logs.
- Archivos: ZIP de las carpetas persistentes de la aplicación (uploads/products, uploads/users,
  instance/historical_imports y el logo de la empresa).
- Completo: un ZIP con database/<bd>.sql + files/... + manifest.json (sin secretos). No es una imagen del servidor:
  no incluye variables de entorno, claves de cifrado, configuración del contenedor ni otros volúmenes.
- Restauración: exige confirmación tipada, verifica SHA-256, valida y prepara las carpetas destino y extrae los
  archivos antes de tocar la base, crea un respaldo de seguridad previo, valida rutas del ZIP (zip-slip) y
  ejecuta con el cliente configurado en MYSQL_BIN. Todo dentro de BACKUP_DIR.
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models import Backup, RestoreRun
from app.models.backup import (BACKUP_KIND_DATABASE, BACKUP_KIND_FILES, BACKUP_KIND_FULL, BACKUP_KINDS,
                               BACKUP_STATUS_COMPLETED, BACKUP_STATUS_CORRUPT, BACKUP_STATUS_DELETED, BACKUP_STATUS_MISSING)
from app.services import settings_service
from app.services.exceptions import ApiError, ConflictError, NotFoundError, ValidationError
from app.utils.schema import table_ready

_TIMEOUT_SECONDS = 600
_HISTORY_LIMIT = 50
_MANIFEST_VERSION = 1
RESTORE_CONFIRMATION = "RESTAURAR"
# Carpetas persistentes reales que forman el respaldo "solo archivos" (etiqueta -> ruta).
_FILE_ROOTS = (("uploads_products", ("uploads", "products")), ("uploads_users", ("uploads", "users")),
               ("historical_imports", ("instance", "historical_imports")))
_LOGO_ROOT = "company_logo"  # carpeta propia del logo (company_logo_service.storage_root)
_EXCLUDED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".tmp", "backups"}
_EXCLUDED_NAMES = {".env"}


def is_available() -> bool:
    return table_ready(Backup.__tablename__)


def restore_available() -> bool:
    return table_ready(RestoreRun.__tablename__)


def project_root() -> Path:
    return Path(current_app.root_path).parent


def default_dir() -> Path:
    configured = Path(current_app.config.get("BACKUP_DIR") or "backups")
    return configured if configured.is_absolute() else Path(current_app.instance_path) / configured


def backup_dir() -> Path:
    custom = settings_service.get(settings_service.BACKUP_DIRECTORY)
    return Path(custom) if custom else default_dir()


def validate_directory(value) -> str:
    """Ruta absoluta, fuera del código fuente y de carpetas sensibles, y escribible (se crea si no existe)."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Indique la carpeta de respaldos.")
    raw = value.strip()
    if any(token in raw for token in ("..", "\x00")):
        raise ValidationError("La ruta de respaldos no puede contener '..'.")
    path = Path(raw)
    if not path.is_absolute():
        raise ValidationError("La carpeta de respaldos debe ser una ruta absoluta.")
    resolved = path.resolve()
    root = project_root().resolve()
    allowed_inside = (Path(current_app.instance_path).resolve(),)
    if resolved == root or (root in resolved.parents and not any(a == resolved or a in resolved.parents for a in allowed_inside)):
        raise ValidationError("La carpeta de respaldos no puede estar dentro del código fuente (salvo instance/).")
    if any(part.lower() in ("windows", "system32", "program files") for part in resolved.parts):
        raise ValidationError("La carpeta de respaldos no puede ser una carpeta del sistema.")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        probe = resolved / f".write_test_{secrets.token_hex(3)}"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError:
        raise ValidationError("La carpeta de respaldos no existe o no tiene permisos de escritura.")
    return str(resolved)


def mysqldump_bin() -> str | None:
    """Ruta configurada en MYSQLDUMP_BIN si apunta a un archivo existente; nunca se adivina."""
    configured = (current_app.config.get("MYSQLDUMP_BIN") or "").strip()
    return configured if configured and Path(configured).is_file() else None


def mysql_bin() -> str | None:
    configured = (current_app.config.get("MYSQL_BIN") or "").strip()
    return configured if configured and Path(configured).is_file() else None


def known_roots() -> list[tuple[str, Path]]:
    """Carpetas persistentes que cubre el respaldo de archivos, existan o no."""
    from app.services import company_logo_service

    root = project_root()
    return [(label, root.joinpath(*parts)) for label, parts in _FILE_ROOTS] + [(_LOGO_ROOT, company_logo_service.storage_root())]


def file_roots() -> list[tuple[str, Path]]:
    """Solo carpetas que existen realmente (estado honesto)."""
    return [(label, path) for label, path in known_roots() if path.is_dir()]


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(project_root()))
    except ValueError:
        return str(path)


def guard_directory_change(value: str) -> None:
    """Impide que cambiar la carpeta global deje inaccesibles respaldos registrados: cada fila guarda solo el
    nombre del archivo y se resuelve contra la carpeta vigente. No mueve ni borra archivos."""
    if not is_available():
        return
    current, target = backup_dir(), (Path(value) if value else default_dir())
    if target.resolve() == current.resolve():
        return
    stranded = [b for b in Backup.query.filter(Backup.status != BACKUP_STATUS_DELETED).all()
                if (current / b.filename).is_file() and not (target / b.filename).is_file()]
    if stranded:
        raise ValidationError(f"No se cambió la carpeta: {len(stranded)} respaldo(s) registrado(s) están en {current} y no "
                              "existen en la nueva ubicación. Cópielos primero a la nueva carpeta con el mismo nombre "
                              "o conserve la carpeta actual.")


def list_backups() -> list[Backup]:
    if not is_available():
        return []
    return Backup.query.order_by(Backup.created_at.desc(), Backup.id.desc()).limit(_HISTORY_LIMIT).all()


def _is_mysql() -> bool:
    return db.engine.url.drivername.startswith("mysql")


def status() -> dict:
    """Estado honesto para la pantalla: disponibilidad real, carpeta, espacio libre e historial."""
    from app.services import google_drive_service, scheduler_service

    history = list_backups()
    target = backup_dir()
    usage = shutil.disk_usage(target if target.exists() else current_app.root_path)
    live = [b for b in history if b.status == BACKUP_STATUS_COMPLETED]
    return {
        "available": is_available() and mysqldump_bin() is not None and _is_mysql(),
        "table_ready": is_available(),
        "mysqldump_configured": mysqldump_bin() is not None,
        "mysql_configured": mysql_bin() is not None,
        "restore_available": restore_available() and mysql_bin() is not None and _is_mysql(),
        "files_available": bool(file_roots()),
        "file_roots": [display_path(path) for _, path in file_roots()],
        "directory": str(target),
        "default_directory": str(default_dir()),
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "count": len(live),
        "stored_bytes": sum(b.byte_size for b in live),
        "last": live[0].to_dict() if live else None,
        "history": [b.to_dict() for b in history],
        "schedule": scheduler_service.schedule_status(),
        "drive": google_drive_service.status(),
        "restores": [r.to_dict() for r in list_restores()],
    }


# ---- creación ----
def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_name(database: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", database)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _target(filename: str) -> Path:
    target_dir = backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    if path.resolve().parent != target_dir.resolve():
        raise ApiError("Ruta de respaldo inválida.", status_code=500)
    return path


def _run_dump(binary: str, url, path: Path) -> None:
    args = [binary, f"--host={url.host or '127.0.0.1'}", f"--port={url.port or 3306}", f"--user={url.username or ''}",
            "--single-transaction", "--routines", "--triggers", "--default-character-set=utf8mb4", url.database]
    env = {**os.environ, "MYSQL_PWD": url.password or ""}
    with path.open("wb") as out:
        try:
            completed = subprocess.run(args, stdout=out, stderr=subprocess.DEVNULL, env=env,
                                       timeout=_TIMEOUT_SECONDS, shell=False, check=False)
        except subprocess.TimeoutExpired:
            completed = None
    if completed is None or completed.returncode != 0:
        path.unlink(missing_ok=True)
        code = "tiempo agotado" if completed is None else completed.returncode
        current_app.logger.error("mysqldump falló (%s) al respaldar la base de datos.", code)
        raise ApiError("El respaldo falló: mysqldump devolvió un error. Revise MYSQLDUMP_BIN y el acceso a la base.",
                       status_code=500)


def _require_dump():
    if not is_available():
        raise ApiError("La tabla backups no existe. Ejecute scripts/migrate_backups.py.", status_code=503)
    binary = mysqldump_bin()
    if binary is None or not _is_mysql():
        raise ApiError("El respaldo no está disponible: configure MYSQLDUMP_BIN para una base MySQL/MariaDB.", status_code=503)
    return binary


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in _EXCLUDED_NAMES:
            continue
        if any(part in _EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def _add_files(archive: zipfile.ZipFile) -> tuple[int, dict]:
    count, checksums = 0, {}
    for label, root in file_roots():
        for path in _iter_files(root):
            arcname = f"files/{label}/{path.relative_to(root).as_posix()}"
            archive.write(path, arcname)
            checksums[arcname] = _sha256(path)
            count += 1
    return count, checksums


def _manifest(kind: str, components: list[str], file_count: int, checksums: dict) -> dict:
    dialect = db.engine.dialect
    return {
        "format_version": _MANIFEST_VERSION, "created_at": datetime.now(timezone.utc).isoformat(), "kind": kind,
        "app_version": current_app.config.get("APP_VERSION"), "database_engine": dialect.name,
        "database_version": ".".join(str(p) for p in (dialect.server_version_info or ())),
        "database_name": db.engine.url.database, "components": components, "file_count": file_count,
        "file_roots": [label for label, _ in file_roots()], "checksums": checksums,
    }


def create_backup(user, kind: str = BACKUP_KIND_DATABASE, source: str = "manual") -> Backup:
    """Crea el respaldo del tipo indicado y registra metadatos solo si tuvo éxito."""
    if kind not in BACKUP_KINDS:
        raise ValidationError("Tipo de respaldo inválido: use full, database o files.")
    if not is_available():
        raise ApiError("La tabla backups no existe. Ejecute scripts/migrate_backups.py.", status_code=503)
    url = db.engine.url
    base = f"{_safe_name(url.database)}_{kind}_{_stamp()}_{secrets.token_hex(3)}"
    file_count = None
    if kind == BACKUP_KIND_DATABASE:
        path = _target(f"{base}.sql")
        _run_dump(_require_dump(), url, path)
    else:
        if kind == BACKUP_KIND_FILES and not file_roots():
            raise ApiError("No existe ninguna carpeta de archivos persistentes para respaldar.", status_code=503)
        binary = _require_dump() if kind == BACKUP_KIND_FULL else None
        path = _target(f"{base}.zip")
        try:
            file_count = _write_zip(path, kind, binary, url)
        except Exception:
            path.unlink(missing_ok=True)
            raise
    record = Backup(filename=path.name, sha256=_sha256(path), byte_size=path.stat().st_size, database_name=url.database,
                    kind=kind, status=BACKUP_STATUS_COMPLETED, created_by_user_id=user.id, file_count=file_count, source=source)
    db.session.add(record)
    db.session.commit()
    return record


def _write_zip(path: Path, kind: str, binary: str | None, url) -> int:
    components = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        checksums = {}
        if kind == BACKUP_KIND_FULL:
            dump_path = path.with_suffix(".sql.tmp")
            try:
                _run_dump(binary, url, dump_path)
                arcname = f"database/{_safe_name(url.database)}.sql"
                archive.write(dump_path, arcname)
                checksums[arcname] = _sha256(dump_path)
            finally:
                dump_path.unlink(missing_ok=True)
            components.append("database")
        file_count, file_checksums = _add_files(archive)
        checksums.update(file_checksums)
        components.append("files")
        archive.writestr("manifest.json", json.dumps(_manifest(kind, components, file_count, checksums), indent=2))
    return file_count


# ---- historial ----
def get_backup_or_404(backup_id: int) -> Backup:
    record = db.session.get(Backup, backup_id) if is_available() else None
    if record is None:
        raise NotFoundError("El respaldo no existe.")
    return record


def backup_path(record: Backup) -> Path:
    """Ruta del archivo garantizada dentro de la carpeta de respaldos (sin traversal)."""
    target_dir = backup_dir().resolve()
    path = (target_dir / record.filename).resolve()
    if path.parent != target_dir:
        raise ApiError("Ruta de respaldo inválida.", status_code=500)
    return path


def verify(record: Backup) -> dict:
    """Recalcula el SHA-256 del archivo y actualiza el estado (completed | missing | corrupt)."""
    path = backup_path(record)
    if record.status == BACKUP_STATUS_DELETED:
        return {"ok": False, "status": record.status, "detail": "El respaldo fue eliminado."}
    if not path.is_file():
        record.status = BACKUP_STATUS_MISSING
        db.session.commit()
        return {"ok": False, "status": record.status, "detail": "El archivo no existe en la carpeta de respaldos."}
    actual = _sha256(path)
    ok = actual == record.sha256 and path.stat().st_size == record.byte_size
    record.status = BACKUP_STATUS_COMPLETED if ok else BACKUP_STATUS_CORRUPT
    db.session.commit()
    return {"ok": ok, "status": record.status, "sha256": actual,
            "detail": "Integridad verificada: el SHA-256 coincide." if ok else "El archivo no coincide con el SHA-256 registrado."}


def delete_backup(record: Backup) -> None:
    if record.status == BACKUP_STATUS_DELETED:
        raise ConflictError("El respaldo ya fue eliminado.")
    backup_path(record).unlink(missing_ok=True)
    record.status = BACKUP_STATUS_DELETED
    db.session.commit()


def download_path(record: Backup) -> Path:
    path = backup_path(record)
    if record.status == BACKUP_STATUS_DELETED or not path.is_file():
        raise NotFoundError("El archivo del respaldo no está disponible.")
    return path


def inspect_backup(record: Backup) -> dict:
    """Metadatos + manifest (si es ZIP) sin extraer nada."""
    data = record.to_dict()
    data["exists"] = backup_path(record).is_file() if record.status != BACKUP_STATUS_DELETED else False
    data["manifest"] = None
    if data["exists"] and record.filename.endswith(".zip"):
        with zipfile.ZipFile(backup_path(record)) as archive:
            if "manifest.json" in archive.namelist():
                manifest = json.loads(archive.read("manifest.json"))
                manifest.pop("checksums", None)
                data["manifest"] = manifest
    return data


def apply_retention() -> int:
    """Elimina (archivo + estado) respaldos completados más antiguos que la retención; solo dentro de BACKUP_DIR."""
    days = settings_service.get(settings_service.BACKUP_RETENTION_DAYS)
    if not days or not is_available():
        return 0
    limit = datetime.now(timezone.utc) - timedelta(days=days)
    removed = 0
    for record in Backup.query.filter(Backup.status == BACKUP_STATUS_COMPLETED).all():
        created = record.created_at if record.created_at.tzinfo else record.created_at.replace(tzinfo=timezone.utc)
        if created < limit:
            backup_path(record).unlink(missing_ok=True)
            record.status = BACKUP_STATUS_DELETED
            removed += 1
    db.session.commit()
    return removed


# ---- restauración ----
def list_restores() -> list[RestoreRun]:
    if not restore_available():
        return []
    return RestoreRun.query.order_by(RestoreRun.created_at.desc(), RestoreRun.id.desc()).limit(20).all()


def restore_preview(record: Backup) -> dict:
    """Datos para el modal de confirmación: fecha, tipo, tamaño, base destino y verificación de integridad."""
    check = verify(record)
    return {"backup": record.to_dict(), "target_database": db.engine.url.database, "integrity": check,
            "file_roots": [display_path(p) for _, p in file_roots()],
            "requires": RESTORE_CONFIRMATION, "mysql_configured": mysql_bin() is not None}


def _sql_sanity(path: Path) -> None:
    head = path.read_bytes()[:65536].decode("utf-8", errors="ignore")
    if "DROP DATABASE" in head.upper() or "CREATE DATABASE" in head.upper():
        raise ValidationError("El volcado contiene sentencias de base de datos completas y no se restaurará.")
    if "CREATE TABLE" not in head.upper() and "INSERT INTO" not in head.upper():
        raise ValidationError("El archivo no parece un volcado SQL válido.")


def _run_mysql(binary: str, url, sql_path: Path) -> None:
    args = [binary, f"--host={url.host or '127.0.0.1'}", f"--port={url.port or 3306}", f"--user={url.username or ''}",
            "--default-character-set=utf8mb4", url.database]
    env = {**os.environ, "MYSQL_PWD": url.password or ""}
    db.session.remove()
    db.engine.dispose()
    with sql_path.open("rb") as source:
        try:
            completed = subprocess.run(args, stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env,
                                       timeout=_TIMEOUT_SECONDS, shell=False, check=False)
        except subprocess.TimeoutExpired:
            completed = None
    if completed is None or completed.returncode != 0:
        detail = "tiempo agotado" if completed is None else f"código {completed.returncode}"
        current_app.logger.error("Restauración de base de datos fallida (%s).", detail)
        raise ApiError(f"La restauración de la base de datos falló ({detail}).", status_code=500)


def _safe_members(archive: zipfile.ZipFile, labels: set[str]) -> list[zipfile.ZipInfo]:
    members = []
    for info in archive.infolist():
        name = info.filename
        if not name.startswith("files/") or info.is_dir():
            continue
        parts = Path(name).parts
        if len(parts) < 3 or parts[1] not in labels or ".." in parts or Path(name).is_absolute() or "\\" in name:
            raise ValidationError(f"El ZIP contiene una ruta no permitida: {name[:80]}")
        members.append(info)
    return members


def _read_manifest(archive: zipfile.ZipFile, record: Backup) -> dict:
    if "manifest.json" not in archive.namelist():
        raise ValidationError("El ZIP no contiene manifest.json.")
    manifest = json.loads(archive.read("manifest.json"))
    if manifest.get("kind") != record.kind or manifest.get("database_name") != record.database_name:
        raise ValidationError("El manifest del ZIP no coincide con el respaldo registrado.")
    return manifest


def _archive_labels(archive: zipfile.ZipFile, manifest: dict) -> set[str]:
    """Carpetas que contiene el respaldo: las declaradas en el manifiesto y las presentes bajo files/."""
    labels = set(manifest.get("file_roots") or [])
    for name in archive.namelist():
        parts = Path(name).parts
        if name.startswith("files/") and len(parts) >= 3:
            labels.add(parts[1])
    return labels


def _prepare_targets(zip_path: Path, record: Backup) -> dict[str, Path]:
    """Antes de modificar la base o los archivos: valida el ZIP y deja escribibles las carpetas destino.

    Solo se reemplazan las carpetas incluidas en el respaldo. Una carpeta conocida que ya no existe se recrea
    vacía para recibir su contenido; una carpeta desconocida o sin permisos bloquea la restauración.
    """
    with zipfile.ZipFile(zip_path) as archive:
        labels = _archive_labels(archive, _read_manifest(archive, record))
        known = dict(known_roots())
        unknown = sorted(labels - set(known))
        if unknown:
            raise ValidationError(f"El respaldo contiene carpetas que esta versión no reconoce: {', '.join(unknown)}.")
        _safe_members(archive, labels)
    targets = {label: known[label] for label in sorted(labels)}
    for target in targets.values():
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target.parent / f".restore_probe_{secrets.token_hex(3)}"
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError:
            raise ApiError(f"No se puede preparar la carpeta {display_path(target)}; no se modificó la base de datos "
                           "ni los archivos.", status_code=503)
    return targets


def _extract_files(archive: zipfile.ZipFile, staging: Path, targets: dict[str, Path]) -> int:
    """Extrae a una carpeta temporal validando cada ruta (zip-slip)."""
    staging.mkdir(parents=True)
    members = _safe_members(archive, set(targets))
    for info in members:
        target = (staging / info.filename).resolve()
        if staging.resolve() not in target.parents:
            raise ValidationError("El ZIP contiene una ruta fuera del destino.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    return len(members)


def _swap_roots(staging: Path, targets: dict[str, Path]) -> None:
    """Reemplaza cada carpeta por su versión extraída; ante un error revierte las ya reemplazadas."""
    swapped: list[tuple[Path, Path]] = []
    try:
        for label, root in targets.items():
            incoming = staging / "files" / label
            incoming.mkdir(parents=True, exist_ok=True)
            backup = root.with_name(f"{root.name}.restore_bak_{_stamp()}")
            root.rename(backup)
            swapped.append((root, backup))
            shutil.move(str(incoming), str(root))
    except Exception:
        for root, backup in swapped:
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
            backup.rename(root)
        raise
    for _, backup in swapped:
        shutil.rmtree(backup, ignore_errors=True)


def _record_run(record: Backup, safety: Backup | None, user, status_value: str, detail: str,
                snapshots: list[dict]) -> RestoreRun | None:
    """Registra la restauración; tras restaurar la BD re-registra los respaldos cuyos metadatos quedaron fuera."""
    db.session.remove()
    db.engine.dispose()
    from app.utils import schema

    schema.reset_cache()
    for snapshot in snapshots:
        if Backup.query.filter_by(filename=snapshot["filename"]).first() is None:
            db.session.add(Backup(**snapshot))
    db.session.commit()
    if not restore_available():
        current_app.logger.warning("restore_runs no existe tras la restauración; la corrida no quedó registrada.")
        return None
    run = RestoreRun(backup_id=Backup.query.filter_by(filename=record.filename).with_entities(Backup.id).scalar(),
                     backup_filename=record.filename, kind=record.kind, target_database=db.engine.url.database,
                     safety_backup_id=(Backup.query.filter_by(filename=safety.filename).with_entities(Backup.id).scalar()
                                       if safety else None),
                     status=status_value, detail=detail[:255], performed_by_user_id=user.id)
    db.session.add(run)
    db.session.commit()
    return run


def _snapshot(record: Backup) -> dict:
    return {"filename": record.filename, "sha256": record.sha256, "byte_size": record.byte_size,
            "database_name": record.database_name, "kind": record.kind, "status": record.status,
            "created_by_user_id": record.created_by_user_id, "created_at": record.created_at,
            "file_count": record.file_count, "source": record.source}


def restore(record: Backup, user, data: dict) -> RestoreRun:
    """Restaura BD, archivos o ambos. Valida destinos antes de cambiar nada y aborta si el respaldo previo falla."""
    if (data.get("confirmation") or "") != RESTORE_CONFIRMATION:
        raise ValidationError(f"Escriba {RESTORE_CONFIRMATION} para confirmar la restauración.")
    if not restore_available():
        raise ApiError("La tabla restore_runs no existe. Ejecute scripts/migrate_settings_full.py.", status_code=503)
    if record.status == BACKUP_STATUS_DELETED:
        raise ConflictError("El respaldo fue eliminado.")
    if record.database_name != db.engine.url.database:
        raise ValidationError("El respaldo pertenece a otra base de datos y no puede restaurarse aquí.")
    check = verify(record)
    if not check["ok"]:
        raise ConflictError(f"No se restaurará: {check['detail']}")
    needs_db = record.kind in (BACKUP_KIND_DATABASE, BACKUP_KIND_FULL)
    if needs_db and (mysql_bin() is None or not _is_mysql()):
        raise ApiError("La restauración de base de datos no está disponible: configure MYSQL_BIN.", status_code=503)
    path = backup_path(record)
    if record.kind == BACKUP_KIND_DATABASE:
        _sql_sanity(path)
        targets = {}
    else:
        targets = _prepare_targets(path, record)
    safety = create_backup(user, kind=record.kind, source="safety")  # aborta (lanza) si falla
    snapshots = [_snapshot(record), _snapshot(safety)]
    detail = f"Respaldo de seguridad previo: {safety.filename}"
    try:
        if record.kind == BACKUP_KIND_DATABASE:
            _run_mysql(mysql_bin(), db.engine.url, path)
        else:
            detail = _restore_zip(record, path, needs_db, targets, detail)
    except (ApiError, ValidationError) as exc:
        _record_run(record, safety, user, "failed", exc.message, snapshots)
        raise
    return _record_run(record, safety, user, "completed", detail, snapshots)


def _restore_zip(record: Backup, path: Path, needs_db: bool, targets: dict[str, Path], detail: str) -> str:
    """Extrae volcado y archivos antes de ejecutar el SQL: un ZIP dañado o sin espacio no deja la base a medias."""
    staging = backup_dir() / f".restore_{_stamp()}_{secrets.token_hex(3)}"
    temp_sql = None
    try:
        with zipfile.ZipFile(path) as archive:
            _read_manifest(archive, record)
            if needs_db:
                dumps = [n for n in archive.namelist() if n.startswith("database/") and n.endswith(".sql")]
                if len(dumps) != 1:
                    raise ValidationError("El respaldo completo debe contener exactamente un volcado SQL.")
                temp_sql = backup_dir() / f".restore_{secrets.token_hex(4)}.sql"
                with archive.open(dumps[0]) as src, temp_sql.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                _sql_sanity(temp_sql)
            restored = _extract_files(archive, staging, targets)
        if temp_sql is not None:
            _run_mysql(mysql_bin(), db.engine.url, temp_sql)
        _swap_roots(staging, targets)
    finally:
        if temp_sql is not None:
            temp_sql.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
    return f"{detail}; archivos restaurados: {restored}"
