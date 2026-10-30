// Correcciones del tutor (5 oct 2026): ejecuta el código real de Compras y Recepciones con DOM y red sintéticos.
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

function purchases(context, replies) {
  const nodes = new Map(), calls = [], assigned = [];
  element(nodes, 'po-context').textContent = JSON.stringify({canViewCost:false, userId:9, today:'2026-10-17', items:[], ...context});
  const location = {href:'http://localhost/purchases', assign(url) { assigned.push(String(url)); }, replace(url) { assigned.push(String(url)); }};
  const sandbox = {document:{getElementById:id => element(nodes, id), addEventListener() {}, querySelector() { return null; }},
    window:{addEventListener() {}, location, open() {}}, location, URL, URLSearchParams, setTimeout, clearTimeout,
    FormData:function(form) { return Object.entries(form.values); },
    fetch:async (path, options) => { calls.push({path, options}); return {ok:true, json:async () => replies(path, options)}; }};
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync('app/static/js/purchase_money.js', 'utf8'), sandbox);
  sandbox.PurchaseMoney = sandbox.window.PurchaseMoney;
  const source = fs.readFileSync('app/static/js/ix_purchases.js', 'utf8');
  vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.ui = {actions, detail, confirmation, reception, save, togglePartialClosure}; })();'), sandbox);
  return {ui:sandbox.ui, node:id => element(nodes, id), calls, assigned};
}

const line = {id:31, product_id:5, product_code:'P-5', product_name:'Cemento', quantity_ordered:10, quantity_received:0,
  quantity_pending:10, quantity_expected:10, quantity_not_supplied:0};
const base = {id:7, order_number:'OC-000007', status:'approved', supplier:{id:3, name:'Proveedor', tax_id:'J-1'},
  creator:{id:2, name:'Asistente'}, ordered_date:'2026-10-10', currency:'VES', items:[line], receptions:[], units:10,
  line_count:1, received_percent:0, received_units:0, quantity_not_supplied:0, shortage_requests:[], closure_request:null};
const receptionForm = (quantity, extra = {}) => ({id:'po-reception-form', values:{received_at:'2026-10-17', notes:'', ['line-31']:String(quantity), closure_reason:extra.reason || ''},
  elements:{['line-31']:{value:String(quantity)}, confirm_partial_closure:{checked:Boolean(extra.confirm), required:false}, closure_reason:{required:false}}});

test('draft detail and menu are labelled as not final and offer no duplicate', async () => {
  const draft = {...base, status:'pending_approval', replenishment_request_id:4};
  const f = purchases({canWrite:true}, () => draft);
  f.ui.actions(draft, {setAttribute() {}, getBoundingClientRect() { return {}; }});
  assert.doesNotMatch(f.node('po-menu').innerHTML, /Duplicar/);
  assert.match(f.node('po-menu').innerHTML, /Imprimir borrador/);
  assert.match(f.node('po-menu').innerHTML, /data-action="renew"/);
  await f.ui.detail(7);
  assert.match(f.node('po-dialog-title').textContent, /^Borrador de orden/);
  assert.match(f.node('po-dialog-body').innerHTML, /no es una orden de compra definitiva/);
});

test('Compras reception: partial needs explicit confirmation and reason; complete sends neither', async () => {
  const f = purchases({canReceive:true}, () => base);
  const rendered = receptionForm(6);
  Object.assign(f.node('po-reception-form'), {elements:rendered.elements});
  await f.ui.reception(7);
  assert.match(f.node('po-dialog-body').innerHTML, /id="po-partial"/);
  assert.equal(f.node('po-partial').hidden, false);
  assert.equal(rendered.elements.closure_reason.required, true);
  assert.equal(rendered.elements.confirm_partial_closure.required, true);
  await assert.rejects(f.ui.save(receptionForm(6)), /Confirme el cierre definitivo/);
  assert.equal(f.calls.filter(call => call.options.method === 'POST').length, 0);
  await f.ui.save(receptionForm(6, {confirm:true, reason:'Proveedor sin existencia del resto'}));
  const partial = JSON.parse(f.calls.at(-1).options.body);
  assert.equal(f.calls.at(-1).path, '/api/purchases/7/receptions');
  assert.equal(partial.confirm_partial_closure, true);
  assert.equal(partial.closure_reason, 'Proveedor sin existencia del resto');
  await f.ui.save(receptionForm(10));
  const complete = JSON.parse(f.calls.at(-1).options.body);
  assert.equal('confirm_partial_closure' in complete, false);
  assert.equal('closure_reason' in complete, false);
});

