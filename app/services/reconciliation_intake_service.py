"""Read-only landing -> private inbox. No CSV parsing or reconciliation engine."""
import json
import os
from pathlib import Path
import secrets
import time
from flask import current_app
from app.services import reconciliation_file_service as files


def paths():
    roots = {}
    for name in ('LANDING', 'INBOX', 'ARCHIVE'):
        value = current_app.config.get(f'RECONCILIATION_{name}_PATH', f'reconciliation_{name.lower()}')
        if not value:
            raise ValueError('Las tres carpetas de conciliación deben ser explícitas')
        path = Path(value)
        roots[name] = (path if path.is_absolute() else Path(current_app.instance_path) / path).resolve()
    values = list(roots.values())
    for index, first in enumerate(values):
        for second in values[index+1:]:
            if first == second or first.is_relative_to(second) or second.is_relative_to(first):
                raise ValueError('Landing, inbox y archive deben ser independientes, sin anidamiento')
    return roots


def _settled(source, inbox):
    """Two observations across invocations; state belongs to inbox, never landing."""
    delay = float(current_app.config.get('RECONCILIATION_SETTLE_SECONDS', 2))
    if delay < 0:
        raise ValueError('Tiempo de estabilidad inválido')
    info = source.stat()
    signature = [info.st_size, info.st_mtime_ns, info.st_ctime_ns]
    folder = inbox / '.intake_observations'
    if folder.resolve().parent != inbox:
        raise ValueError('Observaciones fuera del inbox privado')
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (source.name + '.json')
    previous = json.loads(target.read_text(encoding='utf-8')) if target.exists() else None
    now = time.time()
    if previous and previous['signature'] == signature:
        return now - previous['observed_at'] >= delay
    temporary = folder / (source.name + '.' + secrets.token_hex(6) + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(dict(signature=signature, observed_at=now), stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return delay == 0


def verify_private(name, content_sha256=None):
    """Recheck receipts before processing, including after a process restart.

    Existing private inbox uploads without an intake receipt retain their previous
    contract. Intake-created files always have a receipt before being handed off.
    """
    roots = paths()
    path = roots['INBOX'] / files.validate_name(name)
    if path.resolve().parent != roots['INBOX']:
        raise ValueError('Archivo fuera del inbox privado')
    receipt = roots['INBOX'] / '.transport_receipts' / (name + '.json')
    if not path.exists():
        return False
    if receipt.exists():
        expected = json.loads(receipt.read_text(encoding='utf-8'))
        if files.digest(path) != expected or (content_sha256 and content_sha256 != expected['sha256']):
            raise ValueError('El inbox no coincide con el SHA-256 de ingreso')
    return True


def prepare(name):
    """Only ready hands off to the existing engine; waiting retries on next run."""
    roots = paths()  # Fail closed on configuration errors, before touching any file.
    name = files.validate_name(name)
    source = roots['LANDING'] / name
    try:
        if source.is_symlink() or source.resolve().parent != roots['LANDING']:
            raise ValueError('Archivo fuera del landing')
        if source.exists():
            if not _settled(source, roots['INBOX']):
                return dict(status='waiting', reason='Archivo aún no estable')
            files.transport(source, roots['INBOX'], restore_missing=True)
        ready = verify_private(name)
        return dict(status='ready' if ready else 'waiting', reason=None)
    except (OSError, RuntimeError) as error:
        return dict(status='waiting', reason=str(error))
    except ValueError as error:
        return dict(status='conflict', reason=str(error))
