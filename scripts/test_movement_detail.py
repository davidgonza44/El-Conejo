"""Focused movement detail checks: in-memory SQLite and rendered drawer labels.

Runs the real inventory blueprint, authentication decorator and controller, without
the unrelated application factory or its global session-activity refresh hook.
No operational database, environment file, uploads or scientific data are used.
"""
import json
import os
import subprocess
from datetime import date
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

# Reuse the isolated suite's SQLite compiler and foreign-key enforcement.
from test_products_core import ROOT
from flask import Flask
from sqlalchemy import event, select

from app.extensions import db, login_manager
from app.models import Category, Customer, Product, Sale, SaleLine, StockMovement, User
from app.models.purchase import PurchaseOrder, PurchaseOrderLine, PurchaseReception, PurchaseReceptionLine, Supplier
from app.routes.inventory import inventory_bp
from app.services.exceptions import ApiError
from app.utils.schema import reset_cache


def fixtures():
    user = User(name="Detail test", username="detail_test", email="detail@example.test", role="admin")
    category = Category(name="Detail category")
    customer = Customer(name="Detail customer")
    supplier = Supplier(name="Detail supplier", tax_id="DETAIL-ONLY")
    db.session.add_all([user, category, customer, supplier])
    db.session.flush()
    product = Product(code="DETAIL", name="Detail product", category_id=category.id,
                      unit="unidad", current_stock=6, minimum_stock=0, purchase_price=1, sale_price=2)
    db.session.add(product)
    db.session.flush()
    rows = []
    for kind, before, after, reason in [
        ("entrada", 0, 10, "Recepción 331 · OC-000350"),
        ("salida", 10, 8, "Legacy reason without sale number"),
        ("ajuste", 8, 7, "Conteo físico"),
        ("entrada", 7, 8, "Recepción 331 · OC-000350"),
        ("salida", 8, 7, "Venta VTA-000042"),
        ("salida", 7, 6, "Nota de entrega #TEST"),
    ]:
        row = StockMovement(product_id=product.id, user_id=user.id, movement_type=kind,
                            quantity=abs(after-before), previous_stock=before, new_stock=after, reason=reason)
        db.session.add(row)
        rows.append(row)
    order = PurchaseOrder(id=350, order_number="OC-000350", supplier_id=supplier.id,
                          created_by_user_id=user.id, status="completed")
    db.session.add(order)
    db.session.flush()
    order_line = PurchaseOrderLine(purchase_order_id=order.id, product_id=product.id,
                                   product_code=product.code, product_name=product.name,
                                   quantity_ordered=10, quantity_received=10, unit_cost=1, line_total=10)
    reception = PurchaseReception(id=331, purchase_order_id=order.id, received_by_user_id=user.id,
                                  received_at=date(2025, 1, 1))
    sale = Sale(sale_number="VTA-000042", customer_id=customer.id, seller_id=user.id,
                payment_method="cash", payment_condition="cash", total=4, subtotal=4)
    db.session.add_all([order_line, reception, sale])
    db.session.flush()
    db.session.add(PurchaseReceptionLine(reception_id=reception.id, purchase_order_line_id=order_line.id,
                                        stock_movement_id=rows[0].id, quantity=10))
    db.session.add(SaleLine(sale_id=sale.id, product_id=product.id, stock_movement_id=rows[1].id,
                            product_code=product.code, product_name=product.name,
                            quantity=2, unit_price=2, line_subtotal=4, line_total=4))
    db.session.commit()
    return user.id, [row.id for row in rows]


def snapshot():
    return {table.name: [tuple(row) for row in db.session.execute(select(table).order_by(*table.primary_key.columns))]
            for table in db.metadata.sorted_tables}


def check_drawer(rows):
    # Execute the actual rendering functions, not a duplicate label mapper.
    script = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const source=fs.readFileSync(input.path,'utf8');
