"""Loopback-only jury demo in the guarded Phase E MariaDB fixture; no DEMO access."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch

from flask import jsonify, redirect, render_template_string, request
from flask_login import current_user
from phase_e_fixture import (build_app, db, DAY, CSV, auto, capture, move_after, publish,
                             reconcile, state, assert_integrity, save_evidence, User,
                             service, ControlledClock)
from app.models.reconciliation import ReconciliationCut
from app.services import session_service
from app.utils.auth_decorators import permission_required

MANIFEST = Path('/app/tmp/phase-e-ui-manifest.json')
PAGE = '''<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fase E · SIMULACIÓN AISLADA</title>
<style>body{font:16px system-ui;background:#f5f7fb;color:#17243c;margin:0}main{max-width:1100px;margin:auto;padding:28px}header,.card{background:white;border:1px solid #dce4ed;border-radius:14px;padding:24px;margin-bottom:18px}.banner{padding:15px;background:#fff2d5;color:#704000;font-weight:700}h1{font-size:28px}h2{font-size:20px}button,a.btn{display:inline-block;background:#2563eb;color:white;border:0;border-radius:8px;padding:12px 16px;font:inherit;text-decoration:none;cursor:pointer}button:focus-visible,a:focus-visible{outline:3px solid #f59e0b;outline-offset:3px}form{display:inline-block;margin:6px}table{border-collapse:collapse;width:100%}th,td{padding:10px;text-align:left;border-bottom:1px solid #e2e8f0}pre{overflow:auto;background:#eff5ff;padding:18px}.scroll{overflow:auto}small{color:#526079}</style>
<div class="banner">SIMULACIÓN AISLADA · Puerto 5066 · Datos sintéticos · Reloj controlado {{ day }} · Sin conexión a DEMO 5001</div>
<main><header><h1>Ferretería y Construcciones El Conejo C.A.</h1><p>Demostración de cortes automáticos y conciliación de una fuente externa.</p><p>La fuente sintética representa una exportación de registros. No representa un conteo físico.</p>
{% if current_user.is_authenticated %}<p>Usuario: {{ current_user.name }} · Rol: {{ current_user.role }}</p>{% else %}
<form method="post" action="/fase-e/entrar"><button>Entrar como administrador sintético</button></form>{% endif %}</header>
{% if data %}<section class="card" id="fase-e-schedule"><h2>1. Horarios de referencia</h2><p><strong>{{ data.schedules|join(' · ') }}</strong> · {{ data.timezone }}</p><p>Cuatro cortes nominales. Captura válida durante los primeros dos minutos; ingesta por tarea separada. Los botones llaman al servicio real con un reloj de prueba.</p><small>Base exclusivamente sintética: {{ name }}. Repetir un botón conserva el mismo corte y archivo.</small></section>
<section class="card"><h2>2. Secuencia reproducible</h2>
{% for action,label in [('capture','Ejecutar corte 08:00'),('move','Registrar entrada posterior +3'),('publish','Publicar CSV sintético'),('ingest','Ejecutar conciliación'),('verify','Verificar integridad')] %}
<form method="post" action="/fase-e/accion/{{ action }}"><button>{{ label }}</button></form>{% endfor %}
{% if cut %}<p>Corte #{{ cut.id }} · Estado: {{ cut.status }} · Fotografía: {{ cut.snapshot_status }} · Productos: {{ cut.stock|length }}</p><a class="btn" href="/reconciliation?cut={{ cut.id }}">Ver corte y fotografía</a>{% if cut.run_id %} <a class="btn" href="/reconciliation?detail={{ cut.run_id }}">Ver resultado y revisión</a>{% endif %}{% endif %}
{% if verified %}<p role="status"><strong>Integridad verificada:</strong> fotografía 12 / 20 / 8; stock posterior 15 / 20 / 8; cuatro movimientos legítimos; la conciliación no crea ajustes.</p>{% endif %}
</section><section class="card" id="fase-e-csv"><h2>3. Archivo externo SIMULADO</h2><p>inventario_{{ day }}_0800.csv · UTF-8 · delimitador «;» · publicación atómica</p><pre>{{ csv }}</pre><p>Diferencia = registro externo menos fotografía local. +3 es sobrante relativo entre registros; −5 es faltante relativo entre registros.</p></section>
<section class="card" id="fase-e-stock"><h2>4. Stock actual y movimientos reales de la simulación</h2><div class="scroll"><table><tr><th>SKU</th><th>Stock actual</th></tr>{% for code,qty in data.stock.items() %}<tr><td>{{ code }}</td><td>{{ qty }}</td></tr>{% endfor %}</table><table><tr><th>Movimiento</th><th>SKU</th><th>Antes</th><th>Después</th><th>Motivo</th></tr>{% for m in data.movements %}<tr><td>{{ m.id }}</td><td>{{ m.code }}</td><td>{{ m.previous }}</td><td>{{ m.new }}</td><td>{{ m.reason }}</td></tr>{% endfor %}</table></div></section>
<section class="card"><h2>5. Trazabilidad persistente</h2><p>Consultar fotografía, fecha efectiva, archivo, SHA-256, intentos y revisión en la pantalla real de conciliación.</p>{% if cut %}<p>Corte #{{ cut.id }} · Conciliación #{{ cut.run_id or 'pendiente' }} · Intentos {{ cut.attempt_count }}</p><p>Archivo detectado: {{ cut.source_received_at or 'pendiente' }} UTC · Archivado: {{ cut.archived_at or 'pendiente' }} UTC</p><p>SHA-256: <code>{{ cut.last_source_hash or 'sin archivo' }}</code></p>{% endif %}</section>{% endif %}</main></html>'''


def load_app(new=False):
    previous = json.loads(MANIFEST.read_text()) if MANIFEST.exists() and not new else {}
    app, name = build_app(previous.get('database'))
    with app.app_context():
        instants = [cut.last_attempt_at or cut.local_snapshot_at for cut in ReconciliationCut.query
                    if cut.last_attempt_at or cut.local_snapshot_at]
        if instants:
            ControlledClock.instant = max(instants)
    if not previous:
        MANIFEST.write_text(json.dumps(dict(database=name, simulation=True)), encoding='utf-8')
    return app, name


def attach_demo_routes(app, name):
    @app.get('/fase-e/identity')
    def identity():
        return jsonify(simulation=True, database=name, port=5066, timezone='America/Caracas')

    @app.post('/fase-e/entrar')
    def enter():
        session_service.open_session(User.query.filter_by(username='sim_admin').one())
        return redirect('/fase-e')

    @app.get('/fase-e')
    def console():
        allowed = current_user.is_authenticated and current_user.role == 'admin'
        data = state() if allowed else None
        cut = ReconciliationCut.query.filter_by(scheduled_for=auto.scheduled_at(DAY,'0800')).first() if allowed else None
        return render_template_string(PAGE, data=data, cut=cut, csv=CSV.decode('utf-8'),
                                      name=name, day=DAY, verified=request.args.get('verified') == '1')

    @app.get('/fase-e/state')
    @permission_required('reconciliation:read')
    def read_state():
        return jsonify(state())

    @app.post('/fase-e/accion/<action>')
    @permission_required('reconciliation:create')
    def perform(action):
        cut = ReconciliationCut.query.filter_by(scheduled_for=auto.scheduled_at(DAY,'0800')).first()
        if action == 'capture':
            capture()
        elif cut is None:
            return jsonify(error='Ejecute primero el corte sintético'), 409
        elif action == 'move':
            move_after()
        elif action == 'publish':
            publish(cut)
        elif action == 'ingest':
            reconcile(cut)
        elif action == 'verify':
            assert_integrity()
            save_evidence(name)
            return redirect('/fase-e?verified=1')
        else:
            return jsonify(error='Acción desconocida'), 404
        return redirect('/fase-e')

    @app.after_request
    def simulation_banner(response):
        if request.path.rstrip('/') in ('/reconciliation', '/settings') and response.mimetype == 'text/html':
            banner = '<div id="phase-e-banner" style="position:sticky;top:0;z-index:100;padding:12px;background:#fff2d5;color:#704000;font-weight:bold">SIMULACIÓN AISLADA · 5066 · Datos sintéticos · Reloj controlado 2031-01-15 · Sin conexión a DEMO 5001</div>'
            html = response.get_data(as_text=True)
            start = html.find('>', html.find('<body')) + 1
            response.set_data(html[:start] + banner + html[start:])
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-new', action='store_true')
    args = parser.parse_args()
    app, name = load_app(new=args.prepare_new)
    if args.prepare_new:
        print('Fresh synthetic schema prepared; previous scenario retained; no replay or deletion.')
        return
    attach_demo_routes(app, name)
    # Test-only review timestamps use the same explicit synthetic clock as the cut.
    with patch.object(service, 'datetime', ControlledClock):
        app.run(host='0.0.0.0', port=5066, debug=False, use_reloader=False, threaded=False)


if __name__ == '__main__':
    main()
