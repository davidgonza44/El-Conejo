// Seguimiento del 17 oct 2026: presentación de órdenes cerradas y faltante liberado, con el código real de Compras.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

function element(store, id) {
  if (!store.has(id)) store.set(id, {id, textContent:'', innerHTML:'', value:'', hidden:false, open:false, style:{}, elements:{},
    addEventListener() {}, querySelector(selector) { return element(store, selector); }, querySelectorAll() { return []; },
    showModal() { this.open = true; }, close() { this.open = false; }, focus() {}, setAttribute() {},
    getBoundingClientRect() { return {}; }});
  return store.get(id);
}

function purchases(context, order) {
  const nodes = new Map();
  element(nodes, 'po-context').textContent = JSON.stringify({canViewCost:false, userId:9, today:'2026-10-17', items:[], ...context});
  const location = {href:'http://localhost/purchases', assign() {}, replace() {}};
  const sandbox = {document:{getElementById:id => element(nodes, id), addEventListener() {}, querySelector() { return null; }},
    window:{addEventListener() {}, location, open() {}}, location, URL, URLSearchParams, setTimeout, clearTimeout,
    fetch:async () => ({ok:true, json:async () => order})};
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync('app/static/js/purchase_money.js', 'utf8'), sandbox);
  sandbox.PurchaseMoney = sandbox.window.PurchaseMoney;
  const source = fs.readFileSync('app/static/js/ix_purchases.js', 'utf8');
  vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.ui = {detail}; })();'), sandbox);
  return {ui:sandbox.ui, node:id => element(nodes, id)};
}

const line = {id:31, product_id:5, product_code:'P-5', product_name:'Cemento', quantity_ordered:10, quantity_received:6,
  quantity_pending:4, quantity_expected:0, quantity_not_supplied:4, unit_cost:'10.00', line_total:'100.00'};
const closed = {id:7, order_number:'OC-000007', status:'closed_partial', supplier:{id:3, name:'Proveedor', tax_id:'J-1'},
  creator:{id:2, name:'Asistente'}, ordered_date:'2026-10-10', currency:'VES', items:[line], receptions:[], units:10,
  line_count:1, received_percent:60, received_units:6, quantity_not_supplied:4, expecting_supply:false,
  pending_amount:'0.00', not_supplied_amount:'40.00', subtotal:'100.00', total:'100.00', closure_request:null,
  closed_at:'2026-10-17T12:00:00', closure_reason:'Solo llegaron 6', shortage_requests:[]};

test('closed partial detail shows not supplied, never an amount still to receive', async () => {
  const f = purchases({canViewCost:true}, closed);
  await f.ui.detail(7);
  const html = f.node('po-dialog-body').innerHTML;
  assert.match(html, /<strong>Monto por recibir<\/strong>Sin mercancía por recibir: la orden está cerrada/);
  assert.match(html, /<strong>No suministrado<\/strong>4 unidades · Bs\. 40[.,]00/);
  assert.doesNotMatch(html, /Monto pendiente/);
});

test('without cost permission the shortage is shown in units only', async () => {
  const {pending_amount, not_supplied_amount, ...hidden} = closed;
  const f = purchases({}, hidden);
  await f.ui.detail(7);
  const html = f.node('po-dialog-body').innerHTML;
  assert.match(html, /<strong>No suministrado<\/strong>4 unidades</);
  assert.doesNotMatch(html, /40/);
});

test('an approved order keeps its amount to receive', async () => {
  const approved = {...closed, status:'approved', expecting_supply:true, pending_amount:'100.00', closed_at:null, closure_reason:null,
    quantity_not_supplied:0, items:[{...line, quantity_received:0, quantity_pending:10, quantity_expected:10, quantity_not_supplied:0}]};
  const f = purchases({canViewCost:true}, approved);
  await f.ui.detail(7);
  const html = f.node('po-dialog-body').innerHTML;
  assert.match(html, /<strong>Monto por recibir<\/strong>Bs\. 100[.,]00/);
  assert.doesNotMatch(html, /<strong>No suministrado<\/strong>/);
});

test('a request whose order was rejected no longer covers the shortage and the action returns', async () => {
  const freed = {...closed, shortage_requests:[{id:12, request_number:'SR-000012', status:'fulfilled', status_label:'Convertida en OC',
    purchase_order_id:20, purchase_order_number:'OC-000020', purchase_order_status_label:'Rechazada', covers_shortage:false}]};
  const f = purchases({canRequestShortage:true}, freed);
  await f.ui.detail(7);
  const html = f.node('po-dialog-body').innerHTML;
  assert.match(html, /SR-000012<\/a> · Convertida en OC · OC-000020 Rechazada/);
  assert.match(html, /Ya no cubre el faltante/);
  assert.match(html, /data-action="shortage"/);
});

test('a request that still covers the shortage keeps the action hidden', async () => {
  const covered = {...closed, shortage_requests:[{id:12, request_number:'SR-000012', status:'fulfilled', status_label:'Convertida en OC',
    purchase_order_id:20, purchase_order_number:'OC-000020', purchase_order_status_label:'Aprobada / pendiente de recepción', covers_shortage:true}]};
  const f = purchases({canRequestShortage:true}, covered);
  await f.ui.detail(7);
  const html = f.node('po-dialog-body').innerHTML;
  assert.doesNotMatch(html, /data-action="shortage"/);
  assert.doesNotMatch(html, /Ya no cubre/);
});
