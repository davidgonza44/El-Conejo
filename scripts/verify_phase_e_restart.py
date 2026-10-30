"""Separate synthetic schema for a real server restart, invoked before/after it."""
import argparse
import json
from pathlib import Path
from phase_e_fixture import build_app, capture, move_after, state, publish, reconcile, assert_integrity
from app.models.reconciliation import ReconciliationCut

BEFORE = Path('/app/tmp/restart-before.json')
RESULT = Path('/app/tmp/restart-result.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'verify'))
    args = parser.parse_args()
    if args.mode == 'prepare':
        if BEFORE.exists():
            raise RuntimeError('Existing restart evidence; do not overwrite or duplicate it.')
        app, name = build_app()
        with app.app_context():
            cut = capture()
            move_after()
            assert cut.status == 'waiting_source' and cut.run_id is None
            BEFORE.write_text(json.dumps(dict(database=name, state=state()), indent=2), encoding='utf-8')
        print('Synthetic waiting_source with original snapshot saved; ready for actual server restart.')
        return
    before = json.loads(BEFORE.read_text(encoding='utf-8'))
    app, name = build_app(before['database'])
    with app.app_context():
        assert state() == before['state'], 'Database restart changed the saved snapshot/stock/state'
        cut = ReconciliationCut.query.one()
        publish(cut)
        cut = reconcile(cut)
        assert cut.status == 'completed' and cut.run.lines[0].system_quantity == 12
        result = assert_integrity()
        RESULT.write_text(json.dumps(dict(simulation=True, database=name,
            waiting_state_preserved=True, delayed_csv_used_original=True, after=result), indent=2), encoding='utf-8')
    print('PASS: actual MariaDB server restart retained waiting_source, frozen snapshot, stock and movements; delayed CSV used the original snapshot.')


if __name__ == '__main__':
    main()
