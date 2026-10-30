"""CSV boundaries, bulk rollback and bounded API contracts; disposable SQLite only."""
import unittest
from datetime import datetime
from io import BytesIO
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import StockMovement
from app.models.reconciliation import ReconciliationRun as Run, ReconciliationLine as Line
from app.services import reconciliation_service as service
from app.services.exceptions import ValidationError
from app.controllers import reconciliation_controller as controller


def csv_rows(n):
    return ('product_code;quantity\n'+''.join(f'SYN-{i:08d};10\n' for i in range(n))).encode()


class ParserLimits(unittest.TestCase):
    def test_all_scales_and_row_boundary(self):
        for n in (1, 5000, 20000, 30000, 50000):
            with self.subTest(rows=n):
                lines=service.parse_csv(csv_rows(n))
                self.assertEqual(len(lines),n)
                self.assertEqual(lines[-1].row_number,n+1)
                self.assertTrue(all(line.result=='unmatched' for line in lines))
        with self.assertRaises(ValidationError): service.parse_csv(csv_rows(service.MAX_ROWS+1))

    def test_exact_byte_boundary_and_one_above(self):
        header=b'product_code;quantity;product_name\n'
        base=[f'SYN-{i:08d};10;'.encode() for i in range(service.MAX_ROWS)]
        padding=service.MAX_BYTES-len(header)-sum(len(row)+1 for row in base)
        width,remainder=divmod(padding,len(base))
        content=header+b''.join(row+b' '*(width+(i<remainder))+b'\n' for i,row in enumerate(base))
        self.assertEqual(len(content),service.MAX_BYTES)
        self.assertEqual(len(service.parse_csv(content)),service.MAX_ROWS)
        with self.assertRaises(ValidationError): service.parse_csv(content+b'\n')

    def test_contract_bom_duplicates_invalid_utf8_and_lengths(self):
        rows=service.parse_csv(('\ufeffproduct_code;quantity;product_name\nA;1;Árbol\na;2;dup\nB;1.5;x\n'
            +'C'*51+';1;x\nD;1;'+'N'*151+'\nE;2147483648;x\n').encode())
        self.assertEqual([r.result for r in rows],['unmatched']+['invalid']*5)
        self.assertEqual(rows[0].product_name,'Árbol')
        for data in (b'\xff',b'product_code,quantity\nA,1',b'product_code;quantity\nA;1;2',
                     b'product_code;quantity\nA',b'product_code;quantity;quantity\nA;1;2'):
            with self.assertRaises(ValidationError): service.parse_csv(data)


