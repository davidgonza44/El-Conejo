// Isolated browser contract test. All API calls are intercepted; no database writes.
const { chromium, expect } = require('@playwright/test');
const assert = require('node:assert/strict');
const origin = process.env.PRODUCT_IMAGE_TEST_URL || 'http://127.0.0.1:5032';
(async () => {
    const browser = await chromium.launch({ channel: 'msedge' });
    try {
        const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
        const errors = [], writes = [];
        let failImage = true, failData = false, holdUpload;
        const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=', 'base64');
        const product = { id: 1, name: 'Producto de prueba', code: 'IMAGE-TEST', category_id: 1,
            category: 'Prueba', unit: 'Unidad', sale_price: 2, purchase_price: 1, current_stock: 5,
            minimum_stock: 0, stock_status: 'disponible', image_url: '/test-image/current.png', is_active: true };
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/test-image/*', route => route.fulfill({ contentType: 'image/png', body: png }));
        await page.route('**/api/**', async route => {
            const request = route.request(), path = new URL(request.url()).pathname;
            const method = request.method();
            if (method !== 'GET') writes.push({ path, method, body: request.postData() });
            const reply = json => route.fulfill({ json });
            if (path === '/api/categories') return reply({ items: [{ id: 1, name: 'Prueba' }] });
            if (path === '/api/products/summary') return reply({ total_active: 1, low_stock: 0,
                out_of_stock: 0, active_categories: 1, category_distribution: [], inventory_alerts: [], recent_products: [] });
            if (path === '/api/products') return reply({ items: [product], count: 1,
                pagination: { total: 1, page: 1, per_page: 8, pages: 1 } });
            if (path === '/api/products/1/image') {
                if (failImage) return route.fulfill({ status: 500, json: { error: 'Fallo de prueba' } });
                if (holdUpload) await holdUpload;
                product.image_url = method === 'DELETE' ? null : '/test-image/replaced.png';
                return reply({ product, image_url: product.image_url });
            }
            if (path === '/api/products/1') {
                if (method === 'PUT') {
                    if (failData) return route.fulfill({ status: 400, json: { error: 'Datos inválidos' } });
                    Object.assign(product, request.postDataJSON());
                }
                return reply(product);
            }
            return route.abort();
        });
        await page.goto(`${origin}/products`, { waitUntil: 'networkidle' });
        const open = () => page.locator('#ix-catalog-body [aria-label="Editar"]').click();
        await open();
        await page.locator('#pl-field-name').fill('Nombre que debe conservarse');
        await page.locator('#pl-image-file').setInputFiles({ name: 'prueba.png', mimeType: 'image/png', buffer: png });
        await expect(page.locator('#pl-edit-image')).toHaveAttribute('src', /^blob:/);
        const selectedUrl = await page.locator('#pl-edit-image').getAttribute('src');
        failData = true;
        await page.locator('#pl-action').click();
        await expect(page.locator('#pl-image-message')).toHaveText(/No se pudieron guardar los datos/);
        assert.equal(writes.filter(r => r.path.endsWith('/image')).length, 0);
        failData = false;
        await page.locator('#pl-action').click();
        await expect(page.locator('#pl-image-message')).toHaveText(/Los datos del producto se guardaron/);
        await expect(page.locator('#pl-field-name')).toHaveValue('Nombre que debe conservarse');
        await expect(page.locator('#pl-edit-image')).toHaveAttribute('src', selectedUrl);
        assert.equal(product.image_url, '/test-image/current.png');
        assert.match(writes.find(r => r.path.endsWith('/image')).body, /name="image"; filename="prueba.png"/);
        let release;
        holdUpload = new Promise(resolve => { release = resolve; });
        failImage = false;
        const count = writes.length;
        await page.locator('#pl-action').evaluate(button => { button.click(); button.click(); });
        await expect(page.locator('#pl-action')).toBeDisabled();
        await page.keyboard.press('Escape');
        await expect(page.locator('#pl-dialog')).toBeVisible();
        release();
        await expect(page.locator('#pl-dialog')).toBeHidden();
        assert.equal(writes.length, count + 2, 'one PUT and one image POST');
        await expect(page.locator('#ix-catalog-body img')).toHaveAttribute('src', product.image_url);
        await open();
        failImage = true;
        page.once('dialog', dialog => dialog.accept());
        await page.locator('#pl-image-delete').click();
        await expect(page.locator('#pl-image-message')).toHaveText(/No se pudo eliminar/);
        await expect(page.locator('#pl-edit-image')).toHaveAttribute('src', product.image_url);
        await expect(page.locator('#pl-image-delete')).toBeEnabled();
        for (const write of writes.filter(r => r.method === 'PUT')) {
            assert.equal(JSON.parse(write.body).current_stock, undefined);
        }
        assert.deepEqual(errors, []);
        console.log('PASS: intercepted API contracts only (NOT LIVE): data failure, image failure, retained fields/preview, multipart image, retry, duplicate guard, Escape during upload, table refresh, delete failure, no stock payload. No DB accessed.');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
