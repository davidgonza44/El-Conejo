// Dependency-free UI behavior checks: fake clock/fetch, no network or database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync('app/static/js/predictive.js', 'utf8');
const statusUrl = '/api/predictions/status/synthetic-opaque-token';

function element(attributes = {}, children = {}) {
    return {
        attributes, textContent: '', innerHTML: '', dataset: {}, events: {}, scrollTop: 0,
        classList: { toggle() {} },
        getAttribute(name) { return this.attributes[name]; },
        setAttribute(name, value) { this.attributes[name] = value; },
        querySelector(selector) { return children[selector] || null; },
        querySelectorAll() { return []; },
        closest() { return null; },
        addEventListener(name, callback) { this.events[name] = callback; },
        replaceChildren() { this.innerHTML = ''; },
        focus() {},
    };
}

function fixture({ initial = 'pending', replies = [], reference = false } = {}) {
    const title = element(), message = element();
    const emptyTitle = element(), emptyMessage = element(), topEmpty = element();
    const pendingKpi = element(), observedKpi = element(), donutLabel = element();
    pendingKpi.textContent = 'Pronóstico pendiente';
    observedKpi.textContent = 'Supply pendiente';
    donutLabel.textContent = 'Pendiente';
    const banner = element({ 'data-predictive-status': initial }, { h2: title, p: message });
    const panel = element({ 'data-predictive-status-url': statusUrl, 'data-predictive-state': initial }, {
        '.px-empty strong': emptyTitle, '.px-empty p': emptyMessage, '.px-top-empty': topEmpty,
        '.px-donut-label': donutLabel,
    });
    panel.querySelectorAll = () => [pendingKpi, observedKpi];
    const events = {}, timers = new Map(), requests = [];
    let now = 0, timerId = 0, reloads = 0;
    const location = {
        href: 'http://127.0.0.1:5001/predictive/demand?method=arima&horizon=90&category=2&page=3',
        reload() { reloads += 1; },
    };
    function setTimeout(callback, delay) {
        const id = ++timerId;
        timers.set(id, { callback, at: now + delay });
        return id;
    }
    function fetch(url, options) {
        requests.push({ url, options, at: now });
        const reply = replies.shift() || { status: 'pending', retry_after: 2 };
        if (typeof reply === 'function') return reply(options.signal);
        if (reply instanceof Error) return Promise.reject(reply);
        return Promise.resolve({
            ok: !reply.http || reply.http === 200, status: reply.http || 200,
            redirected: Boolean(reply.redirected), json: async () => reply,
        });
    }
    const document = {
        querySelector: selector => selector === '[data-predictive-status-url]' ?
            (reference ? null : panel) : selector === '[data-predictive-status]' ? banner : null,
        querySelectorAll: () => [],
        addEventListener(name, callback) { events[name] = callback; },
    };
    const window = {
        location, setTimeout, clearTimeout: id => timers.delete(id),
        addEventListener(name, callback) { events[name] = callback; },
    };
    const context = vm.createContext({ document, window, fetch, AbortController, URL, location });
    vm.runInContext(source, context);
    async function flush() {
        for (let index = 0; index < 12; index += 1) await Promise.resolve();
    }
    async function advance(duration) {
        const target = now + duration;
        await flush();
        for (;;) {
            const due = [...timers].filter(([, value]) => value.at <= target)
                .sort((a, b) => a[1].at - b[1].at)[0];
            if (!due) break;
            now = due[1].at;
            timers.delete(due[0]);
            due[1].callback();
            await flush();
        }
        now = target;
        await flush();
    }
    return {
        panel, banner, title, message, emptyTitle, emptyMessage, topEmpty, pendingKpi, observedKpi, donutLabel,
        events, timers, requests, location, advance, flush, context, document, window,
        get reloads() { return reloads; },
    };
}

function untilAborted(signal) {
    return new Promise((_resolve, reject) => signal.addEventListener('abort', () => {
        reject(new Error('Aborted request'));
    }, { once: true }));
}

test('pending -> running -> ready reloads automatically once and preserves filters', async () => {
    const ui = fixture({ replies: [{ status: 'running', retry_after: 2 }, { status: 'ready' }] });
    const originalUrl = ui.location.href;
    await ui.advance(2000);
    assert.equal(ui.banner.getAttribute('data-predictive-status'), 'running');
    assert.equal(ui.title.textContent, 'Calculando pronóstico');
    assert.match(ui.emptyMessage.textContent, /automáticamente/);
    await ui.advance(2000);
    assert.equal(ui.reloads, 1);
    assert.equal(ui.location.href, originalUrl);
    assert.equal(ui.timers.size, 0);
    await ui.advance(60_000);
    assert.equal(ui.requests.length, 2);
    for (const { url, options } of ui.requests) {
        assert.equal(url, statusUrl);
        assert.equal(options.cache, 'no-store');
        assert.equal(options.credentials, 'same-origin');
    }
});

