// Executes the shipped script against native-form test doubles; no browser or DB.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/ix_replenishment_requests.js'), 'utf8');

function fixture(values = {}) {
  const field = (value = '') => ({
    value, listeners: {}, validity: '', attributes: {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    setCustomValidity(message) { this.validity = message; },
    setAttribute(name, value) { this.attributes[name] = value; },
    reportValidity() { return !this.validity; },
  });
  const fields = Object.fromEntries(['search', 'status', 'start', 'end', 'per_page'].map(
    (name) => [name, field(values[name] || (name === 'per_page' ? '8' : ''))]));
  const message = { textContent: '', hidden: true };
  const navigations = [];
  const form = {
    elements: fields, listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    querySelectorAll() { return [fields.status, fields.start, fields.end]; },
    requestSubmit() {
      let prevented = false;
      this.listeners.submit({ preventDefault() { prevented = true; } });
      if (!prevented && !fields.end.validity) {
        navigations.push(new URLSearchParams(Object.entries(fields).map(([key, input]) => [key, input.value])));
      }
    },
  };
  vm.runInNewContext(source, { document: { getElementById(id) {
    return { 'sr-filters': form, 'sr-filter-error': message }[id] || null;
  } } });
  return { fields, form, message, navigations };
}

test('selecting processing submits the internal state and retains combined filters', () => {
  const view = fixture({ search: 'Cemento', start: '2026-01-10', end: '2026-01-12' });
  view.fields.status.value = 'processing';
  view.fields.status.listeners.change();
  assert.equal(view.navigations.length, 1);
  assert.deepEqual(Object.fromEntries(view.navigations[0]), {
    search: 'Cemento', status: 'processing', start: '2026-01-10', end: '2026-01-12', per_page: '8',
  });
  assert.equal(view.navigations[0].has('page'), false);
});

test('each state, including Todos, sends its exact internal value', () => {
  const view = fixture();
  for (const status of ['', 'open', 'processing', 'fulfilled', 'cancelled']) {
    view.fields.status.value = status;
    view.fields.status.listeners.change();
    assert.equal(view.navigations.at(-1).get('status'), status);
  }
  assert.equal(view.navigations.length, 5);
});

test('invalid dates block submission, announce validation and recover after correction', () => {
  const view = fixture({ start: '2026-01-12', end: '2026-01-10' });
  view.form.requestSubmit();
  assert.equal(view.navigations.length, 0);
  assert.equal(view.message.hidden, false);
  assert.match(view.message.textContent, /inicial no puede superar/);
  assert.equal(view.fields.end.attributes['aria-invalid'], 'true');
  view.fields.end.value = '2026-01-12';
  view.fields.end.listeners.input();
  view.fields.end.listeners.change();
  assert.equal(view.navigations.length, 1);
  assert.equal(view.fields.end.validity, '');
  assert.equal(view.message.hidden, true);
});

test('date changes submit, and explicit submit handles search with current state', () => {
  const view = fixture({ status: 'processing' });
  view.fields.start.value = '2026-01-10';
  view.fields.start.listeners.change();
  view.fields.end.value = '2026-01-10';
  view.fields.end.listeners.change();
  view.fields.search.value = 'SR-000003';
  view.form.requestSubmit();
  assert.equal(view.navigations.length, 3);
  assert.equal(view.navigations.at(-1).get('search'), 'SR-000003');
  assert.equal(view.navigations.at(-1).get('status'), 'processing');
});
