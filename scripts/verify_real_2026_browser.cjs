/* Browser acceptance for an explicitly disposable server, never 5000/5001. */
const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const metadataFile = process.argv[2];
assert.ok(metadataFile, 'Indica el JSON privado del servidor desechable.');
const metadataPath = path.resolve(metadataFile);
assert.ok(metadataPath.startsWith(path.resolve('.tmp') + path.sep));
const metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf8'));
const origin = new URL(metadata.origin);
assert.equal(origin.hostname, '127.0.0.1');
assert.ok(!['5000', '5001', ''].includes(origin.port));
const apiBase = metadata.origin + '/api/historical-2026/imports';

async function invariant(context, finish = false) {
    const result = await context.request.get(metadata.origin + '/__isolated_real2026/check' + (finish ? '?finish=1' : ''),
        {headers: {'X-Isolated-Key': metadata.isolated_key}});
    assert.equal(result.status(), 200);
    const data = await result.json();
    assert.equal(data.isolated, true);
    assert.equal(data.operational_and_2025_tables_unchanged, true);
    return data;
}

(async () => {
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    const errors = [];
    let context;
    try {
        context = await browser.newContext({viewport: {width: 1600, height: 900}, deviceScaleFactor: 1});
        await context.route('**/*', (route) => {
            if (new URL(route.request().url()).origin === metadata.origin) return route.continue();
            return route.abort();
        });
        const initial = await invariant(context);
        assert.equal(initial.daily_observations, 0);
        const page = await context.newPage();
        page.on('pageerror', (error) => errors.push(error.message));
        await page.goto(metadata.login_url);
        await expect(page.getByRole('heading', {name: 'Histórico real 2026', exact: true})).toBeVisible();
        await page.locator('#rh-files').setInputFiles(Object.values(metadata.files));
        const uploadResponse = page.waitForResponse((response) => response.url() === apiBase + '/preview' && response.request().method() === 'POST', {timeout: 180000});
        await page.locator('#rh-preview-button').click();
        const uploaded = await uploadResponse;
        assert.equal(uploaded.status(), 201, await uploaded.text());
        const preview = await uploaded.json();
        await expect(page.locator('#rh-batch-state')).toContainText('Vista previa');
        await expect(page.locator('#rh-catalog')).toBeHidden();
        await expect(page.locator('#rh-confirm')).toBeVisible();
        await page.locator('#rh-confirm').click();
        await expect(page.locator('#rh-batch-state')).toContainText('confirmada', {timeout: 30000});
        await expect(page.locator('#rh-products tr').first()).toBeVisible();

        // Select an eligible product through the actual rendered catalog.
        let selected = false;
        for (let pageNumber = 0; pageNumber < 8 && !selected; pageNumber++) {
            const buttons = page.locator('#rh-products button');
            const count = await buttons.count();
            for (let index = 0; index < count; index++) {
                await buttons.nth(index).click();
                await expect(page.locator('#rh-product-detail')).toBeVisible();
                await page.waitForTimeout(50);
                if (await page.locator('#rh-analyze').isEnabled()) { selected = true; break; }
            }
            if (!selected && await page.locator('#rh-next').isEnabled()) {
                const previous = await page.locator('#rh-pagination').innerText();
                await page.locator('#rh-next').click();
                await expect(page.locator('#rh-pagination')).not.toHaveText(previous);
            }
        }
        assert.equal(selected, true, 'No se encontró producto suficiente en las páginas consultadas.');
        await expect(page.locator('#rh-coverage')).toContainText('2026-09-18');
        await expect(page.locator('#rh-history-chart svg')).toBeVisible();
        const analysisResponse = page.waitForResponse((response) => response.url().includes('/analysis?'));
        await page.locator('#rh-analyze').click();
        const analysis = await (await analysisResponse).json();
        assert.equal(analysis.forecast.status, 'available');
        assert.equal(analysis.forecast.points.length, 7);
        await expect(page.locator('#rh-forecast-status')).toContainText('Pronóstico generado');
        await expect(page.locator('#rh-forecast-points tr')).toHaveCount(7);
        await expect(page.locator('#rh-forecast-chart svg')).toBeVisible();
        const desktop = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}));
        assert.ok(desktop.scroll <= desktop.width + 1, JSON.stringify(desktop));
        await page.setViewportSize({width: 390, height: 844});
        const mobile = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}));
        assert.ok(mobile.scroll <= mobile.width + 1, JSON.stringify(mobile));
        await page.setViewportSize({width: 1600, height: 900});
        await page.locator('#rh-files').setInputFiles(Object.values(metadata.files));
        const replayResponse = page.waitForResponse((response) => response.url() === apiBase + '/preview' && response.request().method() === 'POST', {timeout: 180000});
        await page.locator('#rh-preview-button').click();
        const replay = await (await replayResponse).json();
        assert.equal(replay.created, false);
        assert.equal(replay.batch.id, preview.batch.id);
        const final = await invariant(context, true);
        assert.equal(final.batches, 1);
        assert.equal(final.daily_observations, preview.batch.summary.total_points);
        assert.deepEqual(errors, []);
        process.stdout.write(JSON.stringify({result: 'PASS', preview_confirm_history_forecast: true,
            repeated_import_duplicates: 0, browser_errors: errors.length, desktop, mobile,
            daily_observations: final.daily_observations, products: final.products,
            operational_and_2025_tables_unchanged: true}) + '\n');
    } finally {
        if (context) await context.close();
        await browser.close();
    }
})().catch((error) => { console.error(error.message); process.exitCode = 1; });
