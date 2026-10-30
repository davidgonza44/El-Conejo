"""Validate source and assemble the local, synthetic-only evidence packet."""
import ast
import hashlib
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / '.tmp/phase-e-20261009'
CHANGED = (
    'app/services/reconciliation_automation_service.py', 'app/controllers/reconciliation_controller.py',
    'app/templates/reconciliation/page.html', 'app/static/js/ix_reconciliation.js',
    'docker_support/scheduler.py', 'scripts/test_reconciliation_intake.py',
    'scripts/phase_e_fixture.py', 'scripts/serve_phase_e_reconciliation.py',
    'scripts/test_phase_e_reconciliation.py', 'scripts/run_phase_e_demo.ps1',
    'scripts/package_phase_e_fixture.py', 'scripts/verify_phase_e_demo_readonly.py',
    'scripts/verify_phase_e_reconciliation.cjs', 'scripts/verify_phase_e_persistence.cjs',
    'scripts/verify_phase_e_restart.py', 'scripts/finish_phase_e_evidence.py',
)


def read(name):
    return json.loads((OUTPUT / name).read_text(encoding='utf-8-sig'))


def main():
    before, after = read('demo-before.json'), read('demo-after.json')
    assert all(after.get(key) == value for key, value in before.items()), 'Protected DEMO baseline differs; investigate before proceeding'
    containers_before = (OUTPUT/'demo-containers-before.txt').read_text(encoding='utf-8-sig')
    containers_after = (OUTPUT/'demo-containers-after.txt').read_text(encoding='utf-8-sig')
    assert containers_before == containers_after, 'DEMO container identity/start changed'
    git_before = set((OUTPUT/'git-before.txt').read_text(encoding='utf-8-sig').splitlines())
    git_after = set((OUTPUT/'git-after.txt').read_text(encoding='utf-8-sig').splitlines())
    assert git_before <= git_after, 'Pre-existing Git state changed or disappeared'
    for name in CHANGED:
        path = ROOT/name
        content = path.read_text(encoding='utf-8-sig')
        if path.suffix == '.py':
            ast.parse(content, filename=name)
        assert all(line == line.rstrip() for line in content.splitlines()), f'Trailing whitespace: {name}'
    result = read('browser-result.json')
    persistence = read('persistence-browser.json')
    assert result['identity'] == persistence['identity']
    assert result['page_errors'] == [] and result['replay_unchanged']
    assert persistence['persisted_unchanged']
    assert read('restart-result.json')['waiting_state_preserved']
    csv = OUTPUT/'inventario_2031-01-15_0800.csv'
    assert hashlib.sha256(csv.read_bytes()).hexdigest() == result['final']['cuts'][0]['last_source_hash']
    images = sorted(OUTPUT.glob('*.png'))
    labels = ''.join(f'<section><h2>{html.escape(path.stem)}</h2><p>SIMULACIÓN AISLADA · 5066 · Datos sintéticos · No procede de DEMO 5001.</p><a href="{html.escape(path.name)}"><img src="{html.escape(path.name)}" alt="{html.escape(path.stem)}"></a></section>' for path in images)
    (OUTPUT/'CAPTURAS.html').write_text('<!doctype html><html lang="es"><meta charset="utf-8"><title>Fase E · Evidencias reales de simulación aislada</title><style>body{font:16px system-ui;background:#f5f7fb;color:#17243c;margin:32px}section{background:white;border:1px solid #dce4ed;border-radius:12px;padding:24px;margin:24px auto;max-width:1200px}img{width:100%;height:auto}h1{color:#1d4ed8}</style><h1>Fase E · Capturas reales de la simulación aislada</h1><p>Reloj de prueba 2031-01-15. Las pantallas reflejan ejecuciones reales de Flask y MariaDB con datos sintéticos. No acreditan conexión con Navicat ni despliegue en DEMO.</p>'+labels+'</html>',encoding='utf-8')
    summary = dict(demo_protected_unchanged=True, protected_tables=len(before['tables']),
                   container_identities_unchanged=True, preexisting_git_status_preserved=True,
                   source_syntax_whitespace=True, simulation_database=result['identity']['database'],
                   captures=len(images), replay_unchanged=True, actual_restart_pass=True,
                   catalog_amount_fields=after['catalog_amount_fields'],
                   catalog_currency_counts=after['catalog_currency_counts'],
                   counts={key:value['count'] for key,value in after['tables'].items() if value})
    (OUTPUT/'VERIFICACION_FINAL.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__ == '__main__':
    main()