test('initial ready/error and reference pages never start polling', async () => {
    for (const options of [{ initial: 'ready' }, { initial: 'error' }, { reference: true }]) {
        const ui = fixture(options);
        await ui.advance(60_000);
        assert.equal(ui.requests.length, 0);
        assert.equal(ui.timers.size, 0);
    }
});

test('error/unavailable/expired are terminal and never reload an unavailable forecast', async () => {
    for (const status of ['error', 'unavailable', 'expired']) {
        const ui = fixture({ replies: [{ status, message: 'No disponible en esta prueba.' }] });
        await ui.advance(60_000);
        assert.equal(ui.requests.length, 1);
        assert.equal(ui.reloads, 0);
        assert.equal(ui.banner.getAttribute('data-predictive-status'), status);
        assert.equal(ui.title.textContent, 'Pronóstico no disponible');
        assert.equal(ui.emptyTitle.textContent, 'Pronóstico no disponible');
        assert.equal(ui.topEmpty.textContent, 'Pronóstico no disponible.');
        assert.equal(ui.pendingKpi.textContent, 'Pronóstico no disponible');
        assert.equal(ui.observedKpi.textContent, 'Supply pendiente');
        assert.equal(ui.donutLabel.textContent, 'No disponible');
        assert.equal(ui.timers.size, 0);
    }
});

test('busy waits, retry_after is bounded, then ready reloads', async () => {
    const ui = fixture({ initial: 'busy', replies: [
        { status: 'pending', retry_after: -1 }, { status: 'running', retry_after: 10000 }, { status: 'ready' },
    ] });
    await ui.advance(13_000);
    assert.deepEqual(ui.requests.map(request => request.at), [2000, 3000, 13000]);
    assert.equal(ui.reloads, 1);
});

test('three network, HTTP or invalid-payload errors stop with a visible unavailable state', async () => {
    for (const failure of [new Error('Network failed'), { http: 503 }, { status: 'unknown' }, { status: null }]) {
        const ui = fixture({ replies: [failure, failure, failure] });
        await ui.advance(60_000);
        assert.equal(ui.requests.length, 3);
        assert.equal(ui.panel.getAttribute('data-predictive-state'), 'unavailable');
        assert.match(ui.message.textContent, /conexión/);
        assert.equal(ui.reloads, 0);
        assert.equal(ui.timers.size, 0);
    }
});

test('a successful status resets the consecutive failure count', async () => {
    const fail = new Error('Network failed');
    const ui = fixture({ replies: [fail, fail, { status: 'running' }, fail, fail, { status: 'ready' }] });
    await ui.advance(60_000);
    assert.equal(ui.requests.length, 6);
    assert.equal(ui.reloads, 1);
});

test('hung requests time out at 15 seconds without overlapping fetches', async () => {
    const ui = fixture({ replies: [untilAborted, untilAborted, untilAborted] });
    await ui.advance(16_999);
    assert.equal(ui.requests.length, 1);
    assert.equal(ui.requests[0].options.signal.aborted, false);
    await ui.advance(1);
    assert.equal(ui.requests[0].options.signal.aborted, true);
    await ui.advance(60_000);
    assert.equal(ui.requests.length, 3);
    assert.deepEqual(ui.requests.map(request => request.at), [2000, 22000, 42000]);
    assert.equal(ui.panel.getAttribute('data-predictive-state'), 'unavailable');
    assert.equal(ui.timers.size, 0);
});

test('continuous pending reaches the finite 30-minute deadline', async () => {
    const ui = fixture();
    await ui.advance(30 * 60 * 1000);
    assert.equal(ui.panel.getAttribute('data-predictive-state'), 'unavailable');
    assert.match(ui.message.textContent, /tiempo máximo/);
    assert.equal(ui.timers.size, 0);
    const count = ui.requests.length;
    await ui.advance(60_000);
    assert.equal(ui.requests.length, count);
    assert.equal(ui.reloads, 0);
});

test('navigation, filter changes and submits abort polling and ignore late ready responses', async () => {
    for (const event of ['submit', 'change', 'pagehide']) {
        let resolve;
        const ui = fixture({ replies: [() => new Promise(done => { resolve = done; })] });
        await ui.advance(2000);
        ui.events[event]({ target: { closest: () => ({}) } });
        assert.equal(ui.requests[0].options.signal.aborted, true);
        resolve({ ok: true, status: 200, json: async () => ({ status: 'ready' }) });
        await ui.advance(60_000);
        assert.equal(ui.reloads, 0);
        assert.equal(ui.requests.length, 1);
        assert.equal(ui.timers.size, 0);
    }
});

test('changed URL discards a stale result even without a navigation event', async () => {
    let resolve;
    const ui = fixture({ replies: [() => new Promise(done => { resolve = done; })] });
    await ui.advance(2000);
    ui.location.href += '&method=mean';
    resolve({ ok: true, status: 200, json: async () => ({ status: 'ready' }) });
    await ui.advance(60_000);
    assert.equal(ui.reloads, 0);
    assert.equal(ui.requests.length, 1);
    assert.equal(ui.timers.size, 0);
});

