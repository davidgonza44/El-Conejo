"""Controlled Caracas evening boundaries; mutations only via guarded disposable MariaDB."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import json
import re

from sqlalchemy import text
from purchase_e2e_support import make_app, client_for, cleanup, DATABASE
from app.extensions import db
from app.models.purchase import PurchaseOrder
from app.services import purchase_service
from app.controllers import purchase_controller


def run():
    app, ids, _ = make_app()
    client = client_for(app, ids['admin_id'])
    approver = client_for(app, ids['approver_id'])
    checks = 0
    try:
        for hour, minute in ((20, 0), (21, 30), (23, 59)):
            local = datetime(2026, 5, 31, hour, minute, tzinfo=purchase_service.inventory_service.COMMERCIAL_ZONE)
            today = local.date()
            class FrozenDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return local.astimezone(tz) if tz else local.replace(tzinfo=None)
            response = client.post('/api/purchases', json={'supplier_id':ids['supplier_ids'][0],
                'items':[{'product_id':ids['product_ids'][0], 'quantity':3, 'unit_cost':'1'}]})
            assert response.status_code == 201
            order_id = response.json['id']
            assert approver.post(f'/api/purchases/{order_id}/approve', json={}).status_code == 200
            with app.app_context():
                assert db.session.execute(text('SELECT DATABASE()')).scalar() == DATABASE
                db.session.get(PurchaseOrder, order_id).ordered_at = local.astimezone(timezone.utc).replace(tzinfo=None)
                db.session.commit()
            with patch.object(purchase_service, 'datetime', FrozenDateTime), patch.object(purchase_controller, 'datetime', FrozenDateTime):
                detail = client.get(f'/api/purchases/{order_id}').json
                html = client.get('/purchases').get_data(as_text=True)
                context = json.loads(re.search(r'id="po-context">(.*?)</script>', html, re.S).group(1))
                assert detail['ordered_date'] == today.isoformat()
                assert detail['ordered_at'][:10] == (today + timedelta(days=1)).isoformat()
                assert context['today'] >= detail['ordered_date']
                for offset, status in ((-1, 400), (1, 400), (0, 200)):
                    receipt = client.post(f'/api/purchases/{order_id}/receptions', json={
                        'received_at':(today + timedelta(days=offset)).isoformat(),
                        'items':[{'line_id':detail['items'][0]['id'], 'quantity':1}]})
                    assert receipt.status_code == status, (hour, offset, receipt.json)
                    checks += 1
                updated = client.get(f'/api/purchases/{order_id}').json
                assert updated['received_units'] == 1 and len(updated['receptions']) == 1
                checks += 4
        print(f'PASS: {checks} Caracas boundary assertions at 20:00, 21:30, 23:59; same-day accepted, earlier/future rejected.')
    finally:
        cleanup(app, ids)


if __name__ == '__main__':
    run()
