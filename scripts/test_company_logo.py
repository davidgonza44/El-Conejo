"""Company logo HTTP tests and optional local server: disposable SQLite and temporary storage only."""
import io
import json
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from test_settings_core import setup_app, login, ADMIN_PASSWORD, db
from app.services import company_logo_service as logos
from app.services import settings_service
from app.models import AppSetting


def image_bytes(kind='PNG'):
    output = io.BytesIO()
    Image.new('RGBA' if kind != 'JPEG' else 'RGB', (180, 100), '#1268e8').save(output, kind)
    return output.getvalue()


def token(client):
    page = client.get('/settings?tab=general')
    assert page.status_code == 200, page.status_code
    return re.search(rb'data-csrf="([^"]+)"', page.data)[1].decode()


def run(app):
    client = app.test_client()
    login(client, 'core_admin', ADMIN_PASSWORD)
    headers = {'X-CSRFToken': token(client)}
    count = 0
    shells = (
        '/dashboard', '/products', '/sales', '/purchases', '/sales/customers',
        '/predictive/demand', '/reports/sales', '/settings', '/profile',
    )

    def check(label, valid):
        nonlocal count
        assert valid, label
        count += 1
        print('PASS:', label)

    def upload(data, filename='logo.png', mime='image/png', query='', values=None, extra_headers=None):
        return client.put('/api/settings' + query, data={
            'settings': json.dumps(values or {'company.name': 'Empresa de prueba'}),
            'logo': (io.BytesIO(data), filename, mime),
        }, headers=extra_headers or headers)

    def saved():
        with app.app_context():
            return settings_service.get(settings_service.COMPANY_LOGO)

    def files():
        with app.app_context():
            return set(p.name for p in logos.storage_root().glob('*'))

    empty = client.get('/dashboard').data
    check('no custom logo uses default icon', b'/api/settings/company-logo?version=' not in empty and b'ti-tool' in empty and b'data-company-logo' in empty)
    for kind, ext, mime in [('PNG', 'png', 'image/png'), ('JPEG', 'jpg', 'image/jpeg'), ('WEBP', 'webp', 'image/webp')]:
        old = saved()
        response = upload(image_bytes(kind), '../../original.' + ext, mime)
        check(kind + ' upload', response.status_code == 200)
        check('generated filename', bool(re.fullmatch(r'company-logo-[a-f0-9]{32}\.webp', saved())))
        check('replacement cleanup', files() == {saved()} and saved() != old)
        with app.app_context():
            with Image.open(logos.get_company_logo_path()) as im:
                check('normalized WEBP', im.format == 'WEBP' and im.size == (180, 100))
    before = saved()
    baseline = files()
    for label, raw, filename, mime in [
        ('2MB limit', b'x' * (logos.MAX_BYTES + 1), 'a.png', 'image/png'),
        ('request limit', b'x' * (logos.MAX_BYTES + 100000), 'a.png', 'image/png'),
        ('invalid extension', image_bytes(), 'a.exe', 'image/png'),
        ('invalid MIME', image_bytes(), 'a.png', 'text/html'),
        ('renamed executable', b'MZ' + b'0' * 100, 'a.png', 'image/png'),
        ('SVG', b'<svg></svg>', 'a.svg', 'image/svg+xml'),
        ('format mismatch', image_bytes('JPEG'), 'a.png', 'image/png'),
        ('truncated image', image_bytes()[:50], 'a.png', 'image/png'),
    ]:
        response = upload(raw, filename, mime)
        check(label, response.status_code in (400, 413))
        check(label + ' preserves old logo/settings/files', saved() == before and files() == baseline)
    with patch.object(logos, 'store', side_effect=OSError('synthetic failure')):
        check('storage failure', upload(image_bytes()).status_code == 500)
    check('failed replacement preserves logo', saved() == before and files() == baseline)
    with patch.object(db.session, 'commit', side_effect=__import__('sqlalchemy').exc.SQLAlchemyError('synthetic failure')):
        check('DB failure', upload(image_bytes()).status_code == 500)
    check('DB failure removes new file', saved() == before and files() == baseline)
    check('invalid General values reject complete transaction', upload(image_bytes(), values={'company.name': ' '}).status_code == 400 and files() == baseline)
    check('CSRF required', upload(image_bytes(), extra_headers={'X-CSRFToken': 'invalid'}).status_code == 400)
    check('metadata injection rejected', client.put('/api/settings', json={'company.logo': '../evil.webp'}, headers=headers).status_code == 400)
    check('General save', client.put('/api/settings', json={'company.name': 'Empresa guardada', 'alerts.low_stock_enabled': False}, headers=headers).status_code == 200)
    page = client.get('/settings').data
    logo_url = f'/api/settings/company-logo?version={saved()}'.encode()
    check('shell and preview use canonical logo', page.count(logo_url) >= 2)
    check('single brand logo box', page.count(b'data-company-logo') == 1)
    for path in shells:
        response = client.get(path)
        body = response.data
        check(f'shell {path} renders', response.status_code == 200)
        check(f'shell {path} custom logo', logo_url in body and b'ix-brand-logo is-image' in body)
        check(f'shell {path} brand text kept', 'Ferretería Pro'.encode() in body and 'Sistema de Gestión'.encode() in body)
    check('authenticated logo response', client.get('/api/settings/company-logo').status_code == 200)
    with app.app_context():
        stored = logos.get_company_logo_path()
        parked = stored.with_name('parked-' + stored.name)
        stored.rename(parked)
    missing = client.get('/dashboard').data
    check('missing file safe fallback', b'/api/settings/company-logo?version=' not in missing and b'ti-tool' in missing)
    with app.app_context():
        parked.rename(logos.owned_path(saved()))
    check('ref dashboard keeps default icon', b'/api/settings/company-logo?version=' not in client.get('/dashboard?ref=1').data
          and b'ti-tool' in client.get('/dashboard?ref=1').data)
    check('reset keeps logo', client.post('/api/settings/reset', json={'tab': 'general'}, headers=headers).status_code == 200 and saved() == before)
    for query, extra in [('?ref=1', headers), ('', {**headers, 'Referer': 'http://localhost/settings?ref=1'})]:
        check('ref upload rejected', upload(image_bytes(), query=query, extra_headers=extra).status_code == 403)
        check('ref delete rejected', client.put('/api/settings' + query, data={'settings': json.dumps({'company.name': 'Ref'}), 'delete_logo': '1'}, headers=extra).status_code == 403)
    check('ref DELETE endpoint rejected', client.delete('/api/settings/company-logo?ref=1', headers=headers).status_code == 403)
    check('delete CSRF rejected', client.delete('/api/settings/company-logo', headers={'X-CSRFToken': 'bad'}).status_code == 400)
    check('reference logo/file writes zero', saved() == before and files() == baseline)
    anon = app.test_client()
    check('anonymous rejected', anon.put('/api/settings', data={}).status_code == 401)
    login(anon, 'core_ven', 'ven123')
    check('unauthorized rejected', anon.put('/api/settings', data={}).status_code == 403)
    check('delete', client.delete('/api/settings/company-logo', headers=headers).status_code == 200)
    check('delete removes file and setting', saved() == '' and files() == set())
    check('default restored', b'/api/settings/company-logo?version=' not in client.get('/settings').data)
    for path in shells:
        body = client.get(path).data
        check(f'default restored {path}', b'/api/settings/company-logo?version=' not in body and b'ti-tool' in body)
    with app.app_context():
        check('no arbitrary deletion', all(logos.owned_path(p) is None for p in ('../test.webp', 'company-logo-not-owned.webp', 'C:/test.webp')))
    print(f'{count}/{count} PASS; valuable DB: NO; migrations: NONE')


def main():
    with tempfile.TemporaryDirectory(prefix='company_logo_e2e_') as directory:
        root = Path(directory)
        app, _ = setup_app(root)
        app.config.update(COMPANY_LOGO_DIR=str(root / 'company'), DEBUG=True, TEMPLATES_AUTO_RELOAD=True)
        try:
            if '--serve' in sys.argv:
                app.run(host='127.0.0.1', port=5037, use_reloader=False, use_debugger=False)
            else:
                run(app)
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()


if __name__ == '__main__':
    main()
