// Execute the actual purchase UI with synthetic DOM/network data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync('app/static/js/ix_purchases.js', 'utf8');

function fixture(context, order) {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      textContent:'', innerHTML:'', open:false, style:{}, addEventListener() {},
      querySelector() { return node('button'); }, showModal() { this.open = true; },
      focus() {}, setAttribute() {},
    });
    return nodes.get(id);
  };
  node('po-context').textContent = JSON.stringify(context);
  const sandbox = {document:{getElementById:node, addEventListener() {}},
    window:{addEventListener() {}}, location:{href:'http://localhost:5001/purchases'},
    URL, setTimeout, clearTimeout, fetch:async () => ({ok:true, json:async () => order})};
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync('app/static/js/purchase_money.js', 'utf8'), sandbox);
  sandbox.PurchaseMoney = sandbox.window.PurchaseMoney;
  vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.auditUI = {actions, detail, confirmation, closureButtons}; })();'), sandbox);
  return {api:sandbox.auditUI, node};
}
const order = {id:1, order_number:'OC-SYNTHETIC', status:'approved', received_units:0,
  supplier:{name:'Proveedor sintético'}, creator:{id:2, name:'Asistente'},
  items:[], receptions:[], units:0, line_count:0, received_percent:0,
  closure_request:{kind:'unavailable', status:'pending', requested_by_user_id:2,
    requested_by:'Asistente', requested_at:'2026-10-08T12:00:00', reason:'Proveedor no despachará <script>'}};

test('assistant sees exact action in menu and detail only for approved without pending closure', async () => {
  const approved = {...order, closure_request:null};
  const f = fixture({canCloseRequest:true, userId:2}, approved);
  f.api.actions(approved, {setAttribute() {}, getBoundingClientRect() { return {}; }});
  assert.match(f.node('po-menu').innerHTML, /Solicitar cierre por falta de disponibilidad/);
  await f.api.detail(1);
  assert.match(f.node('po-dialog-body').innerHTML, /data-action="close_request"/);
  for (const status of ['pending_approval','rejected','closed_complete','closed_partial','closed_price_change','cancelled','closed_unavailable']) {
    assert.equal(f.api.closureButtons({...approved, status}), '');
  }
  assert.equal(f.api.closureButtons(order), '');
  assert.equal(f.api.closureButtons({...approved, received_units:1}), '');
});

test('unavailability is the default and a required reason is shown', async () => {
  const f = fixture({canCloseRequest:true, userId:2}, {...order, closure_request:null});
  await f.api.confirmation(1, 'close_request');
  assert.match(f.node('po-dialog-title').textContent, /Solicitar cierre por falta de disponibilidad/);
  assert.match(f.node('po-dialog-body').innerHTML, /select name="kind" required><option value="unavailable"/);
  assert.match(f.node('po-dialog-body').innerHTML, /Motivo obligatorio<textarea name="reason" required/);
  assert.match(f.node('po-dialog-body').innerHTML, /nueva solicitud de reposición/);
});

test('admin reviews trace with approve/reject controls and no commercial fields', async () => {
  const f = fixture({canCloseApprove:true, userId:3}, order);
  await f.api.detail(1);
  const html = f.node('po-dialog-body').innerHTML;
  assert.match(html, /Solicitado por.*Asistente/);
  assert.match(html, /Motivo del cierre.*Proveedor no despachará &lt;script&gt;/);
  assert.match(html, /data-action="close_approve"/);
  assert.match(html, /data-action="close_reject"/);
  await f.api.confirmation(1, 'close_approve');
  assert.doesNotMatch(f.node('po-dialog-body').innerHTML, /name="(?:supplier_id|quantity|unit_cost)"/);
  assert.match(f.node('po-dialog-body').innerHTML, /sin recepción ni cambios de stock/);
  const self = fixture({canCloseApprove:true, userId:2}, order);
  assert.equal(self.api.closureButtons(order), '');
});

test('closed trace names decider/date and suppresses stale decisions on received order', async () => {
  const closed = {...order, status:'closed_unavailable', closure_request:{...order.closure_request,
    status:'approved', decided_by_user_id:3, decided_by:'Administrador', decided_at:'2026-10-08T12:01:00'}};
  const f = fixture({canCloseApprove:true, userId:3}, closed);
  await f.api.detail(1);
  assert.match(f.node('po-dialog-body').innerHTML, /Decidido por.*Administrador/);
  assert.match(f.node('po-dialog-body').innerHTML, /Fecha de decisión \(UTC\).*2026-10-08T12:01:00/);
  assert.equal(f.api.closureButtons({...order, status:'closed_partial', received_units:1}), '');
  const assistant = fixture({canWrite:true, canCloseRequest:true, userId:2}, closed);
  assistant.api.actions(closed, {setAttribute() {}, getBoundingClientRect() { return {}; }});
  assert.doesNotMatch(assistant.node('po-menu').innerHTML, /data-action="duplicate"/);
});