test('closed partial order shows not supplied apart from expected and offers the shortage request', async () => {
  const closed = {...base, status:'closed_partial', closed_at:'2026-10-17T12:00:00', closure_reason:'Solo llegaron 6',
    quantity_not_supplied:4, items:[{...line, quantity_received:6, quantity_pending:4, quantity_expected:0, quantity_not_supplied:4}]};
  const f = purchases({canRequestShortage:true}, path => path.startsWith('/api/replenishment-requests') ? {id:12} : closed);
  await f.ui.detail(7);
  const html = f.node('po-dialog-body').innerHTML;
  assert.match(html, /<th>Por recibir<\/th><th>No suministrado<\/th>/);
  assert.match(html, /<td>6<\/td><td>0<\/td><td>4<\/td>/);
  assert.match(html, /Solo llegaron 6/);
  assert.match(html, /data-action="shortage"/);
  await f.ui.confirmation(7, 'shortage');
  assert.match(f.node('po-dialog-body').innerHTML, /La orden original no cambia/);
  await f.ui.save({id:'po-confirm-form', values:{}});
  assert.equal(f.calls.at(-1).path, '/api/replenishment-requests/from-order/7');
  assert.equal(f.calls.at(-1).options.body, '{}');
  assert.deepEqual(f.assigned, ['/purchases/requests/12']);
});

test('an existing shortage request is linked instead of offering another', async () => {
  const closed = {...base, status:'closed_partial', quantity_not_supplied:4,
    shortage_requests:[{id:12, request_number:'SR-000012', status:'open', status_label:'Abierta'}]};
  const f = purchases({canRequestShortage:true}, () => closed);
  await f.ui.detail(7);
  assert.doesNotMatch(f.node('po-dialog-body').innerHTML, /data-action="shortage"/);
  assert.match(f.node('po-dialog-body').innerHTML, /SR-000012/);
});

function receptions(order) {
  const nodes = new Map(), calls = [];
  element(nodes, 'rc-context').textContent = JSON.stringify({today:'2026-10-17', responsible:'Inventario', canReceive:true});
  const inputs = [{dataset:{line:'31'}, value:'10', max:'10', checkValidity() { return true; }}];
  const form = {elements:{received_at:{value:'2026-10-17'}, notes:{value:''}, confirm_partial_closure:{checked:false, required:false},
    closure_reason:{value:'', required:false}}, querySelectorAll:() => inputs, querySelector:selector => element(nodes, 'form ' + selector)};
  nodes.set('rc-receive-form', form);
  const body = element(nodes, 'rc-drawer-body');
  body.querySelectorAll = () => inputs;
  const sandbox = {document:{getElementById:id => element(nodes, id), addEventListener() {}}, location:{search:'', href:''}, URL, URLSearchParams,
    setTimeout, clearTimeout, fetch:async (path, options) => { calls.push({path, options}); return {ok:true, json:async () => ({receptions:[{id:1}]})}; }};
  vm.createContext(sandbox);
  const source = fs.readFileSync('app/static/js/ix_receptions.js', 'utf8');
  vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.ui = {preview, save, select(value) { selectedOrder = value; }}; })();'), sandbox);
  sandbox.ui.select(order);
  return {ui:sandbox.ui, node:id => element(nodes, id), inputs, form, calls};
}

test('Recepciones form applies the same partial closure contract', async () => {
  const f = receptions(base);
  f.ui.preview();
  assert.equal(f.node('rc-partial').hidden, true);
  assert.equal(f.form.elements.closure_reason.required, false);
  f.inputs[0].value = '6';
  f.ui.preview();
  assert.equal(f.node('rc-partial').hidden, false);
  assert.equal(f.form.elements.closure_reason.required, true);
  assert.match(f.node('rc-status').value, /cierre definitivo/);
  await f.ui.save(f.form);
  assert.match(f.node('form .rc-error').textContent, /Confirme el cierre definitivo/);
  assert.equal(f.calls.length, 0);
  f.form.elements.confirm_partial_closure.checked = true;
  f.form.elements.closure_reason.value = 'Proveedor sin existencia del resto';
  await f.ui.save(f.form);
  const sent = JSON.parse(f.calls.at(-1).options.body);
  assert.equal(sent.confirm_partial_closure, true);
  assert.equal(sent.closure_reason, 'Proveedor sin existencia del resto');
  assert.deepEqual(sent.items, [{line_id:31, quantity:6}]);
});
