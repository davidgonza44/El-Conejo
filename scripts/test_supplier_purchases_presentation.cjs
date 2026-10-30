// Actual supplier renderer with synthetic DOM/network data; no server or database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync('app/static/js/ix_suppliers.js', 'utf8');
const statuses = ['pending_approval', 'pending', 'approved', 'partial', 'completed',
  'rejected', 'cancelled', 'closed_unavailable', 'closed_price_change', 'closed_complete', 'closed_partial'];
const labels = Object.fromEntries(statuses.map((status, index) => [status, `Etiqueta ${index}`]));

function fixture(overrides = {}) {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      textContent:'', innerHTML:'', hidden:true, elements:{notes:{value:'', addEventListener() {}}},
      listeners:new Map(), isConnected:true, tabIndex:0, disabled:false,
      addEventListener(type, listener) { this.listeners.set(type, listener); },
      querySelector(selector) { return node(selector); }, querySelectorAll() { return []; },
      closest() { return null; }, matches() { return this.disabled || this.inert; },
      getClientRects() { return this.hidden ? [] : [{}]; },
      insertAdjacentHTML(_position, html) { this.innerHTML += html; },
      showModal() { this.open = true; }, focus(options) { document.activeElement = this; this.focusOptions = options; }, setAttribute() {},
    });
    return nodes.get(id);
  };
  const documentEvents = new Map();
  const document = {getElementById:node, activeElement:null,
    addEventListener(type, listener) { documentEvents.set(type, listener); }};
  const context = {reference:false, canViewCost:true, purchaseStatusLabels:labels, ...overrides};
  node('sp-context').textContent = JSON.stringify(context);
  const requests = [];
  const sandbox = {
    document, window:{addEventListener() {}},
    location:{href:'http://localhost:5001/suppliers'}, URL, setTimeout, clearTimeout,
    fetch:async (path, options) => {
      requests.push({path, method:options.method});
      return {ok:true, json:async () => supplier};
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync('app/static/js/purchase_money.js', 'utf8'), sandbox);
  sandbox.PurchaseMoney = sandbox.window.PurchaseMoney;
  vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.auditUI = {purchases, suppliedProducts, orderLinks, detail}; })();'), sandbox);
  return {ui:sandbox.auditUI, node, requests, document, documentEvents};
}

const product = {product_name:'Llave <especial>', product_code:'PROD-1',
  supplier_product_code:'SUP-1', purchase_price:'0.00', lead_time_days:1,
  operational_preferred:true, is_active:true};
const supplier = {id:1, name:'Proveedor sintético', is_active:true, orders:11, pending:0,
  completed:0, last:'2026-10-08', created_at:'2026-10-01T12:00:00', updated_at:'2026-10-08T12:00:00',
  totals_by_currency:[{currency:'USD', amount:'17.00'}, {currency:'VES', amount:'263.50'}],
  supplied_products:[product, {...product, product_name:'Segundo producto', lead_time_days:0}],
  recent_orders:statuses.map((status, index) => ({id:index+1, number:`OC-${index+1}`,
    date:'2026-10-08', status, currency:index%2 ? 'VES' : 'USD', total:'1.25'}))};

test('independent currencies, zero counts and dates preserve source data', () => {
  const before = JSON.stringify(supplier);
  const html = fixture().ui.purchases(supplier, 1);
  assert.match(html, /data-currency="USD"><dt>Total comprado USD<\/dt><dd>\$ 17\.00/);
  assert.match(html, /data-currency="VES"><dt>Total comprado VES<\/dt><dd>Bs\. 263\.50/);
  assert.match(html, /Pendientes o parciales<\/dt><dd>0<\/dd>/);
  assert.match(html, /Órdenes completadas<\/dt><dd>0<\/dd>/);
  assert.match(html, /Última compra<\/dt><dd>08\/10\/2026/);
  assert.equal(JSON.stringify(supplier), before);
  assert.doesNotMatch(html, /280\.50/);
});

test('every backend status uses the injected domain label; unknown codes stay hidden', () => {
  const f = fixture();
  const html = f.ui.orderLinks(supplier.recent_orders);
  for (const [status, label] of Object.entries(labels)) {
    assert(html.includes(label));
    assert(!html.includes(status));
  }
  assert.equal((html.match(/class="sp-order-item"/g) || []).length, 11);
  assert.match(html, /<time datetime="2026-10-08">08\/10\/2026/);
  assert.match(html, /Bs\. 1\.25 <small>VES/);
  const unknown = f.ui.orderLinks([{id:12, status:'new_internal_code', currency:null, total:'5.00'}]);
  assert.match(unknown, /Estado no disponible/);
  assert.match(unknown, /Moneda no registrada · 5\.00/);
  assert.doesNotMatch(unknown, /new_internal_code|\$|Bs\./);
});

