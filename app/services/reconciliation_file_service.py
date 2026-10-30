"""Shared file copy: read-only source, staging/receipts only in private inbox.

Atomic publication never overwrites an existing final file on Windows or Linux. Producers MUST
finish/close .tmp before publication; a stable but truncated CSV cannot be inferred.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from datetime import date

MAX_BYTES = 48 * 1024 * 1024
MAX_ROWS = 50000
NAME = re.compile(r'inventario_([0-9]{4}-[0-9]{2}-[0-9]{2})_(0800|1200|1600|1830)\.csv\Z')


def validate_name(name):
    match = NAME.fullmatch(name)
    if not match:
        raise ValueError('Nombre de corte inválido')
    date.fromisoformat(match[1])
    return name



def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Se requiere un archivo regular')
    before = path.stat()
    if not 0 < before.st_size <= MAX_BYTES:
        raise ValueError('Tamaño de archivo fuera del límite')
    total, hashed = 0, hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            total += len(block)
            if total > before.st_size:
                raise RuntimeError('El archivo creció durante la lectura')
            hashed.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or total != after.st_size:
        raise RuntimeError('El archivo cambió durante su lectura')
    return {'bytes': total, 'sha256': hashed.hexdigest()}



def rename_new(temporary, final):
    temporary, final = Path(temporary), Path(final)
    if temporary.parent.resolve() != final.parent.resolve() or temporary.is_symlink():
        raise ValueError('Publicación requiere un archivo temporal en la misma carpeta')
    if os.name == 'nt':
        os.rename(temporary, final)  # Windows refuses to replace an existing target.
    else:
        # POSIX rename replaces targets. link is atomic and fails with EEXIST;
        # readers only see a fully written inode. No check-then-rename race.
        os.link(temporary, final, follow_symlinks=False)
        temporary.unlink()



def _receipt(path, value):
    temporary = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        rename_new(temporary, path)
    except FileExistsError:
        if json.loads(path.read_text(encoding='utf-8')) != value:
            raise ValueError('Conflicto de recibo; requiere revisión')
    finally:
        temporary.unlink(missing_ok=True)



def transport(source, inbox, *, progress=None, restore_missing=False):
    """Copy a closed published CSV; hash-verify .tmp before atomically exposing it.

    Receipts retain delivery identity after the consumer archives/removes the CSV.
    progress is an optional diagnostic observer; it cannot publish the partial file.
    """
    source, inbox = Path(source), Path(inbox).resolve()
    origin = source.parent.resolve()
    if inbox == origin or inbox.is_relative_to(origin) or origin.is_relative_to(inbox):
        raise ValueError('La fuente y el inbox deben ser carpetas independientes')
    name = validate_name(source.name)
    expected = digest(source)
    inbox.mkdir(parents=True, exist_ok=True)
    receipts = inbox / '.transport_receipts'
    if receipts.resolve().parent != inbox:
        raise ValueError('Recibos fuera del inbox privado')
    receipts.mkdir(exist_ok=True)
    receipt = receipts / (name + '.json')
    if receipt.exists():
        if json.loads(receipt.read_text(encoding='utf-8')) != expected:
            raise ValueError('Mismo corte con contenido distinto; requiere revisión')
        if (inbox / name).exists() and digest(inbox / name) != expected:
            raise ValueError('El inbox no coincide con el recibo verificado')
        if (inbox / name).exists() or not restore_missing:
            return dict(status='duplicate', **expected)
    final = inbox / name
    temporary = inbox / (name + '.' + secrets.token_hex(6) + '.tmp')
    created = False
    try:
        with source.open('rb') as incoming, temporary.open('xb') as outgoing:
            created = True
            copied = 0
            for block in iter(lambda: incoming.read(64 * 1024), b''):
                copied += len(block)
                if copied > expected['bytes']:
                    raise RuntimeError('El archivo creció durante el transporte')
                outgoing.write(block)
                if progress:
                    outgoing.flush()
                    progress(temporary, copied)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if digest(temporary) != expected or digest(source) != expected:
            raise RuntimeError('Contenido incompleto o cambiante; no publicado')
        # Durable identity before publication; a crash here can safely resume the same hash.
        _receipt(receipt, expected)
        try:
            rename_new(temporary, final)
            status = 'published'
        except FileExistsError:
            if digest(final) != expected:
                raise ValueError('Destino existente diferente; no sobrescrito')
            status = 'duplicate'
        if digest(final) != expected:
            raise RuntimeError('El hash final del inbox no coincide con la fuente')
        return dict(status=status, **expected)
    finally:
        if created:
            temporary.unlink(missing_ok=True)

