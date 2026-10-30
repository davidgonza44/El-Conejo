// Execute shipped SR actions with deferred HTTP responses and minimal DOM doubles.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const source = fs.readFileSync('app/static/js/ix_replenishment_requests.js', 'utf8');

function fixture() {
  const start = {textContent: 'Iniciar procesamiento', disabled: false, events: {},
    closest() { return null; }, addEventListener(name, fn) { this.events[name] = fn; }};
  const disabled = {...start, disabled: true};
  const error = {textContent: '', hidden: true};
  const root = {dataset: {id: '123'}, attributes: {},
    querySelectorAll() { return [start, disabled]; },
    setAttribute(key, value) { this.attributes[key] = value; },
    removeAttribute(key) { delete this.attributes[key]; }};
  const requests = [], listeners = {};
  let reloads = 0;
  const window = {location: {reload() { reloads += 1; }},
    addEventListener(name, fn) { listeners[name] = fn; }};
  vm.runInNewContext(source, {window, document: {getElementById(id) {
    return {'sr-actions': root, 'sr-error': error, 'sr-start': start}[id] || null;
  }}, fetch(url, options) { return new Promise(resolve => requests.push({url, options, resolve})); }});
  return {start, disabled, root, error, requests, listeners, reloads: () => reloads};
}

test('double click sends one request and preserves already disabled controls', async () => {
  const view = fixture();
  const first = view.start.events.click();
  await view.start.events.click();
  assert.equal(view.requests.length, 1);
  assert.equal(view.start.disabled, true);
  assert.equal(view.root.attributes['aria-busy'], 'true');
  assert.equal(view.start.textContent, 'Procesando…');
  view.requests[0].resolve({ok: true, status: 200, json: async () => ({status: 'processing'})});
  await first;
  assert.equal(view.reloads(), 1);
  assert.equal(view.disabled.disabled, true);
});

test('backend conflict is visible and permits a deliberate retry', async () => {
  const view = fixture();
  const first = view.start.events.click();
  view.requests[0].resolve({ok: false, status: 409, json: async () => ({error: 'La solicitud ya está en proceso.'})});
  await first;
  assert.equal(view.error.textContent, 'La solicitud ya está en proceso.');
  assert.equal(view.error.hidden, false);
  assert.equal(view.start.disabled, false);
  assert.equal(view.start.textContent, 'Iniciar procesamiento');
  assert.equal(view.disabled.disabled, true);
  assert.equal(view.root.attributes['aria-busy'], undefined);
  const retry = view.start.events.click();
  assert.equal(view.requests.length, 2);
  view.requests[1].resolve({ok: true, status: 200, json: async () => ({})});
  await retry;
  assert.equal(view.reloads(), 1);
});

test('expired session explains login and reload rather than hiding the error', async () => {
  const view = fixture();
  const pending = view.start.events.click();
  view.requests[0].resolve({ok: false, status: 401, json: async () => ({error: 'Unauthorized'})});
  await pending;
  assert.match(view.error.textContent, /sesión expiró.*Inicie sesión/);
  assert.equal(view.start.disabled, false);
  assert.equal(view.reloads(), 0);
});

test('back-forward cache restores reload the stale actionable SR', () => {
  const view = fixture();
  view.listeners.pageshow({persisted: false});
  assert.equal(view.reloads(), 0);
  view.listeners.pageshow({persisted: true});
  assert.equal(view.reloads(), 1);
});
