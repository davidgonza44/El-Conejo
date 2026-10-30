// Dependency-free local state check; no server, database, or network access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/ix_product_history.js', 'utf8');
function element(dataset = {}) {
    return {
        dataset, value: '', textContent: '', hidden: true, attrs: {}, handlers: {},
        setAttribute(key, value) { this.attrs[key] = value; },
        removeAttribute(key) { delete this.attrs[key]; },
        addEventListener(key, handler) { this.handlers[key] = handler; },
        dispatchEvent(event) { this.lastEvent = event; },
        setCustomValidity(message) { this.validationMessage = message; },
        reportValidity() { return true; }, focus() {},
        fire(key, extra = {}) { this.handlers[key]({ preventDefault() {}, ...extra }); }
    };
}
const keys = ['today', '7days', '30days', 'month', 'previous-month', 'custom'];
function setup(today) {
    const bar = element({ referenceDate: today });
    const presets = keys.map(historyPreset => element({ historyPreset }));
    bar.querySelectorAll = () => presets;
    const nodes = Object.fromEntries(['custom', 'start', 'end', 'error', 'range', 'range-text', 'cancel', 'compare'].map(key => [key, element()]));
    nodes.compare.value = 'previous';
    nodes.error.textContent = 'Invalid range';
    vm.runInNewContext(source, {
        CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
        document: { getElementById: id => id === 'ix-product-history' ? bar : nodes[id.replace('ix-history-', '')] }
    });
    return { bar, presets, nodes };
}
const { bar, presets, nodes } = setup('2026-09-30');
const expected = [
    ['2026-09-30', '2026-09-30'], ['2026-09-24', '2026-09-30'],
    ['2026-09-01', '2026-09-30'], ['2026-09-01', '2026-09-30'],
    ['2026-08-01', '2026-08-31']
];
expected.forEach((range, index) => {
    presets[index].fire('click');
    assert.deepEqual([bar.dataset.start, bar.dataset.end], range);
    assert.equal(presets[index].attrs['aria-pressed'], 'true');
    assert.equal(bar.lastEvent.type, 'ix-product-history-change');
    assert.deepEqual([bar.lastEvent.detail.start, bar.lastEvent.detail.end], range);
});
presets[5].fire('click');
assert.equal(nodes.custom.hidden, false);
nodes.start.value = '2026-08-15'; nodes.end.value = '2026-08-01';
nodes.end.fire('input'); nodes.custom.fire('submit');
assert.equal(nodes.error.hidden, false);
assert.equal(bar.dataset.start, '2026-08-01');
nodes.end.value = '2026-08-28'; nodes.end.fire('input'); nodes.custom.fire('submit');
assert.equal(bar.dataset.preset, 'custom');
assert.equal(bar.dataset.start, '2026-08-15');
assert.equal(bar.dataset.end, '2026-08-28');
assert.equal(nodes.custom.hidden, true);
assert.equal(nodes['range-text'].textContent, '15 ago. 2026 — 28 ago. 2026');
for (const option of ['year', 'none', 'previous']) {
    nodes.compare.value = option; nodes.compare.fire('change');
    assert.equal(bar.dataset.comparison, option);
}
for (const [today, previousStart, previousEnd] of [
    ['2024-03-01', '2024-02-01', '2024-02-29'],
    ['2026-01-01', '2025-12-01', '2025-12-31']
]) {
    const state = setup(today); state.presets[4].fire('click');
    assert.deepEqual([state.bar.dataset.start, state.bar.dataset.end], [previousStart, previousEnd]);
}
console.log('PASS: presets, custom validation, comparison state, leap-year/year rollover; no network globals provided.');
