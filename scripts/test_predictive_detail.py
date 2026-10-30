"""Directed drawer checks on disposable SQLite; no production database or HTTP writes."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_predictive_module import seed, setup_app, login, ADMIN_PASSWORD
from app.extensions import db
from app.models import Product
from app.services import predictive_product_detail_service as detail


def run():
    checks = 0
    def check(label, condition):
        nonlocal checks
        assert condition, label
        checks += 1
        print('PASS', label)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        app, users = setup_app(Path(root))
        ids = seed(app, users['admin'])
        client = app.test_client()
        login(client, 'core_admin', ADMIN_PASSWORD)
        with app.app_context():
            products = {p.code: p.id for p in Product.query.all()}
        def get(code, query=''):
            response = client.get(f'/api/predictions/products/{products[code]}/detail{query}')
            check(f'detail {code} HTTP 200', response.status_code == 200)
            if response.json.get('computation_status') == 'pending':
                from app.services.predictive_job_service import get_jobs
                check('operational detail forecast completes', get_jobs(app).wait(120))
                response = client.get(f'/api/predictions/products/{products[code]}/detail{query}')
                check('completed detail remains HTTP 200', response.status_code == 200)
            return response.json
        cement = get('CEM-050')
        check('selected product ID', cement['product']['id'] == products['CEM-050'])
        filtered = get('CEM-050','?category=999&status=out_of_stock&search=missing')
        check('detail independent of table filters', filtered['product']['id'] == cement['product']['id'])
        check('real demand excludes cancelled sales', cement['history']['total'] == 270)
        check('real forecast total', cement['periods'][1]['total'] == 90)
        check('real forecast path', cement['periods'][1]['path'] == [3.0]*30)
        check('no invented daily interval', 'lower' not in cement['periods'][1] and 'upper' not in cement['periods'][1])
        check('pending supply', cement['pending'] == 100)
        check('no purchase when covered', cement['recommendation']['suggested_quantity'] == 0 and not cement['can_order'])
        paint = get('PIN-18L')
        check('approved supply only remainder', paint['pending'] == 30 and paint['orders'][0]['pending'] == 30)
        var = get('VAR-12')
        check('cancelled excluded', var['pending'] == 0 and not var['orders'])
        check('descriptive interval absent', all(p['interval'] is None for p in var['periods']))
        check('no backtest WAPE ND', var['wape'] is None)
        r = var['recommendation']
        check('ROP authoritative', abs(r['reorder_point']-(r['lead_time_demand']+var['product']['minimum'])) < 1e-9)
        check('lead time assumed', r['lead_time_source'] == 'assumed' and r['lead_time_days'] == 7)
        check('suggested quantity backend', r['suggested_quantity'] > 0 and not var['can_order'])
        check('admin sin replenishment:create no solicita', not var['can_order'])
        check('real inventory movements', bool(cement['movements']) and cement['movements'][0]['document'] is None)
        check('admin no recibe el historial de ventas retirado', cement['sales'] == [] and cement['links']['sales'] is None)
        check('enlace de inventario sigue disponible', '/products?' in (cement['links']['inventory'] or ''))
        check('nonexistent returns 404', client.get('/api/predictions/products/999999/detail').status_code == 404)
        inactive = get('CIN-5M')
        check('inactive valid ID remains accessible', inactive['product']['id'] == products['CIN-5M'] and not inactive['can_order'])
        anonymous = app.test_client()
        check('anonymous denied', anonymous.get(f'/api/predictions/products/{products["CEM-050"]}/detail').status_code in (401,302))
        inv = app.test_client(); login(inv,'core_inv','inv123')
        seen = inv.get(f'/api/predictions/products/{products["VAR-12"]}/detail')
        check('inventario con predictions:read consulta el detalle', seen.status_code == 200)
        view = seen.json
        rec = view.get('recommendation') or {}
        pref = view.get('preferred_supplier') or {}
        check('inventario con replenishment:create puede solicitar', view['can_order'] and bool(view['suppliers']))
        check('detalle inventario sin unit_cost ni purchase_price',
              'unit_cost' not in rec and 'purchase_price' not in pref)
        ven = app.test_client(); login(ven,'core_ven','ven123')
        with app.app_context():
            from app.models import PurchaseOrder
            orders_before = PurchaseOrder.query.count()
        denied = ven.get(f'/api/predictions/products/{products["VAR-12"]}/detail')
        with app.app_context():
            orders_after = PurchaseOrder.query.count()
        check('vendedor sin predictions:read no consulta el detalle', denied.status_code == 403)
        check('GET denegado no crea orden de compra', orders_after == orders_before)
        check('debug off ref unavailable', client.get('/api/predictions/products/0/detail?ref=1&sku=CEM-050').status_code == 404)
        app.debug = True
        with patch.object(db.session, 'commit', side_effect=AssertionError('reference write')), patch.object(detail, 'build', side_effect=AssertionError('LIVE fallback')):
            first = client.get('/api/predictions/products/0/detail?ref=1&sku=CEM-050')
            second = client.get('/api/predictions/products/0/detail?ref=1&sku=CEM-050')
            check('reference deterministic zero commits and no LIVE fallback', first.status_code == 200 and first.json == second.json and first.json['reference'])
            check('reference cannot buy', not first.json['can_order'])
        with app.app_context():
            db.session.remove(); db.engine.dispose()
    print(f'{checks} PASS / 0 FAIL')

if __name__ == '__main__':
    run()
