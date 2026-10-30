/* Real UI against test_catalog_approval_review.py --serve only, using synthetic data. */
const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = new URL(process.argv[2] || '');
assert.equal(base.hostname, '127.0.0.1');
assert.equal(base.protocol, 'http:');
assert(base.port && base.port !== '5000', 'Use the disposable fixture port');
const output = '.tmp/catalog-approval-review';
fs.mkdirSync(output, {recursive: true});

(async () => {
    const browser = await chromium.launch({channel: 'chrome', headless: true});
    const errors = [];
    const context = await browser.newContext({viewport: {width: 1600, height: 900}, deviceScaleFactor: 1});
    await context.route('**/*', route => new URL(route.request().url()).port === '5000' ? route.abort() : route.continue());
    // Optional public vendor cache for offline rendering; no private application files.
    const assets = process.argv[3];
    if (assets) {
        for (const [pattern, filename, contentType] of [
            ['**/chart.umd.min.js', 'chart.umd.min.js', 'text/javascript'],
            ['**/tabler.min.css', 'tabler.min.css', 'text/css'],
            ['**/tabler.min.js', 'tabler.min.js', 'text/javascript'],
            ['**/tabler-icons.min.css', 'tabler-icons.min.css', 'text/css'],
            ['**/tabler-icons.woff2*', 'tabler-icons.woff2', 'font/woff2'],
            ['https://fonts.googleapis.com/**', 'inter.css', 'text/css'],
        ]) await context.route(pattern, route => route.fulfill({path: path.join(assets, filename), contentType}));
    }
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    const go = path => page.goto(new URL(path, base).href, {waitUntil: 'networkidle'});
    const snapshot = name => page.screenshot({path: `${output}/${name}.png`, fullPage: true});
    const api = (path, method = 'GET', data) => context.request.fetch(new URL(path, base).href, {method, data});
    try {
        await go('/_fixture/session/admin');
        const pendingNav = page.getByRole('navigation', {name: 'Revisar propuestas pendientes'});
        await expect(pendingNav.getByRole('link', {name: 'Productos pendientes 2'})).toBeVisible();
        await expect(pendingNav.getByRole('link', {name: 'Proveedores pendientes 1'})).toBeVisible();
        await expect(pendingNav.getByRole('link', {name: 'Categorías pendientes 1'})).toBeVisible();
        await snapshot('admin-dashboard');
        await pendingNav.getByRole('link', {name: 'Productos pendientes 2'}).click();
        await expect(page.locator('#ix-filter-approval')).toHaveValue('pending');
        assert.equal(await page.locator('#ix-filter-approval').evaluate(el => getComputedStyle(el).opacity), '1');
        await expect(page.locator('#ix-catalog-body tr')).toHaveCount(2);
        await expect(page.getByRole('button', {name: 'Proponer modificación', exact: true})).toHaveCount(0);
        await snapshot('admin-products-pending');
        const productRow = page.locator('#ix-catalog-body tr').filter({hasText: 'Producto propuesto 1'});
        await productRow.getByRole('button', {name: 'Ver detalle', exact: true}).click();
        await expect(page.locator('#pl-approve')).toBeVisible();
        await expect(page.locator('#pl-reject')).toBeVisible();
        await snapshot('admin-product-review');
        await page.locator('#pl-rejection-reason').fill('No corresponde al catálogo');
        await page.locator('#pl-reject').click();
        await expect(page.locator('#pl-review-note')).toContainText('No corresponde al catálogo');
        await expect(page.locator('#pl-review-actions')).toBeHidden();
        await page.keyboard.press('Escape');
        await go('/products?approval_status=pending');
        await page.locator('#ix-catalog-body').getByRole('button', {name: 'Ver detalle', exact: true}).click();
        await expect(page.locator('#pl-review-comparison')).toContainText('Modificación pendiente');
        await page.locator('#pl-approve').click();
        await expect(page.locator('#pl-review-actions')).toBeHidden();
        await page.keyboard.press('Escape');

        await go('/suppliers?approval_status=pending');
        await expect(page.locator('#sp-filters [name=approval_status]')).toHaveValue('pending');
        await expect(page.locator('.sp-table tbody tr')).toHaveCount(1);
        await snapshot('admin-suppliers-pending');
        await page.locator('.sp-table').getByRole('button', {name: /Ver detalle/}).click();
        await expect(page.locator('#sp-review')).toBeVisible();
        await expect(page.locator('#sp-approve')).toBeVisible();
        await snapshot('admin-supplier-review');
        const supplier = (await (await api('/api/suppliers?approval_status=pending')).json()).items[0];
        await page.locator('#sp-review-reason').fill('Datos insuficientes');
        await page.locator('#sp-reject').click();
        await expect(page.locator('#sp-detail-state')).toContainText('Rechazado');
        await expect(page.locator('#sp-review')).toBeHidden();
        await expect(page.locator('#sp-detail-body')).toContainText('Datos insuficientes');
        assert.equal((await api(`/api/suppliers/${supplier.id}/approve`, 'POST', {})).status(), 409);
        await page.keyboard.press('Escape');

        await go('/categories?approval_status=pending');
        await expect(page.locator('#ix-cat-approval')).toHaveValue('pending');
        assert.equal(await page.locator('#ix-cat-approval').evaluate(el => getComputedStyle(el).opacity), '1');
        await expect(page.locator('#ix-cat-body tr')).toHaveCount(1);
        await snapshot('admin-categories-pending');
        await page.locator('#ix-cat-body').getByRole('button', {name: 'Ver detalle', exact: true}).click();
        await expect(page.locator('#cl-content [data-cl-review]')).toBeVisible();
        await expect(page.locator('#cl-content [data-cl-action=approve]')).toBeVisible();
        await expect(page.locator('#cl-content [data-cl-action=edit]')).toBeHidden();
        await snapshot('admin-category-review');
        await page.locator('#cl-content [data-cl-rejection-reason]').fill('Categoría duplicada');
        await page.locator('#cl-content [data-cl-action=reject]').click();
        await expect(page.locator('#cl-content [data-cl-detail=reason]')).toHaveText('Categoría duplicada');
        await expect(page.locator('#cl-content [data-cl-review]')).toBeHidden();
        await page.keyboard.press('Escape');

        await go('/_fixture/session/asistente');
        await expect(page.getByRole('navigation', {name: 'Revisar propuestas pendientes'})).toHaveCount(0);
        await go('/products');
        await page.getByRole('button', {name: 'Nuevo producto', exact: true}).first().click();
        await page.locator('#np-name').fill('Propuesta desde navegador');
        await page.locator('#np-sku').fill('BROWSER-PROP');
        await page.locator('#np-category').selectOption({label: 'Pruebas aisladas'});
        await page.locator('#np-unit').selectOption({label: 'Unidad'});
        const productCreated = page.waitForResponse(r => new URL(r.url()).pathname === '/api/products' && r.request().method() === 'POST');
        await page.locator('#np-form').getByRole('button', {name: /Guardar|propuesta/i}).click();
        const creation = await productCreated;
        assert.equal(creation.status(), 201);
        assert.equal((await creation.json()).approval_status, 'pending');
        await page.keyboard.press('Escape');

        await go('/suppliers');
        const approvedSupplier = page.locator('.sp-table tbody tr').filter({hasText: 'Proveedor propuesto 2'});
        await approvedSupplier.getByRole('button', {name: /Más acciones/}).click();
        for (const action of ['status', 'preferred', 'delete']) await expect(page.locator(`#sp-menu [data-action=${action}]`)).toHaveCount(0);
        await page.keyboard.press('Escape');
        await page.locator('#sp-new').click();
        for (const [name, value] of Object.entries({name: 'Proveedor desde navegador', tax_id: 'BROWSER-SUP', contact_name: 'Contacto sintético', phone: '0000000000', email: 'browser@example.invalid', category: 'Prueba'})) {
            await page.locator(`#sp-editor-form [name=${name}]`).fill(value);
        }
        const supplierCreated = page.waitForResponse(r => new URL(r.url()).pathname === '/api/suppliers' && r.request().method() === 'POST');
        await page.locator('#sp-editor-form').getByRole('button', {name: 'Enviar propuesta', exact: true}).click();
        const supplierCreation = await supplierCreated;
        assert.equal(supplierCreation.status(), 201);
        await expect(page.locator('#sp-detail-state')).toContainText('Pendiente');
        await expect(page.locator('#sp-review')).toBeHidden();
        await page.keyboard.press('Escape');

        await go('/categories');
        await page.getByRole('button', {name: 'Nueva categoría', exact: true}).click();
        await page.locator('#cl-name').fill('Categoría desde navegador');
        const categoryCreated = page.waitForResponse(r => new URL(r.url()).pathname === '/api/categories' && r.request().method() === 'POST');
        await page.locator('#cl-content [data-cl-action=save]').click();
        const categoryCreation = await categoryCreated;
        assert.equal(categoryCreation.status(), 201);
        assert.equal((await categoryCreation.json()).approval_status, 'pending');
        await go('/categories?approval_status=pending');
        await page.locator('#ix-cat-body').getByRole('button', {name: 'Ver detalle', exact: true}).click();
        await expect(page.locator('#cl-content [data-cl-review]')).toHaveCount(0);
        await page.keyboard.press('Escape');

        await go('/_fixture/session/inventario');
        for (const path of ['/products', '/categories']) {
            await go(path + '?approval_status=pending');
            const body = page.locator(path === '/products' ? '#ix-catalog-body' : '#ix-cat-body');
            await body.getByRole('button', {name: 'Ver detalle', exact: true}).first().click();
            await expect(page.getByRole('button', {name: 'Aprobar', exact: true})).toHaveCount(0);
            await expect(page.getByRole('button', {name: 'Rechazar', exact: true})).toHaveCount(0);
            await page.keyboard.press('Escape');
        }
        assert.equal((await api('/suppliers')).status(), 403);

        await go('/_fixture/session/admin');
        await page.setViewportSize({width: 390, height: 844});
        for (const path of ['/dashboard', '/products', '/suppliers', '/categories']) {
            await go(path + (path === '/dashboard' ? '' : '?approval_status=pending'));
            if (path !== '/dashboard') await expect(page.getByRole('combobox', {name: 'Estado de aprobación', exact: true}).first()).toBeVisible();
            assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), path + ' must fit mobile');
            await snapshot('mobile-' + path.slice(1));
        }
        await page.setViewportSize({width: 1600, height: 900});
        for (const [path, bodyId, buttonId, decisionState] of [
            ['/products', '#ix-catalog-body', '#pl-approve', '#pl-review-actions'],
            ['/suppliers', '.sp-table tbody', '#sp-approve', '#sp-review'],
            ['/categories', '#ix-cat-body', '#cl-content [data-cl-action=approve]', '#cl-content [data-cl-review]'],
        ]) {
            await go(path + '?approval_status=pending');
            await page.locator(bodyId).getByRole('button', {name: /Ver detalle/}).first().click();
            await page.locator(buttonId).click();
            await expect(page.locator(decisionState)).toBeHidden();
            await page.keyboard.press('Escape');
            await go(path + '?approval_status=pending');
            await expect(page.locator(bodyId)).toContainText(/No hay|No se encontraron/);
        }
        await go('/categories?approval_status=approved');
        await expect(page.getByRole('button', {name: 'Editar', exact: true})).toHaveCount(0);
        assert.deepEqual(errors, [], 'No JavaScript errors');
        console.log('PASS browser: dashboard links, pending filters, product change review, rejection reasons, assistant proposals, inventory restrictions and mobile.');
        console.log('Synthetic screenshots: ' + output);
    } finally {
        await browser.close();
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