test('session expiry, redirects and forbidden responses stop without reload', async () => {
    for (const reply of [{ http: 401 }, { http: 403 }, { redirected: true }]) {
        const ui = fixture({ replies: [reply] });
        await ui.advance(60_000);
        assert.equal(ui.requests.length, 1);
        assert.equal(ui.reloads, 0);
        assert.match(ui.message.textContent, /sesión/);
        assert.equal(ui.timers.size, 0);
    }
});

function detailData(status, reference = false) {
    return {
        reference, computation_status: status, computation_status_url: statusUrl,
        product: { id: 1, name: 'Producto sintético', sku: 'SYN-1', category: 'Prueba', suppliers: [], stock: 10, minimum: 1, unit: 'uds.' },
        risk: { level: 'low', cover: null }, links: {}, history: { values: [], total: 0 },
        periods: [7, 30, 90].map(days => ({ days, total: status === 'ready' ? 15 : null, path: [] })),
        eligibility: 'Sin gráfico disponible.', recommendation: null,
    };
}

function detailFixture(replies, reference = false) {
    const ui = fixture({ initial: 'ready', replies });
    const ids = Object.fromEntries(['pd-body', 'pd-footer', 'pd-product', 'pd-title', 'pd-buy'].map(id => [id, element()]));
    const close = element(), tab = element(), button = element();
    tab.dataset.pdTab = 'summary';
    button.dataset.productDetail = '1';
    const dialog = element({}, { '[data-pd-close]': close });
    dialog.dataset = { page: 'critical', detailUrl: '/api/predictions/products/0/detail', reference: reference ? '1' : '0' };
    dialog.querySelectorAll = () => [tab];
    dialog.showModal = () => { dialog.open = true; };
    dialog.close = () => { dialog.open = false; };
    ids['pd-drawer'] = dialog;
    ui.document.getElementById = id => ids[id];
    ui.document.querySelectorAll = () => [button];
    ui.document.body = { style: { overflow: '' } };
    ui.location.origin = 'http://127.0.0.1:5001';
    vm.runInContext(fs.readFileSync('app/static/js/predictive_detail.js', 'utf8'), ui.context);
    button.events.click();
    return { ...ui, ids, dialog, close, button };
}

test('detail polls token only then refreshes one DTO automatically on ready', async () => {
    const ui = detailFixture([detailData('pending'), { status: 'running' }, { status: 'ready' }, detailData('ready')]);
    await ui.flush();
    assert.match(ui.ids['pd-body'].innerHTML, /automáticamente/);
    await ui.advance(4000);
    assert.equal(ui.requests.length, 4);
    assert.match(String(ui.requests[0].url), /products\/1\/detail$/);
    assert.equal(ui.requests[1].url, statusUrl);
    assert.equal(ui.requests[2].url, statusUrl);
    assert.equal(String(ui.requests[3].url), String(ui.requests[0].url));
    assert.match(ui.ids['pd-body'].innerHTML, /15 uds\./);
    assert.doesNotMatch(ui.ids['pd-body'].innerHTML, /Calculando pronóstico/);
    assert.equal(ui.timers.size, 0);
    assert.equal(ui.reloads, 0);
});

test('detail terminal failure leaves observations and stops without fetching another DTO', async () => {
    const ui = detailFixture([detailData('pending'), { status: 'error', message: 'Pronóstico no disponible.' }]);
    await ui.advance(60_000);
    assert.equal(ui.requests.length, 2);
    assert.match(ui.ids['pd-body'].innerHTML, /Pronóstico no disponible/);
    assert.match(ui.ids['pd-product'].innerHTML, /Producto sintético/);
    assert.equal(ui.timers.size, 0);
});

test('closing a detail aborts its polling and discards a late ready response', async () => {
    let resolve;
    const ui = detailFixture([detailData('pending'), () => new Promise(done => { resolve = done; })]);
    await ui.advance(2000);
    ui.close.events.click();
    assert.equal(ui.requests[1].options.signal.aborted, true);
    resolve({ ok: true, status: 200, json: async () => ({ status: 'ready' }) });
    await ui.advance(60_000);
    assert.equal(ui.requests.length, 2);
    assert.equal(ui.dialog.open, false);
    assert.equal(ui.timers.size, 0);
});

test('reference detail never polls and a hung detail request has a finite timeout', async () => {
    const reference = detailFixture([detailData('pending', true)], true);
    await reference.advance(60_000);
    assert.equal(reference.requests.length, 1);
    const hung = detailFixture([untilAborted]);
    await hung.advance(15_000);
    assert.equal(hung.requests.length, 1);
    assert.match(hung.ids['pd-body'].innerHTML, /No se pudo cargar el producto/);
    assert.equal(hung.timers.size, 0);
});
