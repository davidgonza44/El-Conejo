/* Product amounts and supplier detail regression; no browser, API or database. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('app/static/js/ix_products_live.js', 'utf8');
const offset = name => {
    const normal = code.indexOf('    function ' + name);
    return normal >= 0 ? normal : code.indexOf('    async function ' + name);
};
const extract = (start, end) => {
    const from = offset(start), to = offset(end);
    assert.ok(from >= 0 && to > from, 'Production function boundaries must exist');
    return code.slice(from, to);
};
class Element {
    constructor(tag, text, className) { this.tag = tag; this.textContent = text; this.className = className; this.children = []; }
    append(...items) { this.children.push(...items); }
}
const context = { canViewCost: true, node: (tag, text, className) => new Element(tag, text, className) };
vm.createContext(context);
vm.runInContext(extract('money(', 'node(') + extract('supplierCard(', 'renderSupplierRows('), context);
for (const [value, expected] of [[0,'0.00'],[0.77,'0.77'],[0.55,'0.55'],[1234.5,'1,234.50'],[undefined,'—'],[null,'—'],['','—']]) {
    assert.equal(context.money(value, 'USD'), expected === '—' ? '—' : '$ ' + expected);
    assert.equal(context.money(value, 'VES'), expected === '—' ? '—' : 'Bs. ' + expected);
}
assert.equal(context.money('1.50'), 'Moneda no registrada · 1.50');
assert.match(context.formatWhen('2026-10-06T16:02:00'), /12:02/, 'Naive stored UTC becomes Caracas time');
assert.equal(context.formatWhen('2026-10-06T16:02:00'), context.formatWhen('2026-10-06T16:02:00Z'));
assert.match(context.formatWhen('2020-12-31T02:00:00Z'), /30 dic. 2020/, 'Day and year use Caracas timezone');
const supplier = { supplier_name:'Proveedor de prueba', supplier_product_code:'COD-1', purchase_price:0.55, purchase_price_currency:'USD', lead_time_days:0, minimum_order_quantity:3, is_preferred:true, operational_preferred:true };
const card = context.supplierCard(supplier);
assert.equal(card.tag, 'article');
assert.equal(card.children[0].children[0].textContent, supplier.supplier_name);
assert.equal(card.children[0].children[1].textContent, 'Preferido');
const fields = card.children[1].children;
assert.deepEqual(fields.map(x => x.children[1].textContent), ['COD-1','$ 0.55','0 días','3']);
assert.ok(fields.every(x => x.children[0].tag === 'dt' && x.children[1].tag === 'dd'));
context.canViewCost = false;
const restricted = context.supplierCard(supplier);
assert.equal(restricted.children[1].children.length, 3);
assert.ok(!restricted.children[1].children.some(x => x.children[0].textContent.includes('Costo')));
context.canViewCost = true;
const incomplete = context.supplierCard({supplier_name:'Sin condiciones'});
assert.deepEqual(incomplete.children[1].children.map(x => x.children[1].textContent), ['No registrado','No registrada','No registrada']);
const inactive = context.supplierCard({...supplier, operational_preferred:false, supplier_active:false});
assert.equal(inactive.children[0].children[1].textContent, 'Preferencia inactiva');
assert.equal(inactive.children[2].textContent, 'Proveedor inactivo');
console.log('PASS: unchanged amounts, missing-price state, no assumed currency, UTC/Caracas dates, individual supplier cards, zero-day delivery, preferred/inactive states and cost permissions.');

const first = {disabled:false,tabIndex:0,getClientRects:()=>[{}],focus(){context.document.activeElement=this;}};
const last = {...first,focus(){context.document.activeElement=this;}};
const hidden = {...first,getClientRects:()=>[]};
context.document = {activeElement:first};
context.els = {dialog:{dataset:{view:'detail'},querySelectorAll:()=>[first,hidden,last]}};
vm.runInContext(extract('trapLayerFocus(', 'bindLayerChrome('), context);
let prevented = 0;
context.trapLayerFocus({key:'Tab',shiftKey:true,preventDefault(){prevented++;}});
assert.equal(context.document.activeElement,last);
context.trapLayerFocus({key:'Tab',shiftKey:false,preventDefault(){prevented++;}});
assert.equal(context.document.activeElement,first);
assert.equal(prevented,2);
context.trapLayerFocus({key:'Escape',shiftKey:false,preventDefault(){throw new Error('Escape intercepted');}});
console.log('PASS: Tab/Shift+Tab remain in detail/edit; hidden controls skipped; Escape preserved.');

Element.prototype.setAttribute = function(name,value) { this[name] = value; };
Element.prototype.addEventListener = function(name,fn) { (this.handlers ||= {})[name] = fn; };
Element.prototype.remove = function() { this.removed = true; };
vm.runInContext(extract('thumb(', 'hasCatalogFilters('), context);
const emptyThumb = context.thumb(null, 'Producto');
assert.equal(emptyThumb.children.length, 1);
assert.equal(emptyThumb.children[0]['aria-label'], 'Sin imagen del producto');
const existingThumb = context.thumb('/static/product-test.png', 'Producto');
const placeholder = existingThumb.children[0], img = existingThumb.children[1];
assert.equal(img.src, '/static/product-test.png');
img.handlers.load();
assert.equal(placeholder.hidden, true);
img.handlers.error();
assert.equal(img.removed, true);
assert.equal(placeholder.hidden, false);
console.log('PASS: absent image, original image URL, successful load and broken-image fallback.');

context.KPI_CARDS = {total_products:'total',low_stock:'low',out_of_stock:'out',active_categories:'cats'};
const historyNote = {}, historyInfo = {setAttribute(name,value){this[name]=value;}};
context.document = {getElementById:id=>id==='ix-history-info'?historyInfo:historyNote};
const rendered = [];
context.renderKpi = (key,kpi,sub)=>rendered.push({key,kpi,sub});
context.shortDate = value=>value;
context.rangeLabel = (start,end)=>start+' - '+end;
vm.runInContext(extract('renderHistory(', 'loadHistory('), context);
const data = {today:'2026-10-08',period:{end:'2026-10-08',available:true},comparison:{available:false,type:'previous'},history:{available_from:null},kpis:{total_products:{value:49,delta:null},low_stock:{value:8,delta:null},out_of_stock:{value:6,delta:null},active_categories:{value:10,delta:null}}};
context.renderHistory(data);
assert.ok(rendered.every(x=>x.sub==='Estado actual · sin filtros'));
assert.match(historyNote.textContent,/Comparación histórica no disponible/);
assert.equal(rendered[0].kpi.value,49);
rendered.length=0;
context.renderHistory({...data,period:{end:'2026-09-30',available:false}});
assert.ok(rendered.every(x=>x.sub==='Sin cierre para esta fecha'));
assert.match(historyNote.textContent,/El catálogo muestra datos actuales/);
console.log('PASS: live KPI counts stay independent of missing historical comparisons; missing past close stays explicit.');