class ScalableAPI(IsolatedCase):
    def upload(self,content,query=''):
        return self.clients['admin'].post('/api/reconciliation'+query,data={
            'file':(BytesIO(content),'synthetic.csv'),'cutoff_at':'2026-09-30T12:00:00-04:00'},
            content_type='multipart/form-data')

    def fixture(self):
        run=Run(filename='synthetic.csv',sha256='0'*64,cutoff_at=datetime(2026,9,30),
                status='confirmed',received_count=121,valid_count=91,invalid_count=30,
                match_count=31,discrepancy_count=30,unmatched_count=30)
        db.session.add(run)
        db.session.flush()
        db.session.execute(Line.__table__.insert(),[dict(run_id=run.id,row_number=i+2,product_code=f'SYN-{i}',
            external_quantity=i,system_quantity=i,difference=0,result=('match','discrepancy','unmatched','invalid')[i%4],
            review_status=('pending','reviewed','resolved')[i%3],reviewed_by_user_id=self.users['admin'].id,
            resolved_by_user_id=self.users['admin2'].id) for i in range(121)])
        db.session.commit()
        return run.id

    def test_bulk_upload_and_bounded_mutations(self):
        inserts=[]
        def observed(conn,cursor,sql,params,context,many):
            if sql.startswith('INSERT INTO reconciliation_lines'): inserts.append((many,len(params)))
        event.listen(db.engine,'before_cursor_execute',observed)
        try: response=self.upload(csv_rows(6000))
        finally: event.remove(db.engine,'before_cursor_execute',observed)
        self.assertEqual(response.status_code,201,response.json)
        self.assertEqual(inserts,[(True,2500),(True,2500),(True,1000)])
        self.assertEqual((response.json['total'],len(response.json['items'])),(6000,50))
        run_id=response.json['id']
        confirmed=self.clients['admin'].post(f'/api/reconciliation/{run_id}/confirm',json={})
        self.assertEqual(confirmed.status_code,200)
        self.assertEqual(len(confirmed.json['items']),50)
        self.assertEqual(Line.query.filter_by(run_id=run_id).count(),6000)
        self.assertEqual(StockMovement.query.count(),0)
        self.assertEqual(self.stock(),10)

    def test_failure_after_executed_batch_rolls_back_everything(self):
        def fail(conn,cursor,sql,params,context,many):
            if sql.startswith('INSERT INTO reconciliation_lines'): raise SQLAlchemyError('synthetic failure')
        event.listen(db.engine,'after_cursor_execute',fail)
        try: response=self.upload(csv_rows(6000))
        finally: event.remove(db.engine,'after_cursor_execute',fail)
        self.assertEqual(response.status_code,409)
        self.assertEqual((Run.query.count(),Line.query.count(),StockMovement.query.count()),(0,0,0))
        self.assertEqual(self.stock(),10)
        self.assertEqual(self.upload(csv_rows(6000)).status_code,201)

    def test_bulk_preserves_comparison_fields_and_generated_ids(self):
        content='product_code;quantity;product_name\nTEST-01;14;Árbol\nUNKNOWN;2;x\nBAD;-1;x\ntest-01;4;duplicado\n'.encode()
        lines=service.parse_csv(content)
        service._compare(lines)
        fields=('row_number','product_code','product_name','product_id','external_quantity',
                'system_quantity','difference','result','error')
        expected=[tuple(getattr(line,key) for key in fields) for line in lines]
        run=Run(filename='synthetic.csv',sha256='0'*64,cutoff_at=datetime(2026,9,30))
        service.persist_new_lines(run,lines)
        db.session.commit()
        saved=Line.query.filter_by(run_id=run.id).order_by(Line.row_number).all()
        self.assertEqual([tuple(getattr(line,key) for key in fields) for line in saved],expected)
        self.assertEqual(len(set(line.id for line in saved)),4)
        self.assertTrue(all(line.id and line.review_status=='pending' and line.stock_movement_id is None for line in saved))

    def test_paging_filters_global_counts_and_null_source(self):
        run_id=self.fixture()
        client=self.clients['admin']
        first=client.get(f'/api/reconciliation/{run_id}').json
        second=client.get(f'/api/reconciliation/{run_id}?page=2').json
        self.assertEqual((first['total'],first['pages'],len(first['items'])),(121,3,50))
        self.assertIsNone(first['source_type'])
        self.assertTrue(set(x['id'] for x in first['items']).isdisjoint(x['id'] for x in second['items']))
        self.assertEqual(second['items'][0]['row_number'],52)
        for result in ('match','discrepancy','unmatched','invalid'):
            for review in ('pending','reviewed','resolved'):
                for size in (5,25):
                    query=f'/api/reconciliation/{run_id}?result={result}&review_status={review}&per_page={size}'
                    data=client.get(query).json
                    expected=Line.query.filter_by(run_id=run_id,result=result,review_status=review).order_by(Line.row_number).all()
                    self.assertEqual(data['total'],len(expected))
                    self.assertEqual(data['received_count'],121)
                    self.assertTrue(all(r['result']==result and r['review_status']==review for r in data['items']))
                    rows=list(data['items'])
                    for number in range(2,data['pages']+1):
                        rows.extend(client.get(query+f'&page={number}').json['items'])
                    self.assertEqual([row['id'] for row in rows],[row.id for row in expected])
        for options in ('page=0','page=-1','per_page=50000','result=bad','review_status=bad'):
            self.assertEqual(client.get(f'/api/reconciliation/{run_id}?{options}').status_code,400)
        self.assertEqual(client.get(f'/api/reconciliation/{run_id}?page=999').json['page'],3)
        self.assertEqual(client.get('/api/reconciliation/2147483647').status_code,404)
        for actor in ('inventario','vendedor'):
            self.assertEqual(self.clients[actor].get(f'/api/reconciliation/{run_id}?page=2').status_code,403)

    def test_constant_query_count_empty_filter_and_history(self):
        run_id=self.fixture()
        for i in range(20):
            db.session.add(Run(filename='synthetic.csv',sha256='0'*64,cutoff_at=datetime(2026,9,30),
                               created_by_user_id=self.users['admin'].id))
        db.session.commit()
        controller.available()
        for path,fn,expected in [('/api/reconciliation',controller.listing,2),
                (f'/api/reconciliation/{run_id}',lambda:controller.detail(run_id),3)]:
            db.session.remove()
            queries=[]
            def observe(conn,cursor,sql,params,context,many):
                if sql.lstrip().startswith('SELECT'): queries.append(sql)
            event.listen(db.engine,'before_cursor_execute',observe)
            try:
                with self.app.test_request_context(path): fn()
            finally: event.remove(db.engine,'before_cursor_execute',observe)
            self.assertEqual(len(queries),expected,queries)
        empty=Run.query.filter(Run.id!=run_id).first().id
        result=self.clients['admin'].get(f'/api/reconciliation/{empty}?result=discrepancy').json
        self.assertEqual((result['items'],result['total'],result['pages']),([],0,1))
        page=self.clients['admin'].get('/reconciliation')
        self.assertIn(b'rc-line-next',page.data)

    def test_bad_pagination_rejected_before_mutation(self):
        result=self.upload(csv_rows(2),'?per_page=50000')
        self.assertEqual(result.status_code,400)
        self.assertEqual(Run.query.count(),0)


if __name__=='__main__': unittest.main(verbosity=2)
