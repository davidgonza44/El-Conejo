"""Opt-in integration acceptance on a separately restored MariaDB container only."""
import hashlib
import io
import json
import os
import re
import secrets
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flask import g, session
from flask_login import login_user
from sqlalchemy import event, text

from app import create_app
from app.extensions import db
from app.models import Product, StockMovement, User
from app.models.reconciliation import ReconciliationCut, ReconciliationCutStock, ReconciliationRun
from app.services import session_service, reconciliation_cut_web_service as web
from app.services import reconciliation_automation_service as auto
from docker_support.scheduler import reconciliation_tick


def guarded_app():
    if os.environ.get("CUT_WEB_DISPOSABLE") != "RESTORED-MARIADB-ONLY":
        raise RuntimeError("Explicit isolated restored MariaDB required")
    app = create_app()
    with app.app_context():
        with db.engine.connect() as c:
            hostname = c.execute(text("SELECT @@hostname")).scalar()
            assert hostname == os.environ["CUT_WEB_DB_HOSTNAME"], "Wrong MariaDB container"
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0007_catalog_currency"
    return app


APP = guarded_app()
KEYS = [secrets.token_hex(16) for _ in range(5)]


def actor_for(role):
    if role == "admin":
        actor_id = db.session.execute(text("SELECT admin_id FROM container_installation WHERE id=1")).scalar_one()
        return db.session.get(User, actor_id)
    name = "demo_soporte" if role == "soporte_ti" else "demo_" + role
    return User.query.filter_by(username=name, role=role).one()


def client(role="admin"):
    with APP.app_context(), APP.test_request_context():
        actor = actor_for(role)
        session_service.open_session(actor)
        cookies = dict(session)
    client = APP.test_client()
    with client.session_transaction() as target:
        target.update(cookies)
    return client


def token(actor):
    response = actor.get("/reconciliation")
    assert response.status_code == 200, response.status_code
    context = json.loads(re.search(r'<script type="application/json" id="rc-context">(.*?)</script>', response.text, re.S)[1])
    return {"X-CSRFToken": context["csrfToken"]}


def simulate(actor, headers, key, slot="0800"):
    response = actor.post("/api/reconciliation/cuts/simulate", json={"slot":slot,"request_key":key}, headers=headers)
    assert response.status_code == 201, response.json
    return response.json


def upload(actor, headers, cut, content, filename="DEMO.csv"):
    return actor.post(f'/api/reconciliation/cuts/{cut}/upload', data={"file":(io.BytesIO(content),filename)}, headers=headers)


