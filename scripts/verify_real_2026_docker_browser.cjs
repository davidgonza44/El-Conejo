/* Acceptance against the explicitly isolated Docker instance, never 5000/5001.
 * Input: private .tmp JSON; credentials, product data and response bodies stay private.
 */
'use strict';
const {chromium, expect} = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const TEMP = path.join(ROOT, '.tmp');
const SOURCE_LABEL = 'Histórico real 2026';
const CUTOFF = '2026-09-18';
// Lunes a sábado tras el corte (viernes); el domingo 2026-09-20 no es operativo.
const FUTURE_7 = ['2026-09-19', '2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-25', '2026-09-26'];
const FILES = [
    'productos_2026.csv', 'demanda_limpia_2026.csv', 'movimientos_detalle_2026.csv',
    'existencias_2026_final.csv', 'MANIFIESTO_SHA256.json', 'empirical_2026_build_report.json',
    'empirical_2026_cohort_manifest.csv', 'empirical_2026_operational_calendar.csv',
    'empirical_2026_series_strict.csv', 'empirical_2026_series_sensitivity.csv'
];
const LEGACY_PAGES = ['/inventory', '/predictive/demand', '/historical-imports', '/purchases', '/reports'];
let phase = 'manifest';

function verify(condition) {
    if (!condition) throw new Error('Acceptance check failed.');
}

function privatePath(value) {
    verify(typeof value === 'string' && value.length > 0);
    const resolved = fs.realpathSync(path.resolve(ROOT, value));
    const relative = path.relative(fs.realpathSync(TEMP), resolved);
    verify(relative !== '' && !relative.startsWith('..') && !path.isAbsolute(relative));
    return resolved;
}

function metadata() {
    const file = privatePath(process.argv[2]);
    verify(fs.statSync(file).isFile());
    const input = JSON.parse(fs.readFileSync(file, 'utf8'));
    verify(input.format === 'conejo-real2026-docker-v1');
    verify(typeof input.username === 'string' && input.username.length > 0);
    verify(typeof input.password === 'string' && input.password.length > 0);
    const origin = new URL(input.origin);
    verify(origin.protocol === 'http:' && origin.hostname === '127.0.0.1');
    verify(!['', '5000', '5001'].includes(origin.port));
    verify(!origin.username && !origin.password && origin.pathname === '/' && !origin.search && !origin.hash);
    const source = privatePath(input.source_dir);
    verify(fs.statSync(source).isDirectory());
    const files = FILES.map((name) => privatePath(path.join(source, name)));
    verify(files.every((item) => fs.statSync(item).isFile()));
    verify(files.reduce((size, item) => size + fs.statSync(item).size, 0) <= 64 * 1024 * 1024);
    return {...input, origin: origin.origin, files};
}

function responseFor(page, url, method = 'GET', timeout = 180000) {
    return page.waitForResponse((response) => response.url() === url && response.request().method() === method,
        {timeout});
}

async function login(page, input) {
    phase = 'login-page';
    const entry = await page.goto(input.origin + '/login');
    verify(entry.status() === 200);
    phase = 'login-fields';
    await page.locator('#login-identifier').fill(input.username);
    await page.locator('#login-password').fill(input.password);
    const response = responseFor(page, input.origin + '/api/auth/login', 'POST');
    phase = 'login-submit';
    await page.locator('#login-submit').click();
    phase = 'login-response';
    const result = await response;
    verify(result.status() === 200);
    // The login script immediately navigates; Chromium may discard this body.
    // Authenticated access to the historical page verifies the resulting session.
    phase = 'login-navigation';
    await page.waitForURL((url) => url.origin === input.origin && url.pathname !== '/login');
}

async function upload(page, input, expectedStatus) {
    await page.locator('#rh-files').setInputFiles(input.files);
    const pending = responseFor(page, input.origin + '/api/historical-2026/imports/preview', 'POST', 300000);
    await page.locator('#rh-preview-button').click();
    const response = await pending;
    verify(response.status() === expectedStatus);
    return response.json();
}

async function confirm(page, input, preview) {
    phase = 'preview';
    verify(preview.created === true && preview.batch.status === 'preview');
    await expect(page.locator('#rh-batch-state')).toContainText('Vista previa');
    await expect(page.locator('#rh-catalog')).toBeHidden();
    await expect(page.locator('#rh-confirm')).toBeVisible();
    phase = 'confirmation';
    const pending = responseFor(page,
        `${input.origin}/api/historical-2026/imports/${preview.batch.id}/confirm`, 'POST', 300000);
    await page.locator('#rh-confirm').click();
    const response = await pending;
    verify(response.status() === 200);
    const confirmed = await response.json();
    verify(confirmed.changed === true && confirmed.batch.status === 'confirmed');
    await expect(page.locator('#rh-batch-state')).toContainText('confirmada');
    await expect(page.locator('#rh-products button').first()).toBeVisible();
}

async function eligibleProduct(page, input, batchId) {
    phase = 'product-selection';
    const prefix = `${input.origin}/api/historical-2026/imports/${batchId}/products/`;
    for (let number = 0; number < 8; number++) {
        const buttons = page.locator('#rh-products button');
        for (let index = 0, count = await buttons.count(); index < count; index++) {
            const pending = page.waitForResponse((response) => response.url().startsWith(prefix)
                && /^\d+$/.test(response.url().slice(prefix.length)) && response.request().method() === 'GET');
            await buttons.nth(index).click();
            const response = await pending;
            verify(response.status() === 200);
            const history = await response.json();
            await expect(page.locator('#rh-product-detail')).toBeVisible();
            if (history.sufficiency.eligible) {
                await expect(page.locator('#rh-analyze')).toBeEnabled();
                return history;
            }
        }
        if (await page.locator('#rh-next').isDisabled()) break;
        const old = await page.locator('#rh-pagination').innerText();
        await page.locator('#rh-next').click();
        await expect(page.locator('#rh-pagination')).not.toHaveText(old);
    }
    throw new Error('No eligible product in the checked pages.');
}

