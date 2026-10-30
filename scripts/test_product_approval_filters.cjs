/* URL y estado vacío: ejecuta las funciones de producción sin servidor ni BD. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const window = {};
vm.runInNewContext(fs.readFileSync('app/static/js/ix_product_filters.js', 'utf8'), { window, URL, URLSearchParams });
const initial = () => ({ search: '', categoryId: '', supplierId: '', status: '', approvalStatus: '',
    priceMin: '', priceMax: '', stockMin: '', stockMax: '', dateMin: '', dateMax: '', sort: 'name-asc', page: 1, perPage: 8 });
for (const approval of ['pending', 'approved', 'rejected']) {
    const state = initial();
    window.productFilters.restore(state, `?approval_status=${approval}&category_id=7&status=inactivo&search=PVC&page=2&per_page=1`);
    assert.equal(state.approvalStatus, approval);
    assert.equal(state.categoryId, '7');
    assert.equal(state.status, 'inactivo');
    assert.equal(state.search, 'PVC');
    assert.equal(state.page, 2);
    const query = window.productFilters.url(state, 'http://localhost:5001/products?context=catalog#tabla');
    const restored = initial();
    window.productFilters.restore(restored, new URL(query, 'http://localhost:5001').search);
    assert.deepEqual(restored, state, 'Recargar conserva aprobación, categoría, estado, búsqueda y página');
    assert.equal(new URL(query, 'http://localhost:5001').searchParams.get('context'), 'catalog');
    assert.ok(query.endsWith('#tabla'));
    state.approvalStatus = 'pending';
    state.page = 1;
    assert.equal(new URL(window.productFilters.url(state, 'http://localhost:5001' + query),
        'http://localhost:5001').searchParams.get('category_id'), '7', 'Cambiar aprobación conserva categoría');
}
const advanced = initial();
window.productFilters.restore(advanced, '?supplier_id=2&price_min=0&price_max=8&stock_min=0&stock_max=10&updated_from=2026-10-01&updated_to=2026-10-07&sort=updated-desc');
const restored = initial();
window.productFilters.restore(restored, new URL(window.productFilters.url(advanced, 'http://localhost:5001/products'), 'http://localhost:5001').search);
assert.deepEqual(restored, advanced);
assert.equal(window.productFilters.url(initial(), 'http://localhost:5001/products?approval_status=pending&category_id=7&status=inactivo&search=PVC&page=2'), '/products', 'Limpiar quita todos los filtros');
const invalid = initial();
window.productFilters.restore(invalid, '?approval_status=unknown&page=-1&per_page=no');
assert.equal(invalid.approvalStatus, '');
assert.equal(invalid.page, 1);
assert.equal(invalid.perPage, 8);

class Element {
    constructor(tag, text) { this.tag = tag; this.textContent = text; this.children = []; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
}
const state = initial();
const els = { body: new Element('tbody') };
const code = fs.readFileSync('app/static/js/ix_products_live.js', 'utf8');
const renderCode = code.slice(code.indexOf('    function hasCatalogFilters()'), code.indexOf('    function renderPager('));
const context = { state, els, canWrite: false, window, node: (tag, text) => new Element(tag, text) };
vm.createContext(context);
vm.runInContext(renderCode, context);
for (const approval of ['pending', 'approved', 'rejected']) {
    state.approvalStatus = approval;
    vm.runInContext('renderRows([], 0)', context);
    const cell = els.body.children[0].children[0];
    assert.equal(cell.textContent, 'No se encontraron productos con estos filtros.');
    assert.equal(cell.children.length, 0, 'Estado vacío filtrado sin invitación a crear');
}
console.log('PASS: URL conserva filtros combinados y avanzados, paginación, limpieza y estado vacío de aprobación.');
