/* Execute the actual live render/refresh functions, without a browser or database. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/ix_movements_live.js', 'utf8');
const node = (tag = 'div', className = '', textContent = '') => ({
    tag, className, textContent, children: [], attributes: {},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    setAttribute(key, value) { this.attributes[key] = value; },
    querySelector() { return this.value; },
});
const kpis = Array.from({length: 4}, () => ({value: node()}));
const legend = Array.from({length: 3}, () => ({value: node()}));
const center = node(), canvas = node(), activity = node();
const chart = {data: {datasets: [{data: []}]}, update() {}};
const context = {
    node, Intl, URLSearchParams, Promise,
    $: selector => ({'.iv-donut .ix-donut-value': center, '.iv-donut canvas': canvas,
        '.iv-mov-activity .iv-activity-list': activity})[selector],
    document: {querySelectorAll: selector => ({'.iv-kpis .ix-kpi': kpis,
        '.iv-kpis .ix-kpi-value': kpis.map(card => card.value), '.iv-type-legend li': legend})[selector],
        createTextNode: text => node('text', '', text)},
    window: {Chart: {getChart: () => chart}},
};
kpis.forEach(card => {card.querySelector = () => card.value;});
legend.forEach(item => {item.querySelector = () => item.value;});
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('    function renderSummary('), source.indexOf('    async function refresh(')), context);
for (const [distribution, total, historyTotal] of [[{entrada: 2, salida: 3, ajuste: 1}, 6, 6],
    [{ajuste: 1}, 1, 6], [{entrada: 2, transferencia: 99}, 2, 101], [{}, 0, 0]]) {
    context.renderSummary({daily: {entrada: 2, salida: 3, ajuste: 1}, distribution, total: historyTotal,
        commercial_day: '2026-09-22', recent: []}, historyTotal);
    assert.equal(kpis[3].value.textContent, new Intl.NumberFormat('es-VE').format(historyTotal));
    assert.equal(kpis[3].title, `Movimientos registrados: ${kpis[3].value.textContent}`);
    assert.equal(center.textContent, total);
    assert.equal(chart.data.datasets[0].data.length, 3);
    assert.equal(chart.data.datasets[0].data.reduce((a, b) => a + b, 0), total);
    assert.ok(canvas.attributes['aria-label'].endsWith(`Total ${total}`));
}

const calls = [], renders = [];
Object.assign(context, {requestId: 0, tbody: node('tbody'), tableMessage() {}, renderPagination() {},
    renderRow: row => row, renderSummary: (_summary, total) => renders.push(total),
    state: {page: 1, per_page: 8, sort: '-created_at', search: '', movement_type: '', user_id: '', date_from: '', date_to: ''},
    api: async path => {
        calls.push(path);
        return {count: 1, total: path.includes('movement_type=ajuste') ? 1 : 6, items: [{}], pages: 1};
    }});
vm.runInContext(source.slice(source.indexOf('    async function refresh('), source.indexOf('    function select(')), context);
(async () => {
    await context.refresh();
    assert.equal(calls.length, 2);
    assert.equal(renders.at(-1), 6);
    calls.length = 0;
    context.state.movement_type = 'ajuste';
    await context.refresh();
    assert.equal(calls.length, 3);
    assert.equal(calls[2], 'movements?page=1&per_page=1');
    assert.equal(renders.at(-1), 6);
    console.log('PASS: real live summary/refresh, total vs filtered count, zero state, three chart categories, unknown legacy type excluded, unfiltered request only when needed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