class Acceptance(unittest.TestCase):
    def test_01_browser_contract_frozen_csv_idempotency_review(self):
        actor = client()
        headers = token(actor)
        with APP.app_context():
            stocks = {p.id:p.current_stock for p in Product.query}
            moves = StockMovement.query.count()
        before = datetime.utcnow()
        cut = simulate(actor, headers, KEYS[0])
        self.assertEqual(cut["execution_type"], "simulation")
        self.assertEqual(cut["reference_slot"], "08:00")
        self.assertGreaterEqual(datetime.fromisoformat(cut["local_snapshot_at"]), before.replace(microsecond=0))
        self.assertEqual(cut["scheduled_for"],cut["local_snapshot_at"])
        self.assertEqual(cut["status"],"waiting_source")
        self.assertEqual(simulate(actor,headers,KEYS[0])["id"],cut["id"])
        self.assertEqual(actor.post("/api/reconciliation/cuts/simulate",json={"slot":"1200","request_key":KEYS[0]},headers=headers).status_code,409)
        csv = actor.get(f'/api/reconciliation/cuts/{cut["id"]}/csv?variant=scenario')
        self.assertEqual(csv.status_code,200)
        with APP.app_context():
            frozen = {row.product_id:row.quantity for row in ReconciliationCutStock.query.filter_by(cut_id=cut["id"])}
            self.assertEqual(frozen,stocks)
            # This change is confined to the restored disposable DB, to prove comparison provenance.
            Product.query.first().current_stock += 7
            db.session.commit()
        result = upload(actor,headers,cut["id"],csv.data)
        self.assertEqual(result.status_code,201,result.json)
        run = result.json
        self.assertEqual((run["match_count"],run["discrepancy_count"],run["unmatched_count"]),(1,2,1))
        self.assertEqual(sorted(item["difference"] for item in run["items"] if item["difference"] is not None),[-1,0,2])
        self.assertEqual(run["status"],"preview")
        self.assertEqual(upload(actor,headers,cut["id"],csv.data).json["id"],run["id"])
        self.assertEqual(upload(actor,headers,cut["id"],b'product_code;quantity\nUNKNOWN;2\n').status_code,409)
        result = actor.post(f'/api/reconciliation/{run["id"]}/confirm',json={},headers=headers)
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(result.json["items"],run["items"])
        self.assertEqual(actor.post(f'/api/reconciliation/{run["id"]}/confirm',json={},headers=headers).status_code,200)
        self.assertEqual(actor.post(f'/api/reconciliation/{run["id"]}/discard',json={},headers=headers).status_code,409)
        line = next(item for item in run["items"] if item["result"]=="discrepancy")
        endpoint=f'/api/reconciliation/{run["id"]}/lines/{line["id"]}'
        self.assertEqual(actor.post(endpoint+'/resolve',json={"action":"adjustment","new_stock":99,"note":"DEMO prohibited"}).status_code,403)
        reviewed=actor.post(endpoint+'/review',json={"note":"DEMO revisión sintética; inventario intacto"})
        self.assertEqual(reviewed.status_code,200,reviewed.json)
        self.assertEqual(sum(item["review_status"]=="reviewed" for item in reviewed.json["items"]),1)
        self.assertEqual(sum(item["result"]=="discrepancy" and item["review_status"]=="pending" for item in reviewed.json["items"]),1)
        self.assertEqual(actor.post(endpoint+'/resolve',json={"action":"acknowledge","note":"DEMO sin ajuste"}).status_code,200)
        self.assertEqual(actor.get(f'/api/reconciliation/cuts/{cut["id"]}/csv?variant=uploaded').data,csv.data)
        with APP.app_context():
            self.assertEqual(StockMovement.query.count(),moves)
            for product in Product.query:
                expected=stocks[product.id]+(7 if product.id==min(stocks) else 0)
                self.assertEqual(product.current_stock,expected)
            Product.query.first().current_stock -= 7
            db.session.commit()
            self.assertEqual({p.id:p.current_stock for p in Product.query},stocks)
            self.assertEqual(auto.process_cut(cut["id"]).run_id,run["id"])
        history=actor.get('/api/reconciliation/cuts?type=simulation').json
        self.assertIn(cut["id"],[item["id"] for item in history["items"]])
        self.assertEqual(actor.get(f'/api/reconciliation/cuts/{cut["id"]}?snapshot_page=0').status_code,400)

    def test_02_csv_validation_permissions_csrf_and_demo_only(self):
        actor=client(); headers=token(actor)
        self.assertEqual(actor.post('/api/reconciliation/cuts/simulate',json={"slot":"0800","request_key":KEYS[1]}).status_code,400)
        self.assertEqual(actor.post('/api/reconciliation/cuts/simulate',json={"slot":"0900","request_key":KEYS[1]},headers=headers).status_code,400)
        self.assertEqual(actor.post('/api/reconciliation/cuts/simulate',json={"slot":"0800","request_key":KEYS[1],"current_stock":2},headers=headers).status_code,400)
        self.assertEqual(actor.post('/api/reconciliation/cuts/simulate',json={"slot":"0800","request_key":KEYS[1]},headers={**headers,"Origin":"https://foreign.invalid"}).status_code,403)
        cut=simulate(actor,headers,KEYS[1], "1200")
        for content in (b'bad,columns\nA,2\n', b'product_code;quantity\nA;-1\n', b'product_code;quantity\nA;2\na;2\n', b'product_code;quantity\n', b'\xff\xfe'):
            response=upload(actor,headers,cut["id"],content)
            self.assertEqual(response.status_code,400,response.json)
            self.assertTrue(response.json["error"])
        self.assertEqual(actor.get(f'/api/reconciliation/cuts/{cut["id"]}').json["run_id"],None)
        for role in ("inventario","asistente","vendedor","soporte_ti"):
            denied=client(role)
            self.assertEqual(denied.get('/reconciliation').status_code,403)
            self.assertEqual(denied.post('/api/reconciliation/cuts/simulate',json={"slot":"0800","request_key":KEYS[2]},headers=headers).status_code,403)
            self.assertEqual(denied.get(f'/api/reconciliation/cuts/{cut["id"]}').status_code,403)
            self.assertEqual(upload(denied,headers,cut["id"],b'product_code;quantity\nA;1\n').status_code,403)
        with patch.dict(APP.config,APP_ENV="production"):
            self.assertEqual(actor.post('/api/reconciliation/cuts/simulate',json={"slot":"0800","request_key":KEYS[2]},headers=headers).status_code,403)
            self.assertNotIn('id="rc-simulate"',actor.get('/reconciliation').text)
        with patch.dict(APP.config,RECONCILIATION_ACTIVATED_ON=""), patch.object(auto,'capture_cut',side_effect=AssertionError("Worker activated")), patch.object(auto,'ingest_due',side_effect=AssertionError("Worker activated")):
            with APP.app_context():
                reconciliation_tick()
        self.assertEqual(actor.get('/api/reconciliation/cuts?type=bad').status_code,400)

    def test_03_concurrent_execution_and_recovery_after_restart(self):
        def capture(_):
            with APP.app_context(), APP.test_request_context():
                login_user(actor_for('admin'))
                return web.simulate('1600',KEYS[3]).id
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(capture,range(2)))
        self.assertEqual(ids[0],ids[1])
        with APP.app_context():
            row=db.session.get(ReconciliationCut,ids[0])
            before=[(s.product_id,s.quantity) for s in row.stock]
            source=row.source
            db.session.remove(); db.engine.dispose()
        self.assertEqual(capture(0),ids[0])
        with APP.app_context():
            self.assertEqual([(s.product_id,s.quantity) for s in db.session.get(ReconciliationCut,ids[0]).stock],before)
            self.assertEqual(ReconciliationCut.query.filter_by(source=source).count(),1)
        actor=client(); headers=token(actor)
        content=actor.get(f'/api/reconciliation/cuts/{ids[0]}/csv?variant=scenario').data
        run=upload(actor,headers,ids[0],content).json
        with APP.app_context():
            db.session.remove(); db.engine.dispose()
        result=actor.post(f'/api/reconciliation/{run["id"]}/confirm',json={},headers=headers)
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(upload(actor,headers,ids[0],content).json["id"],run["id"])

    def test_04_capture_rollback_never_leaves_partial_snapshot(self):
        with APP.app_context(), APP.test_request_context():
            login_user(actor_for('admin'))
            count=ReconciliationCut.query.count()
            def fail(_conn,_cursor,statement,_params,_context,_many):
                if statement.upper().startswith('INSERT INTO RECONCILIATION_CUT_STOCK'):
                    raise RuntimeError('Synthetic rollback')
            event.listen(db.engine,'after_cursor_execute',fail)
            try:
                with self.assertRaisesRegex(RuntimeError,'Synthetic'):
                    web.simulate('1830',KEYS[4])
            finally:
                event.remove(db.engine,'after_cursor_execute',fail)
            self.assertEqual(ReconciliationCut.query.count(),count)
            cut=web.simulate('1830',KEYS[4])
            self.assertEqual(len(cut.stock),Product.query.count())

    def test_05_operational_cut_uses_same_frozen_browser_flow(self):
        actor=client(); headers=token(actor)
        with APP.app_context():
            now=datetime.utcnow()
            scheduled=auto.scheduled_at(now.date(), "1600")
            while ReconciliationCut.query.filter_by(source=auto.SOURCE,scheduled_for=scheduled).first():
                scheduled += auto.timedelta(days=1)
            cut=ReconciliationCut(source=auto.SOURCE, scheduled_for=scheduled,
                local_snapshot_at=now, snapshot_status="captured", status="waiting_source", attempt_count=0)
            db.session.add(cut); db.session.flush()
            for product in Product.query:
                db.session.add(ReconciliationCutStock(cut_id=cut.id,product_id=product.id,
                    product_code_snapshot=product.code,quantity=product.current_stock))
            cut_id=cut.id; db.session.commit()
        with APP.app_context():
            filename=auto.filename_for(db.session.get(ReconciliationCut,cut_id))
            example=('product_code;quantity\n'+''.join(f'{p.code};{p.current_stock}\n' for p in Product.query)).encode()
        self.assertEqual(actor.get(f'/api/reconciliation/cuts/{cut_id}/csv').status_code,403)
        self.assertEqual(actor.get(f'/api/reconciliation/cuts/{cut_id}').json['execution_type'],'real')
        self.assertEqual(actor.get(f'/api/reconciliation/cuts/{cut_id}/csv?variant=scenario').status_code,403)
        for invalid_name in ('DEMO.csv',filename.replace('_1600','_0800'),'inventario_2020-01-01_1600.csv'):
            self.assertEqual(upload(actor,headers,cut_id,example,invalid_name).status_code,400)
        run=upload(actor,headers,cut_id,example,filename).json
        self.assertEqual(run['source_type'],'automatic')
        self.assertEqual(run['discrepancy_count'],0)
        confirmed=actor.post(f'/api/reconciliation/{run["id"]}/confirm',json={},headers=headers)
        self.assertEqual(confirmed.status_code,200,confirmed.json)
        with APP.app_context():
            row=db.session.get(ReconciliationCut,cut_id)
            original=web._archive_path(cut_id,run['sha256'])
            archive=auto._root('RECONCILIATION_ARCHIVE_PATH')
            day=row.scheduled_for.replace(tzinfo=auto.timezone.utc).astimezone(auto.CARACAS).date().isoformat()
            target=archive/'processed'/day/auto.filename_for(row)
            target.parent.mkdir(parents=True,exist_ok=True)
            original.replace(target)
        self.assertEqual(actor.get(f'/api/reconciliation/cuts/{cut_id}/csv?variant=uploaded').data,example)


if __name__=='__main__':
    unittest.main(verbosity=2)
