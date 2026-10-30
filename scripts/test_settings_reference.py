"""Disposable no-database harness for settings and integrated reference views.

Run from repository root. Blocks dotenv loading and never initializes SQLAlchemy,
OAuth, SMTP, uploads, or an application database. Synthetic login exists only here.
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask, request
from flask_login import UserMixin, current_user
from flask_wtf.csrf import generate_csrf
from app.extensions import login_manager
from app.routes.pages import pages_bp
from app.routes.purchases import purchases_bp
from app.routes.reconciliation import reconciliation_bp
from app.utils import navigation
from app.utils.permissions import role_has_permission

ROOT = Path(__file__).resolve().parents[1]
app = Flask(__name__, template_folder=str(ROOT / 'app/templates'), static_folder=str(ROOT / 'app/static'))
app.secret_key = 'disposable-reference-harness-only'
app.debug = True
login_manager.init_app(app)
app.register_blueprint(pages_bp)
app.register_blueprint(purchases_bp)
app.register_blueprint(reconciliation_bp)
app.add_template_global(generate_csrf, "csrf_token")
app.add_template_global(lambda: None, "get_company_logo_url")
app.add_template_global(lambda code: current_user.is_authenticated and role_has_permission(current_user.role, code), "has_permission")
app.add_template_global(lambda key, reference=False: navigation.nav_allowed(current_user.role, key, reference), "nav_allowed")
app.add_template_global(lambda links, reference=False: navigation.visible_links(current_user.role, links, reference), "visible_nav")
app.add_template_global(lambda key, reference=False: navigation.settings_tab_allowed(current_user.role, key, reference), "settings_tab_allowed")
app.add_template_global(lambda: navigation.home_path(current_user.role), "home_path")

class PreviewUser(UserMixin):
    id = 'reference-only'
    name = 'Administrador'
    email = 'admin@example.invalid'
    role = 'admin'
    profile_photo_url = None

@login_manager.request_loader
def preview_user(req):
    if req.headers.get('X-Preview-Role') == 'anonymous':
        return None
    user = PreviewUser()
    user.role = req.headers.get('X-Preview-Role', 'admin')
    return user

@login_manager.unauthorized_handler
def unauthorized():
    return 'Authentication required', 401

if __name__ == '__main__':
    client = app.test_client()
    for tab in ('general','predictive','notifications','users','security','integrations','backup'):
        assert client.get('/settings?tab=' + tab + '&ref=1').status_code == 200
    assert client.get('/settings?tab=unknown&ref=1').status_code == 404
    assert client.get('/settings?ref=1', headers={'X-Preview-Role':'anonymous'}).status_code == 401
    for role in ('inventario','vendedor'):
        assert client.get('/settings?ref=1',headers={'X-Preview-Role':role}).status_code == 302
    for method in ('post','put','patch','delete'):
        assert getattr(client,method)('/settings?ref=1').status_code == 405
    # Las pestañas en vivo (sin ?ref=1) leen datos reales y se cubren en scripts/test_settings_core.py (SQLite).
    assert b'notificaciones@ferreteriapro.com' in client.get('/settings?tab=notifications&ref=1').data
    fixture_markers = {'users': b'isabel.vivas@ferreteriapro.com', 'security': b'190.12.45.67', 'backup': b'/backups/ferreteria_pro'}
    for tab, marker in fixture_markers.items():
        reference = client.get('/settings?tab=' + tab + '&ref=1')
        assert marker in reference.data
        assert ('tab=' + tab + '&amp;ref=1" class="is-active"').encode() in reference.data
        for method in ('post', 'put', 'patch', 'delete'):
            assert getattr(client, method)('/settings?tab=' + tab + '&ref=1').status_code == 405
    backup = client.get('/settings?tab=backup&ref=1').get_data(as_text=True)
    for text in ('Resumen de respaldos', 'Crear respaldo ahora', 'Todo correcto', 'Restaurar respaldo seleccionado', 'Consejos y recomendaciones'):
        assert text in backup
    # Con debug=False, ?ref=1 se ignora y la página en vivo consulta la base de datos:
    # esa comprobación vive en scripts/test_settings_core.py ("live ?ref=1 sin debug no expone fixture").
    print('PASS: settings reference routes, active tabs, authorization and rejected mutation methods; no database initialized', flush=True)
    if '--serve' in sys.argv:
        app.run(host='127.0.0.1', port=int(os.environ.get('SETTINGS_PREVIEW_PORT', '5019')), debug=True, use_reloader=False, use_debugger=False)

