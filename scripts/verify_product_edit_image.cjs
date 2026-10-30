// Reference mode: no DB. LIVE requires an explicitly verified E2E server and fixture ID.
const { chromium, expect } = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const reference = process.argv.includes('--reference');
const origin = process.env.PRODUCT_IMAGE_TEST_URL || 'http://127.0.0.1:5032';
const productId = process.env.PRODUCT_IMAGE_TEST_ID;
const output = '.tmp/product-edit-image-validation';
if (!reference && (!productId || process.env.PRODUCT_IMAGE_TEST_DATABASE !== 'ferreteria_conejo_e2e')) {
    throw new Error('LIVE requires a verified ferreteria_conejo_e2e server and disposable fixture ID.');
}
fs.mkdirSync(output, { recursive: true });
(async () => {
    const browser = await chromium.launch({ channel: 'msedge' });
    try {
        const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
        const mutations = [], errors = [];
        page.on('request', request => {
            if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(request);
        });
        page.on('pageerror', error => errors.push(error.message));
        const url = `${origin}/products${reference ? '?ref=1' : ''}`;
        await page.goto(url, { waitUntil: 'networkidle' });
        await page.evaluate(() => document.fonts.ready);
        assert.deepEqual(await page.evaluate(() => [innerWidth, innerHeight, devicePixelRatio, visualViewport.scale]), [1600, 900, 1, 1]);
        const apiProduct = async () => {
            const response = await page.request.get(`${origin}/api/products/${productId}`);
            assert.equal(response.status(), 200);
            return response.json();
        };
        const original = reference ? null : await apiProduct();
        const open = async () => {
            if (!reference) {
                await page.locator('#ix-prod-search').fill(original.code);
                await expect(page.locator('#ix-catalog-body tr')).toHaveCount(1);
            }
            await page.locator('.iv-catalog-table').getByRole('button', { name: /^Editar(?: |$)/ }).first().click();
            await expect(page.locator('#pl-dialog')).toBeVisible();
            await expect(page.locator('#pl-image-editor')).toBeVisible();
        };
        const shot = name => page.screenshot({ path: `${output}/product-edit-image-${name}${reference ? '-reference' : ''}.png` });
        const file = async (type = 'png') => ({
            name: `prueba.${type}`, mimeType: `image/${type === 'jpg' ? 'jpeg' : type}`,
            buffer: Buffer.from(await page.evaluate(mime => {
                const canvas = document.createElement('canvas');
                canvas.width = canvas.height = 800;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = '#edf5ff'; ctx.fillRect(0, 0, 800, 800);
                ctx.fillStyle = '#1768d4'; ctx.fillRect(180, 260, 440, 180);
                ctx.fillRect(390, 430, 110, 200);
                ctx.fillStyle = '#33415a'; ctx.fillRect(90, 305, 90, 80);
                return canvas.toDataURL(mime).split(',')[1];
            }, `image/${type === 'jpg' ? 'jpeg' : type}`), 'base64')
        });
        await open();
        await expect(page.locator('#pl-edit-image')).toBeVisible();
        await page.waitForFunction(() => document.getElementById('pl-edit-image').naturalWidth > 0);
        await shot('before');
        const beforeUrl = await page.locator('#pl-edit-image').getAttribute('src');
        const count = mutations.length;
        for (const [name, mimeType, buffer] of [
            ['invalid.svg', 'image/svg+xml', Buffer.from('<svg/>')],
            ['large.png', 'image/png', Buffer.alloc(2 * 1024 * 1024 + 1)]
        ]) {
            await page.locator('#pl-image-file').setInputFiles({ name, mimeType, buffer });
            await expect(page.locator('#pl-image-message')).toHaveClass(/is-error/);
            assert.equal(await page.locator('#pl-edit-image').getAttribute('src'), beforeUrl);
        }
        assert.equal(mutations.length, count);
        for (const type of ['jpg', 'webp', 'png']) {
            const chooser = page.waitForEvent('filechooser');
            await page.locator('#pl-image-change').click();
            await (await chooser).setFiles(await file(type));
            await expect(page.locator('#pl-edit-image')).toHaveAttribute('src', /^blob:/);
            await expect(page.locator('#pl-image-message')).not.toHaveClass(/is-error/);
            if (type === 'png') await shot('preview');
            if (!reference) {
                const saved = page.waitForResponse(r => r.url().endsWith(`/products/${productId}/image`) && r.request().method() === 'POST');
                await page.locator('#pl-action').click();
                assert.equal((await saved).status(), 200);
                await expect(page.locator('#pl-dialog')).toBeHidden();
                await page.reload({ waitUntil: 'networkidle' });
                await open();
                const product = await apiProduct();
                assert(product.image_url.endsWith(`.${type}`));
                await expect(page.locator('#pl-edit-image')).toHaveAttribute('src', product.image_url);
                await expect(page.locator('#ix-catalog-body img').first()).toHaveAttribute('src', product.image_url);
                assert.equal(product.current_stock, original.current_stock);
            }
        }
        if (!reference) await shot('after-upload');
        page.once('dialog', dialog => dialog.dismiss());
        await page.locator('#pl-image-delete').click();
        await expect(page.locator('#pl-edit-image')).toBeVisible();
        const beforeDelete = reference ? null : await apiProduct();
        await page.locator('#pl-field-name').fill('Cambio pendiente conservado');
        page.once('dialog', dialog => dialog.accept());
        await page.locator('#pl-image-delete').click();
        await expect(page.locator('#pl-edit-placeholder')).toBeVisible();
        await expect(page.locator('#pl-image-delete')).toBeDisabled();
        await expect(page.locator('#pl-field-name')).toHaveValue('Cambio pendiente conservado');
        await shot('after-delete');
        if (!reference) {
            const afterDelete = await apiProduct();
            assert.equal(afterDelete.image_url, null);
            for (const key of ['name', 'code', 'description', 'sale_price', 'purchase_price', 'current_stock', 'minimum_stock']) {
                assert.deepEqual(afterDelete[key], beforeDelete[key]);
            }
        }
        if (reference) {
            await page.locator('#pl-action').click();
            await expect(page.locator('#pl-dialog')).toBeHidden();
            await page.reload({ waitUntil: 'networkidle' });
            await open();
            assert.equal(await page.locator('#pl-edit-image').getAttribute('src'), beforeUrl);
            assert.equal(mutations.length, 0);
        }
        await page.setViewportSize({ width: 390, height: 844 });
        await shot('mobile');
        assert(await page.locator('#pl-dialog').evaluate(el => el.scrollWidth <= el.clientWidth));
        assert.deepEqual(errors, []);
        console.log(`PASS: ${reference ? 'REFERENCE zero mutation requests' : 'LIVE PNG/JPG/WebP uploads, persistence, delete, unchanged stock'}, 1600x900 DPR1 zoom100%, current image, local preview, SVG and >2MB rejection, confirmation cancel, responsive; ${output}`);
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
