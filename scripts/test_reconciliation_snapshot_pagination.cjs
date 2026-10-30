/* Complete UI script with a synthetic DOM/API: no server, browser or database. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(id, dataset = {}) {
    this.id = id;
    this.dataset = dataset;
    this.value = 'all';
    this.hidden = false;
    this.disabled = false;
    this.textContent = '';
    this.innerHTML = '';
    this.listeners = {};
    this.elements = {};
  }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  closest() { return this; }
  hasAttribute(name) { return name === 'data-close' && 'close' in this.dataset; }
  focus() {}
  querySelector() { return null; }
}

function cut(id, count) {
  return {
    id, execution_type: 'simulation', source: 'demo_sim:0800:synthetic',
    status: 'waiting_source', snapshot_status: 'captured', product_count: count,
    local_snapshot_at: '2031-01-15T12:00:00', reference_slot: '08:00',
    items: Array.from({length: count}, (_, i) => ({
      product_id: i + 1, product_code: `SYN-${id}-${i + 1}`, quantity: i * 3
    }))
  };
}

function harness() {
  const elements = new Map();
  const node = id => {
    if (!elements.has(id)) elements.set(id, new Element(id));
    return elements.get(id);
  };
  node('rc-context').textContent = JSON.stringify({available: false, canCreate: false, canSimulate: false});
  node('rc-resolution').elements.action = new Element('action');
  node('rc-line-size').value = '50';
  const fixtures = new Map([[1, cut(1, 52)], [2, cut(2, 7)], [3, cut(3, 0)],
    [4, cut(4, 101)], [5, cut(5, 5)], [6, cut(6, 10)]]);
  fixtures.get(1).items[0].product_code = '<img src=x onerror=alert(1)>';
  const original = JSON.stringify([...fixtures]);
  const requests = [], listeners = {}, pending = new Map();
  let failPage = false;
  const sandbox = {
    document: {getElementById: node, querySelectorAll: () => [],
      addEventListener: (type, listener) => { listeners[type] = listener; }},
    URL, URLSearchParams, FormData,
    location: {href: 'http://synthetic.invalid/reconciliation'},
    window: {history: {replaceState() {}}},
    fetch: async (url, options) => {
      assert.equal(options.method, 'GET', 'Snapshot navigation must never write');
      requests.push(url);
      const parsed = new URL(url, 'http://synthetic.invalid');
      const id = Number(parsed.pathname.split('/').at(-1));
      const number = Number(parsed.searchParams.get('snapshot_page'));
      if (failPage && number === 2) return {ok: false, json: async () => ({error: 'Fallo sintético'})};
      if (pending.has(id)) await pending.get(id).promise;
      const fixture = fixtures.get(id);
      const {items, ...metadata} = fixture;
      const data = {...metadata, snapshot: {
        items: items.slice((number - 1) * 50, number * 50),
        total: items.length, pages: Math.max(1, Math.ceil(items.length / 50)),
        page: number, per_page: 50
      }};
      return {ok: true, json: async () => structuredClone(data)};
    }
  };
  vm.runInNewContext(fs.readFileSync('app/static/js/ix_reconciliation.js', 'utf8'), sandbox);
  const click = async element => {
    if (element.disabled) return;
    await listeners.click({target: element});
  };
  return {
    node, fixtures, requests,
    open: id => click(new Element('', {cut: String(id)})),
    next: () => click(node('rc-snapshot-next')),
    prev: () => click(node('rc-snapshot-prev')),
    rows: () => [...node('rc-snapshot-lines').innerHTML.matchAll(/<tr><td>(\d+)<\/td>/g)].map(match => Number(match[1])),
    unchanged: () => assert.equal(JSON.stringify([...fixtures]), original),
    fail: value => { failPage = value; },
    defer: id => {
      let resolve;
      const promise = new Promise(done => { resolve = done; });
      pending.set(id, {promise});
      return () => { pending.delete(id); resolve(); };
    }
  };
}

function expectPage(ui, number, pages, start, end, total) {
  assert.equal(ui.node('rc-snapshot-pagination').textContent, `Página ${number} de ${pages}`);
  assert.equal(ui.node('rc-snapshot-range').textContent, `Mostrando ${start}–${end} de ${total} productos`);
  assert.equal(ui.node('rc-snapshot-prev').disabled, number === 1);
  assert.equal(ui.node('rc-snapshot-next').disabled, number === pages);
  assert.ok(ui.rows().length <= 5);
}

async function main() {
  const ui = harness();
  await ui.open(1);
  assert.equal(ui.requests.length, 2, '52 rows use the existing two bounded API pages');
  expectPage(ui, 1, 11, 1, 5, 52);
  assert.deepEqual(ui.rows(), [1, 2, 3, 4, 5]);
  assert.ok(!ui.node('rc-snapshot-lines').innerHTML.includes('<img'));
  assert.ok(ui.node('rc-actions').innerHTML.includes('/cuts/1/csv'));
  const actions = ui.node('rc-actions').innerHTML;
  await ui.prev();
  expectPage(ui, 1, 11, 1, 5, 52);
  const visited = [...ui.rows()];
  for (let number = 2; number <= 11; number++) {
    await ui.next();
    expectPage(ui, number, 11, (number - 1) * 5 + 1, Math.min(number * 5, 52), 52);
    visited.push(...ui.rows());
    assert.equal(ui.requests.length, 2, 'No requests while changing visual pages');
  }
  assert.deepEqual(visited, Array.from({length: 52}, (_, i) => i + 1));
  assert.deepEqual(ui.rows(), [51, 52]);
  await ui.next();
  expectPage(ui, 11, 11, 51, 52, 52);
  assert.equal(ui.node('rc-actions').innerHTML, actions, 'CSV and reconciliation actions remain unchanged');
  for (let number = 10; number >= 1; number--) {
    await ui.prev();
    expectPage(ui, number, 11, (number - 1) * 5 + 1, number * 5, 52);
  }
  assert.equal(ui.requests.length, 2);
  await ui.next();
  await ui.open(2);
  expectPage(ui, 1, 2, 1, 5, 7);
  await ui.next();
  expectPage(ui, 2, 2, 6, 7, 7);
  assert.ok(ui.node('rc-snapshot-lines').innerHTML.includes('SYN-2-6'));
  await ui.open(1);
  expectPage(ui, 1, 11, 1, 5, 52);
  await ui.open(3);
  expectPage(ui, 1, 1, 0, 0, 0);
  assert.ok(ui.node('rc-snapshot-lines').innerHTML.includes('No hay fotografía disponible.'));
  for (const [id, total] of [[5, 5], [6, 10]]) {
    await ui.open(id);
    expectPage(ui, 1, total / 5, 1, 5, total);
    if (total > 5) {
      await ui.next();
      expectPage(ui, 2, 2, 6, 10, 10);
    }
  }
  const previousRequests = ui.requests.length;
  await ui.open(4);
  assert.equal(ui.requests.length - previousRequests, 3);
  const largeVisited = [...ui.rows()];
  for (let number = 2; number <= 21; number++) {
    await ui.next();
    largeVisited.push(...ui.rows());
  }
  expectPage(ui, 21, 21, 101, 101, 101);
  assert.deepEqual(largeVisited, Array.from({length: 101}, (_, i) => i + 1));
  assert.equal(ui.requests.length - previousRequests, 3);
  ui.fail(true);
  await ui.open(1);
  assert.equal(ui.node('rc-message').textContent, 'Fallo sintético');
  expectPage(ui, 21, 21, 101, 101, 101);
  ui.fail(false);
  await ui.open(1);
  expectPage(ui, 1, 11, 1, 5, 52);
  const release = ui.defer(4);
  const staleRequest = ui.open(4);
  await ui.open(2);
  release();
  await staleRequest;
  expectPage(ui, 1, 2, 1, 5, 7);
  ui.unchanged();
  console.log('PASS: all 52 and 101 rows, first/intermediate/last pages, disabled boundaries, no navigation requests, cut reset, empty/exact-size states, safe escaping, error/retry, stale responses, unchanged source rows and CSV actions.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
