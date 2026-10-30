"""Explicit synthetic producer, publisher and shared read-only-source transport."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
from datetime import date

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.reconciliation_file_service import MAX_BYTES, MAX_ROWS, validate_name, digest, rename_new, transport


def produce(directory, day, slot, rows):
    """Generate only synthetic SKU/stock; never reads a source database."""
    if not 1 <= rows <= MAX_ROWS:
        raise ValueError('Filas fuera del límite')
    name = validate_name(f'inventario_{date.fromisoformat(day).isoformat()}_{slot}.csv')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / name
    temporary = directory / (name + '.' + secrets.token_hex(6) + '.tmp')
    with temporary.open('x', encoding='utf-8', newline='') as stream:
        stream.write('product_code;quantity\n')
        for i in range(1, rows + 1):
            stream.write(f'SYN-BENCH-{i:08d};{10 + i % 201}\n')
        stream.flush()
        os.fsync(stream.fileno())
    expected = digest(temporary)
    rename_new(temporary, final)
    return dict(path=str(final), **expected)



def publish_export(temporary, final_name):
    """Called only AFTER an exporter reports success and closes its temporary file."""
    temporary = Path(temporary)
    if temporary.suffix != '.tmp':
        raise ValueError('La exportación debe terminar en .tmp')
    final = temporary.parent / validate_name(final_name)
    expected = digest(temporary)
    rename_new(temporary, final)
    return dict(path=str(final), **expected)



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    generate = commands.add_parser('synthetic')
    generate.add_argument('--exports', required=True)
    generate.add_argument('--day', required=True)
    generate.add_argument('--slot', choices=('0800','1200','1600','1830'), required=True)
    generate.add_argument('--rows', type=int, required=True)
    publish = commands.add_parser('publish')
    publish.add_argument('--temporary', required=True)
    publish.add_argument('--name', required=True)
    publish.add_argument('--export-completed', action='store_true', required=True,
                         help='Sólo después de éxito y cierre del archivo por el exportador')
    copy = commands.add_parser('transport')
    copy.add_argument('--source', required=True)
    copy.add_argument('--inbox', required=True)
    args = parser.parse_args()
    if args.command == 'synthetic':
        result = produce(args.exports, args.day, args.slot, args.rows)
    elif args.command == 'publish':
        result = publish_export(args.temporary, args.name)
    else:
        result = transport(args.source, args.inbox)
    print(json.dumps(result, ensure_ascii=False))



if __name__ == '__main__':
    main()
