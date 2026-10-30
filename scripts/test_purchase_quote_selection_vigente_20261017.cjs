// Copia vigente (posterior al 5 oct 2026) de test_purchase_quote_selection.cjs, que se conserva sin cambios.
// Único cambio: Compras ya no abre «Nueva orden de compra» (edit() sin id); toda orden nace de una solicitud.
// El primer caso abre ahora un borrador antiguo sin solicitud, el único que conserva este formulario.
// Los demás casos son idénticos al original.
// Executes the real modal code with a minimal DOM and controlled network promises.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync('app/static/js/ix_purchases.js', 'utf8');
function fixture() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      const node = { value:'', disabled:false, isConnected:true, events:{}, textContent:'',
        selectedOptions: [],
        addEventListener(event, fn) { this.events[event] = fn; },
        querySelector(selector) { return element(selector); },
        showModal() { this.open = true; }, focus() {}, setAttribute() {} };
      Object.defineProperty(node, 'innerHTML', { get() { return this.html || ''; },
        set(value) { this.html = value; if (id === '[name=supplier_quote_id]') this.value = ''; } });
      elements.set(id, node);
    }
    return elements.get(id);
  }
  element('po-context').textContent = JSON.stringify({canViewCost:true});
  const requests = [];
  const location = {href:'http://localhost:5001/purchases?supplier_id=1'};
  const sandbox = { document:{ getElementById:element, addEventListener() {} },
    window:{ addEventListener() {}, location }, location, URL, URLSearchParams,
    setTimeout, clearTimeout,
    fetch(path, options) { return new Promise(resolve => requests.push({path, options, reply(data) {
      resolve({ok:true, json:async () => data});
    }})); } };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync('app/static/js/purchase_money.js', 'utf8'), sandbox);
  sandbox.PurchaseMoney = sandbox.window.PurchaseMoney;
  vm.runInContext(source.replace(/\}\)\(\);\s*$/, `globalThis.modalTest = {
    edit, reloadQuotes, reloadProducts, clearQuoteProducts, api,
    setLines(value) { lines = value; }, getLines() { return lines; },
    products() { return options.products; }
  }; })();`), sandbox);
  return {element, requests, api:sandbox.modalTest};
}
const quote = (id, supplier_id) => ({id, supplier_id, currency:'VES', label:'Cotización sintética ' + id});
const line = {product_id:10, product_name:'Producto A', product_code:'A', quantity:1, unit_cost:'9.00'};

const legacyDraft = {id:7, order_number:'OC-000007', status:'pending_approval', supplier:{id:1, name:'A'},
  supplier_quote_id:null, currency:'VES', items:[], replenishment_request_id:null};

test('opening a legacy draft ignores stale URL supplier, has disabled empty quote; reopening clears lines', async () => {
  const f = fixture();
  const opening = f.api.edit(7);
  f.requests.shift().reply(legacyDraft);
  for (let i=0; i<10; i++) await Promise.resolve();
  f.requests.shift().reply({suppliers:[{id:1,name:'A',tax_id:'A'}], quotes:[quote(1,1)]});
  await opening;
  assert.equal(f.element('[name=supplier_id]').value, '');
  assert.equal(f.element('[name=supplier_quote_id]').disabled, true);
  assert.match(f.element('[name=supplier_quote_id]').innerHTML, /Seleccione primero un proveedor/);
  assert.equal(f.api.getLines().length, 0);
  assert.equal(f.requests.length, 0);
  f.api.setLines([line]);
  const reopen = f.api.edit(7);
  f.requests.shift().reply(legacyDraft);
  for (let i=0; i<10; i++) await Promise.resolve();
  f.requests.shift().reply({suppliers:[]});
  await reopen;
  assert.equal(f.api.getLines().length, 0);
});

test('supplier filters quotes, clears lines/search/prices, and supports no quotes', async () => {
  const f = fixture();
  f.element('[name=supplier_id]').value = '1';
  f.api.setLines([line]);
  f.element('po-product-search').value = 'anterior';
  const loading = f.api.reloadQuotes();
  assert.equal(f.api.getLines().length, 0);
  assert.equal(f.api.products().length, 0);
  assert.equal(f.element('po-product-search').value, '');
  f.requests.shift().reply({quotes:[quote(1,1), quote(2,2)]});
  await loading;
  assert.match(f.element('[name=supplier_quote_id]').innerHTML, /value="1"/);
  assert.doesNotMatch(f.element('[name=supplier_quote_id]').innerHTML, /value="2"/);
  assert.equal(f.element('[name=supplier_quote_id]').value, '');
  f.element('[name=supplier_id]').value = '2';
  const empty = f.api.reloadQuotes();
  f.requests.shift().reply({quotes:[]});
  await empty;
  assert.equal(f.element('[name=supplier_quote_id]').disabled, true);
  assert.match(f.element('[name=supplier_quote_id]').innerHTML, /No hay cotizaciones vigentes/);
});

test('late supplier and product responses cannot restore previous selection', async () => {
  const f = fixture();
  f.element('[name=supplier_id]').value = '1';
  const first = f.api.reloadQuotes();
  const old = f.requests.shift();
  f.element('[name=supplier_id]').value = '2';
  const second = f.api.reloadQuotes();
  f.requests.shift().reply({quotes:[quote(2,2)]});
  await second;
  old.reply({quotes:[quote(1,1)]});
  await first;
  assert.doesNotMatch(f.element('[name=supplier_quote_id]').innerHTML, /value="1"/);
  f.element('[name=supplier_quote_id]').value = '2';
  const products = f.api.reloadProducts();
  const pending = f.requests.shift();
  assert.match(pending.path, /supplier_id=2&supplier_quote_id=2/);
  f.element('[name=supplier_id]').value = '';
  await f.api.reloadQuotes();
  pending.reply({products:[{id:10,name:'Viejo',code:'OLD',unit_cost:'99.00'}]});
  await products;
  assert.equal(f.api.products().length, 0);
});

test('selecting quote clears prior lines and loads only its returned products/prices', async () => {
  const f = fixture();
  f.element('[name=supplier_id]').value = '1';
  f.element('[name=supplier_quote_id]').value = '3';
  f.element('[name=supplier_quote_id]').selectedOptions = [{dataset:{currency:'VES'}}];
  f.api.setLines([line]);
  f.element('po-dialog-body').events.change({target:{name:'supplier_quote_id'}});
  assert.equal(f.api.getLines().length, 0);
  assert.equal(f.element('[name=currency]').value, 'VES');
  const pending = f.requests.shift();
  assert.match(pending.path, /supplier_quote_id=3/);
  pending.reply({products:[{id:20,name:'Nuevo',code:'NEW',unit_cost:'12.50'}]});
  for (let i=0; i<10; i++) await Promise.resolve();
  assert.deepEqual(JSON.parse(JSON.stringify(f.api.products())), [{id:20,name:'Nuevo',code:'NEW',unit_cost:'12.50'}]);
  assert.match(f.element('po-product-results').innerHTML, /Bs\. 12\.50/);
});

test('real API client sends CSRF on PATCH and leaves GET unchanged', async () => {
  const f = fixture();
  const read = f.api.api('/api/purchases/1');
  const get = f.requests.shift();
  assert.equal(get.options.headers['X-CSRFToken'], undefined);
  get.reply({id:1});
  await read;
  const write = f.api.api('/api/purchases/1', 'PATCH', {notes:'Legítimo'});
  const patch = f.requests.shift();
  assert.ok(Object.hasOwn(patch.options.headers, 'X-CSRFToken'));
  assert.equal(patch.options.method, 'PATCH');
  patch.reply({id:1});
  await write;
});