async function historyAndAnalysis(page, input, batchId) {
    const history = await eligibleProduct(page, input, batchId);
    phase = 'history';
    verify(history.source_label === SOURCE_LABEL && history.coverage_end === CUTOFF);
    verify(history.points.length >= 60 && history.summary.count === history.points.length);
    verify(history.points.every((point) => /^2026-\d{2}-\d{2}$/.test(point.date) && point.date <= CUTOFF));
    verify(history.points.at(-1).date === CUTOFF && history.summary.last_date === CUTOFF);
    await expect(page.locator('#rh-coverage')).toContainText(CUTOFF);
    await expect(page.locator('#rh-history-chart svg')).toBeVisible();
    await expect(page.locator('#rh-observations tr')).toHaveCount(history.points.length);
    phase = 'forecast';
    await page.locator('#rh-model').selectOption('moving_average');
    await page.locator('#rh-horizon').selectOption('7');
    const url = `${input.origin}/api/historical-2026/imports/${batchId}/products/${history.product.id}/analysis?model=moving_average&horizon=7`;
    const pending = responseFor(page, url);
    await page.locator('#rh-analyze').click();
    const response = await pending;
    verify(response.status() === 200);
    const result = await response.json();
    const forecast = result.forecast;
    verify(result.source_label === SOURCE_LABEL && result.coverage_end === CUTOFF);
    verify(forecast.status === 'available' && forecast.cutoff === CUTOFF && forecast.horizon === 7);
    verify(forecast.time_unit === 'operational_day' && forecast.points.length === 7);
    verify(forecast.points.every((point, index) => point.period === index + 1 && point.date === FUTURE_7[index]
        && Number.isFinite(point.quantity) && point.quantity >= 0));
    verify(result.points.every((point) => point.date <= CUTOFF));
    verify(result.namespace === `real_2026:${batchId}:${history.product.id}`);
    await expect(page.locator('#rh-forecast-status')).toContainText('Pronóstico generado');
    await expect(page.locator('#rh-forecast-metadata')).toContainText(SOURCE_LABEL);
    await expect(page.locator('#rh-forecast-metadata')).toContainText(CUTOFF);
    await expect(page.locator('#rh-forecast-points tr')).toHaveCount(7);
    await expect(page.locator('#rh-forecast-chart svg')).toBeVisible();
    return history.points.length;
}

async function dimensions(page) {
    const size = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}));
    verify(size.scroll <= size.width + 1);
    return size;
}

async function main() {
    const input = metadata();
    phase = 'browser';
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    let context;
    try {
        context = await browser.newContext({viewport: {width: 1600, height: 900},
            deviceScaleFactor: 1, serviceWorkers: 'block'});
        await context.route('**/*', (route) => {
            if (new URL(route.request().url()).origin === input.origin) return route.continue();
            return route.abort();
        });
        const page = await context.newPage();
        let browserErrors = 0;
        page.on('pageerror', () => { browserErrors++; });
        await login(page, input);
        phase = 'historical-page';
        const entry = await page.goto(input.origin + '/historical-2026');
        verify(entry.status() === 200);
        await expect(page.getByRole('heading', {name: SOURCE_LABEL, exact: true})).toBeVisible();
        await expect(page.locator('#real-history')).toHaveAttribute('data-available', 'true');
        phase = 'upload';
        const preview = await upload(page, input, 201);
        await confirm(page, input, preview);
        const observations = await historyAndAnalysis(page, input, preview.batch.id);
        phase = 'desktop';
        const desktop = await dimensions(page);
        phase = 'mobile';
        await page.setViewportSize({width: 390, height: 844});
        await expect(page.locator('#rh-history-chart svg')).toBeVisible();
        const mobile = await dimensions(page);
        await page.setViewportSize({width: 1600, height: 900});
        phase = 'duplicate';
        const replay = await upload(page, input, 200);
        verify(replay.created === false && replay.batch.id === preview.batch.id);
        verify(replay.batch.status === 'confirmed');
        phase = 'existing-pages';
        for (const route of LEGACY_PAGES) {
            phase = 'existing-page:' + route;
            const response = await page.goto(input.origin + route);
            const expectedPath = route === '/reports' ? '/dashboard' : route;
            verify(response.status() === 200 && new URL(page.url()).pathname === expectedPath);
        }
        phase = 'browser-errors';
        verify(browserErrors === 0);
        process.stdout.write(JSON.stringify({result: 'PASS', login_ui: true, files: input.files.length,
            preview_confirmation_history_forecast: true, selected_product_observations: observations,
            forecast_periods: 7, coverage_cutoff: CUTOFF, duplicate_created: false,
            existing_pages_http_200: LEGACY_PAGES.length, browser_errors: browserErrors, desktop, mobile}) + '\n');
    } finally {
        if (context) await context.close();
        await browser.close();
    }
}

main().catch((error) => {
    // Browser exceptions can include form values, response bodies or private paths.
    const network = String(error.message).match(/net::([A-Z_]+)/);
    process.stderr.write(JSON.stringify({result: 'FAIL', phase,
        network: network ? network[1] : null}) + '\n');
    process.exitCode = 1;
});