function node(tag,cls='',text='') {
    return {tag,tagName:tag.toUpperCase(),children:[],_text:String(text),dataset:{},className:cls,
        get textContent(){return this._text+this.children.map(c=>c.textContent).join('');},
        set textContent(value){this._text=String(value);this.children=[];},
        setAttribute(){},addEventListener(){},append(...children){this.children.push(...children);},
        replaceChildren(...children){this._text='';this.children=children;},querySelector(){return node('div');}};
}
const content=node('div');
const context={node,content,footer:()=>node('footer'),date:()=> '01/01/2025',signed:r=>r.quantity,
    badge:()=>node('span'),product:()=>node('div'),live:{labels:{}},document:{querySelectorAll:()=>[]}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('    function fieldList('),source.indexOf('    function footer('))+
    source.slice(source.indexOf('    function details('),source.indexOf('    async function detail(')),context);
const tableSource=fs.readFileSync('app/static/js/ix_movements_live.js','utf8');
vm.runInContext(tableSource.slice(tableSource.indexOf('    function referenceNode('),tableSource.indexOf('    function tableMessage(')),context);
context.live.referenceNode=context.referenceNode;
function fields(el,result={}) {
    if(el.tag==='div' && el.children[0]?.tag==='dt') result[el.children[0].textContent]=el.children[1].textContent;
    el.children.forEach(child=>fields(child,result));return result;
}
for(const row of input.rows){
    context.details(row);
    const rendered=fields(content);
    assert.equal(rendered.Estado,'Aplicado');
    assert.ok(!('Sucursal' in rendered));
    assert.equal(rendered.Referencia,row.display_reference || '—');
    const table=context.renderRow(row);
    assert.equal(table.children[2].children[0].textContent,rendered.Referencia);
    assert.equal(table.children[8].textContent,rendered.Estado);
    assert.equal(rendered.Motivo,row.reason);
    assert.ok(!Object.values(rendered).includes('No persistido'));
}
console.log('PASS: 6 actual table/drawer renders with identical Estado/Referencia, including unlinked adjustment.');
"""
    result = subprocess.run(["node", "-e", script], input=json.dumps({
        "path": str(ROOT / "app/static/js/ix_movements_live_layers.js"), "rows": rows,
    }), text=True, capture_output=True, encoding="utf-8", check=True)
    print(result.stdout.strip())


def run():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="isolated-detail-test", SQLALCHEMY_DATABASE_URI="sqlite://",
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    login_manager.init_app(app)
    app.register_blueprint(inventory_bp)
    app.register_error_handler(ApiError, lambda error: ({"error": error.message}, error.status_code))
    with app.app_context():
        reset_cache()
        assert db.engine.url.drivername == "sqlite" and db.engine.url.database in (None, "")
        db.create_all()
        user_id, ids = fixtures()
        assert "status" not in StockMovement.__table__.columns
        assert "reference" not in StockMovement.__table__.columns
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

        def get(path, authenticated=True):
            # Each request gets a fresh Flask-Login cache, as in production.
            with app.app_context():
                return (client if authenticated else app.test_client()).get(path)

        before = snapshot()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.lstrip().split()[0].upper())

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            rows = []
            for movement_id in ids:
                response = get(f"/api/inventory/movements/{movement_id}")
                assert response.status_code == 200
                rows.append(response.json)
            assert [row["presentation"] for row in rows] == [
                {"status_label": "Aplicado", "reference": reference}
                for reference in ("OC-000350 · REC-00331", "VTA-000042", *(f"MOV-{key:06d}" for key in ids[2:]))
            ]
            for row in rows:
                assert "status" not in row
                stored = db.session.get(StockMovement, row["id"]).to_dict()
                assert all(row[key] == value for key, value in stored.items())
            assert get("/api/inventory/movements/99999").status_code == 404
            assert get(f"/api/inventory/movements/{ids[0]}", authenticated=False).status_code == 401
            with patch("app.services.movement_traceability_service.table_ready", return_value=False):
                for movement_id in ids[:2]:
                    response = get(f"/api/inventory/movements/{movement_id}")
                    assert response.status_code == 200 and response.json["presentation"]["reference"] == f"MOV-{movement_id:06d}"
            assert not ({"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"} & set(statements))
            assert snapshot() == before
            check_drawer(rows)
            print("PASS: 6 persisted movement details, structured origins, unlinked text fallbacks, optional-table fallback, 401/404; all-table snapshot unchanged and no SQL writes.")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
            db.session.remove()
            db.engine.dispose()
            reset_cache()


if __name__ == "__main__":
    run()