test('products are separate, escaped and never assigned an unsupported currency', () => {
  const html = fixture().ui.suppliedProducts(supplier.supplied_products);
  assert.equal((html.match(/class="sp-product-item"/g) || []).length, 2);
  assert.match(html, /Llave &lt;especial&gt;/);
  assert.match(html, /Costo referencial<\/dt><dd>0\.00<small>Moneda no registrada/);
  assert.match(html, /Entrega estimada<\/dt><dd>1 día/);
  assert.match(html, /Entrega estimada<\/dt><dd>0 días/);
  assert.match(html, /is-positive">Preferido/);
  assert.doesNotMatch(html, /\$|Bs\.|USD|VES|Lead time|<especial>/);
});

test('missing data, unavailable lists and empty lists have distinct truthful states', () => {
  const f = fixture();
  assert.match(f.ui.suppliedProducts([]), /no tiene productos asociados/);
  assert.match(f.ui.suppliedProducts(null), /no está disponible/);
  const missing = f.ui.suppliedProducts([{is_active:false}]);
  assert.match(missing, /Nombre no registrado/);
  assert.match(missing, /Costo referencial<\/dt><dd>No registrado/);
  assert.match(missing, /Entrega estimada<\/dt><dd>No registrado/);
  assert.match(missing, /Asociación inactiva/);
  const html = f.ui.purchases({...supplier, totals_by_currency:[], last:'', recent_orders:[]}, 1);
  assert.match(html, /Sin compras en USD/);
  assert.match(html, /Sin compras en VES/);
  assert.match(html, /no tiene órdenes de compra recientes/);
  assert.doesNotMatch(html, /\$ 0\.00|Bs\. 0\.00/);
});

test('cost visibility is preserved and historical omissions remain individually discoverable', () => {
  const historical = {id:30, number:'OC-30', status:'completed', date:'2026-01-01', currency:null, total:'4.25'};
  const row = {...supplier, unknown_currency_orders:1, unknown_currency_order_details:[historical]};
  const html = fixture().ui.purchases(row, 1);
  assert.match(html, /1 orden sin moneda/);
  assert.match(html, /Órdenes históricas sin moneda \(1\)/);
  assert.match(html, /Moneda no registrada · 4\.25/);
  const redacted = fixture({canViewCost:false}).ui.purchases(row, 1);
  assert.doesNotMatch(redacted, /Total comprado|Costo referencial|Moneda no registrada|17\.00|263\.50|4\.25/);
  assert.match(redacted, /OC-30/);
});

test('opening detail uses only GET and retains four tab contracts and dialog', async () => {
  const f = fixture();
  await f.ui.detail(1);
  assert.deepEqual(f.requests, [{path:'/api/suppliers/1', method:'GET'}]);
  assert.equal(f.node('sp-detail').open, true);
  const html = f.node('sp-detail-body').innerHTML;
  for (const tab of ['information', 'purchases', 'contacts', 'history']) {
    assert(html.includes(`id="sp-tab-${tab}"`));
    assert(html.includes(`aria-controls="sp-panel-${tab}"`));
    assert(html.includes(`id="sp-panel-${tab}" role="tabpanel" tabindex="0"`));
  }
  assert.equal(f.node('sp-detail-body').scrollTop, 0);
});

function keyboardFixture() {
  const f = fixture();
  const dialog = f.node('sp-detail');
  const controls = ['close', 'tab', 'panel', 'order-link'].map(id => f.node(id));
  controls.forEach(control => { control.hidden = false; });
  dialog.open = true;
  dialog.querySelectorAll = () => controls;
  dialog.contains = control => controls.includes(control);
  const press = (active, shiftKey = false, key = 'Tab') => {
    f.document.activeElement = active;
    const event = {key, shiftKey, target:active, prevented:false,
      preventDefault() { this.prevented = true; }};
    f.documentEvents.get('keydown')(event);
    return event;
  };
  return {...f, dialog, controls, press};
}

test('modal detail wraps Shift+Tab from Close and Tab from the last link', () => {
  const f = keyboardFixture();
  const first = f.controls[0], last = f.controls.at(-1);
  assert(f.press(first, true).prevented);
  assert.equal(f.document.activeElement, last);
  assert(f.press(last).prevented);
  assert.equal(f.document.activeElement, first);
  assert(!f.press(f.controls[1]).prevented);
  assert(!f.press(f.controls[2], true).prevented);
});

test('tab cycle omits hidden panels, inactive tabs and disabled approval controls', () => {
  const f = keyboardFixture();
  const first = f.controls[0];
  f.controls[1].tabIndex = -1;
  f.controls[2].hidden = true;
  f.controls[3].disabled = true;
  assert(f.press(first, true).prevented);
  assert.equal(f.document.activeElement, first);
  assert(f.press(first).prevented);
  assert.equal(f.document.activeElement, first);
});

test('focus outside the open drawer is recovered, while closed drawer and Escape stay native', () => {
  const f = keyboardFixture();
  const outside = f.node('body');
  assert(f.press(outside).prevented);
  assert.equal(f.document.activeElement, f.controls[0]);
  assert(f.press(outside, true).prevented);
  assert.equal(f.document.activeElement, f.controls.at(-1));
  assert(!f.press(f.controls[0], false, 'Escape').prevented);
  f.dialog.open = false;
  assert(!f.press(outside).prevented);
  assert.equal(f.document.activeElement, outside);
});

test('closing asynchronous detail restores its captured provider trigger once', async () => {
  const f = fixture();
  const opener = f.node('provider-trigger');
  f.document.activeElement = opener;
  const opening = f.ui.detail(1);
  f.document.activeElement = f.node('other-trigger');
  await opening;
  const closed = f.node('sp-detail').listeners.get('close');
  closed();
  assert.equal(f.document.activeElement, opener);
  assert.equal(opener.focusOptions.preventScroll, true);
  f.document.activeElement = f.node('body');
  closed();
  assert.equal(f.document.activeElement, f.node('body'));
});
