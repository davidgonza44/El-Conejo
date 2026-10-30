// Run against scripts/test_settings_reference.py --serve: synthetic login, no DB.
const {chromium} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const output = '.tmp/product-layers-validation';
fs.mkdirSync(output, {recursive: true});
let browser;
(async () => {
    browser = await chromium.launch({channel: 'msedge'});
    const page = await browser.newPage({viewport: {width: 1600, height: 900}, deviceScaleFactor: 1, locale: 'es-VE'});
    const mutations = [], downloads = [], errors = [];
    page.on('request', request => { if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(request.method()); });
    page.on('download', download => downloads.push(download.suggestedFilename()));
    page.on('pageerror', error => errors.push(error.message));
    const origin = process.env.PRODUCT_PREVIEW_URL || 'http://127.0.0.1:5000';
    // Reconstruct the pre-change render without editing/restoring any local file.
    await page.route('**/products?ref=1', async route => {
        const response = await route.fetch();
        const html = (await response.text())
            .replace(/<link[^>]+ix_product_layers\.css[^>]*>/g, '')
            .replace(/<script[^>]+ix_product_layers\.js[^>]*><\/script>/g, '')
            .replace(/<dialog id="pl-dialog"[\s\S]*?<\/dialog>/, '');
        await route.fulfill({response, body: html});
    });
    await page.goto(`${origin}/products?ref=1`, {waitUntil: 'networkidle'});
    const baseBefore = await page.screenshot({path: `${output}/base-before.png`});
    await page.unroute('**/products?ref=1');
    await page.reload({waitUntil: 'networkidle'});
    const baseAfter = await page.screenshot({path: `${output}/base-after.png`});
    const changedPixels = await page.evaluate(async ([before, after]) => {
        const pixels = async data => {
            const image = new Image(); image.src = `data:image/png;base64,${data}`;
            await image.decode();
            const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
            const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
            return context.getImageData(0, 0, image.width, image.height).data;
        };
        const [a, b] = await Promise.all([pixels(before), pixels(after)]);
        let differences = 0;
        for (let i = 0; i < a.length; i += 4) {
            if (a[i] !== b[i] || a[i + 1] !== b[i + 1] || a[i + 2] !== b[i + 2]) differences++;
        }
        return differences;
    }, [baseBefore.toString('base64'), baseAfter.toString('base64')]);
    assert(changedPixels <= 1, `Base changed by ${changedPixels} pixels (one rasterization pixel tolerated)`);
    console.log(`Base visual comparison: ${changedPixels} / 1440000 differing pixels.`);
    assert.deepEqual(await page.evaluate(() => [innerWidth, innerHeight, devicePixelRatio, visualViewport.scale]), [1600, 900, 1, 1]);
    // Chromium screenshot caret suppression leaves inert empty style attributes.
    const baseMarkup = () => page.locator('.iv-prod-page').evaluate(element => {
        const copy = element.cloneNode(true);
        copy.querySelectorAll('[style=""]').forEach(node => node.removeAttribute('style'));
        return copy.innerHTML;
    });
    const base = await baseMarkup();
    const sidebar = await page.locator('#ix-sidebar').innerHTML();
    const shot = name => page.screenshot({path: `${output}/${name}.png`});
    await shot('base');
    const drawer = page.locator('#pl-dialog');
    const eye = page.locator('.iv-catalog-table [aria-label="Ver detalle"]').first();
    const pencil = page.locator('.iv-catalog-table [aria-label="Editar"]').first();
    async function closed() {
        await drawer.waitFor({state: 'hidden'});
        await page.waitForTimeout(30);
        assert.equal(await page.locator('body').evaluate(n => n.style.overflow), '');
    }
    async function oneLayer() {
        assert.equal(await page.locator('dialog[open]').count(), 1);
        assert.equal(await page.locator('body').evaluate(n => n.style.overflow), 'hidden');
        assert(await drawer.evaluate(n => n.contains(document.activeElement)));
    }
    await eye.click(); await oneLayer();
    assert.equal(await page.locator('#pl-title').innerText(), 'Detalle del producto');
    assert.equal(await page.locator('#pl-code').innerText(), 'Código: TAL-750W');
    await shot('detail-initial');
    await page.locator('#pl-action').click(); await oneLayer();
    assert.equal(await page.locator('#pl-field-code').inputValue(), 'TAL-750W');
    for (const [key, value] of Object.entries({category: 'Herramientas eléctricas', supplier: 'Distribuidora Andina', branch: 'Sucursal Principal', brand: 'Bosch', unit: 'Unidad', price: '1850.00', cost: '1250.00', stock: '24', minimum: '10', status: 'Disponible'})) {
        assert.equal(await page.locator(`#pl-field-${key}`).inputValue(), value);
    }
    await shot('edit-initial');
    await page.locator('#pl-field-name').fill('Simulación sin escritura');
    await page.locator('#pl-field-stock').fill('999');
    await page.locator('#pl-action').click(); await closed();
    assert(await eye.evaluate(n => n === document.activeElement));
    await pencil.click();
    assert.equal(await page.locator('#pl-field-stock').inputValue(), '24');
    await page.locator('#pl-cancel').click(); await closed();
    assert(await pencil.evaluate(n => n === document.activeElement));
    await eye.click(); await page.keyboard.press('Escape'); await closed();
    await eye.click(); await page.getByRole('button', {name: 'Cerrar capa', exact: true}).click(); await closed();
    await eye.click(); await page.mouse.click(600, 100); await closed();
    await page.getByRole('link', {name: 'Ver todo el catálogo', exact: true}).click(); await oneLayer();
    assert.equal(page.url(), `${origin}/products?ref=1`);
    assert.equal(await page.locator('#pl-rows tr').count(), 5);
    await shot('catalog-initial');
    await page.locator('#pl-per-page').selectOption('10');
    assert.equal(await page.locator('#pl-rows tr').count(), 8);
    await page.locator('#pl-per-page').selectOption('5');
    assert.equal(await page.locator('#pl-rows tr').count(), 5);
    await page.getByRole('button', {name: 'Siguiente', exact: true}).click();
    assert.equal(await page.locator('#pl-rows tr').count(), 3);
    await page.locator('#pl-search').fill('TAL-750W');
    assert.equal(await page.locator('#pl-rows tr').count(), 1);
    await page.locator('#pl-search').fill('no-coincide');
    assert.match(await page.locator('#pl-rows').innerText(), /No se encontraron/);
    await page.locator('#pl-search').fill('');
    await page.locator('#pl-filter-status').selectOption('Agotado');
    assert.equal(await page.locator('#pl-rows tr').count(), 1);
    await page.locator('#pl-rows button').first().click(); await oneLayer();
    assert.equal(await page.locator('#pl-code').innerText(), 'Código: BRO-1/2');
    await page.locator('#pl-action').click();
    assert.equal(await page.locator('#pl-field-code').inputValue(), 'BRO-1/2');
    await page.keyboard.press('Escape'); await closed();
    const exportButton = page.locator('.iv-prod-toolbar').getByRole('button', {name: 'Exportar', exact: true});
    await exportButton.click(); await oneLayer(); await shot('export-initial');
    for (const format of ['csv', 'pdf', 'xlsx']) await page.locator(`.pl-format:has([value="${format}"])`).click();
    await page.locator('#pl-export input[type="checkbox"]').first().check();
    await page.locator('#pl-action').click(); await closed();
    assert.match(await page.locator('#pl-feedback').innerText(), /No se generó/);
    await exportButton.click(); await page.locator('#pl-cancel').click(); await closed();
    // Programmatic triggers exercise transitions even while the base is inert.
    await eye.click();
    await page.locator('.iv-prod-toolbar').getByRole('button', {name: 'Nuevo producto', exact: true}).evaluate(n => n.click());
    await page.waitForTimeout(40);
    assert.equal(await page.locator('dialog[open]').count(), 1);
    assert(await page.locator('#np-drawer').isVisible());
    assert.equal(await page.locator('body').evaluate(n => n.style.overflow), 'hidden');
    await eye.evaluate(n => n.click()); await page.waitForTimeout(40); await oneLayer();
    await page.keyboard.press('Escape'); await closed();
    assert.equal(await baseMarkup(), base);
    assert.equal(await page.locator('#ix-sidebar').innerHTML(), sidebar);
    assert(await page.locator('[aria-controls="ix-nav-pred"]').isVisible());
    await page.reload({waitUntil: 'networkidle'});
    assert.equal(await baseMarkup(), base);
    await eye.click(); await shot('detail-final'); await page.keyboard.press('Escape');
    await pencil.click(); await shot('edit-final'); await page.keyboard.press('Escape');
    await page.getByRole('link', {name: 'Ver todo el catálogo', exact: true}).click(); await shot('catalog-final'); await page.keyboard.press('Escape');
    await exportButton.click(); await shot('export-final'); await page.keyboard.press('Escape');
    await page.setViewportSize({width: 390, height: 844});
    await pencil.click(); await shot('edit-mobile');
    assert(await drawer.evaluate(n => n.scrollWidth <= n.clientWidth));
    await page.keyboard.press('Escape');
    await page.setViewportSize({width: 1600, height: 900});
    await page.goto(`${origin}/inventory?ref=1`, {waitUntil: 'networkidle'});
    assert.equal(await drawer.count(), 0);
    assert(await page.locator('[aria-controls="ix-nav-pred"]').isVisible());
    assert.deepEqual(mutations, []); assert.deepEqual(downloads, []); assert.deepEqual(errors, []);
    console.log('PASS: Chromium 1600x900 DPR1 zoom100, four overlays, same-product editing, simulation/reset, X/Escape/cancel/backdrop/focus/scroll, catalog search/filter/pagination, one-layer including New Product, base/sidebar unchanged, predictive visible, mobile, no mutation requests/downloads/JS errors.');
    await browser.close();
})().catch(async error => {console.error(error); await browser?.close(); process.exitCode = 1;});
