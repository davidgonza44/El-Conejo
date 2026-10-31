// Notas de entrega (vendedor emite, administrador cancela y el stock vuelve) y clientes.
import { test, expect, as, api } from '../support/fixtures.mjs';
import { uid } from '../support/data.mjs';

const customer = `E2E Cliente Mostrador ${uid()}`;

async function stockOf(page, code) {
    const response = await page.request.get(`/api/products?search=${code}&page=1&per_page=5`);
    return (await response.json()).items.find((row) => row.code === code).current_stock;
}

test.describe.configure({ mode: 'serial' });

test.describe('Emisión (vendedor)', () => {
    test.use(as('vendedor'));

    test('emite una nota con dos líneas y descuenta el stock', async ({ page }) => {
        await page.goto('/delivery-notes');
        const before = await stockOf(page, 'E2E-005');
        await page.locator('#btn-new-note').click();
        const modal = page.locator('#create-modal');
        await expect(modal).toBeVisible();
        await modal.getByLabel('Nombre del cliente').fill(customer);
        await modal.getByLabel('Documento').fill('V-12345678');
        for (const [code, quantity] of [['E2E-005', '2'], ['E2E-001', '1']]) {
            const value = await modal.locator('#c-product option', { hasText: `${code} — ` }).getAttribute('value');
            await modal.locator('#c-product').selectOption(value);
            await modal.locator('#c-quantity').fill(quantity);
            await modal.locator('#btn-add-line').click();
        }
        await expect(modal.locator('#create-lines-tbody tr')).toHaveCount(2);
        // Primer paso: revisión con cliente, líneas, total y aviso de salida de inventario.
        await modal.locator('#create-save-btn').click();
        const review = modal.locator('#create-confirm');
        await expect(review).toBeVisible();
        await expect(review).toContainText(customer);
        await expect(review).toContainText('E2E-005');
        await expect(review).toContainText('salida de inventario');
        expect(await stockOf(page, 'E2E-005'), 'revisar no descuenta stock').toBe(before);
        await expect(modal.locator('#create-save-btn')).toHaveText(/Confirmar emisión/);
        await modal.locator('#create-save-btn').click();
        await expect(modal).toBeHidden();
        await expect(page.locator('#notes-tbody')).toContainText(customer);
        expect(await stockOf(page, 'E2E-005')).toBe(before - 2);
    });

    test('rechaza documento y teléfono con formato inválido', async ({ page }) => {
        const client = await api(page, '/delivery-notes');
        const product = (await client.get('/api/products?search=E2E-006&page=1&per_page=5')).body.items[0];
        const base = { customer_name: 'E2E Formato',
            items: [{ product_id: product.id, quantity: 1, expected_unit_price: Number(product.sale_price).toFixed(2) }] };
        for (const extra of [{ customer_phone: 'llamar-luego' }, { customer_document: '<script>' }, { customer_name: 'x'.repeat(151) }]) {
            const response = await client.post('/api/delivery-notes', { ...base, ...extra });
            expect(response.status, JSON.stringify(extra)).toBe(400);
        }
    });

    test('un precio desactualizado exige confirmar el importe vigente', async ({ page }) => {
        const client = await api(page, '/delivery-notes');
        const product = (await client.get('/api/products?search=E2E-006&page=1&per_page=5')).body.items[0];
        const before = product.current_stock;
        const stale = await client.post('/api/delivery-notes', { customer_name: 'E2E Precio',
            items: [{ product_id: product.id, quantity: 1, expected_unit_price: '0.01' }] });
        expect(stale.status).toBe(409);
        expect(stale.body.price_changes[0]).toMatchObject({ code: 'E2E-006', shown: '0.01' });
        expect(Number(stale.body.price_changes[0].current)).toBe(Number(product.sale_price));
        expect(await stockOf(page, 'E2E-006'), 'no se emite ni descuenta').toBe(before);
    });

    test('rechaza escrituras sin token CSRF', async ({ page }) => {
        await page.goto('/delivery-notes');
        const response = await page.request.post('/api/delivery-notes', { data: { customer_name: 'Sin token', items: [] } });
        expect(response.status()).toBe(400);
        expect((await response.json()).error).toContain('CSRF');
    });

    test('no ofrece productos sin existencia', async ({ page }) => {
        await page.goto('/delivery-notes');
        await page.locator('#btn-new-note').click();
        await expect(page.locator('#c-product option', { hasText: 'Arena lavada m3' })).toHaveCount(0);
    });

    test('enlaza la nota con un cliente del directorio y la muestra en su historial', async ({ page }) => {
        await page.goto('/delivery-notes');
        await page.locator('#btn-new-note').click();
        const modal = page.locator('#create-modal');
        await modal.locator('#c-customer-search').fill('Constructora Prueba');
        await modal.getByRole('option', { name: /E2E Constructora Prueba/ }).click();
        await expect(modal.locator('#c-customer-linked')).toContainText('E2E Constructora Prueba');
        await expect(modal.getByLabel('Nombre del cliente')).toHaveValue('E2E Constructora Prueba');
        await expect(modal.getByLabel('Nombre del cliente')).not.toBeEditable();
        const value = await modal.locator('#c-product option', { hasText: 'E2E-006 — ' }).getAttribute('value');
        await modal.locator('#c-product').selectOption(value);
        await modal.locator('#btn-add-line').click();
        await modal.locator('#create-save-btn').click();
        await modal.locator('#create-save-btn').click();
        await expect(modal).toBeHidden();

        await page.goto('/sales/customers');
        await page.locator('#cu-search').fill('E2E Constructora Prueba');
        const row = page.locator('#cu-body tr', { hasText: 'E2E Constructora Prueba' });
        await row.getByRole('link', { name: /Ver notas de entrega/ }).click();
        await expect(page.locator('#f-customer-link')).toContainText('E2E Constructora Prueba');
        await expect(page.locator('#notes-tbody tr')).not.toHaveCount(0);
        for (const cell of await page.locator('#notes-tbody tr td:nth-child(2)').allTextContents()) {
            expect(cell).toBe('E2E Constructora Prueba');
        }
    });

    test('un reintento con la misma clave no emite otra nota', async ({ page }) => {
        const client = await api(page, '/delivery-notes');
        const product = (await client.get('/api/products?search=E2E-006&page=1&per_page=5')).body.items[0];
        const payload = { customer_name: 'E2E Reintento', idempotency_key: `e2e-${uid()}-reintento`,
            items: [{ product_id: product.id, quantity: 1, expected_unit_price: Number(product.sale_price).toFixed(2) }] };
        const first = await client.post('/api/delivery-notes', payload);
        const again = await client.post('/api/delivery-notes', payload);
        expect([first.status, again.status]).toEqual([201, 200]);
        expect(again.body.delivery_note.note_number).toBe(first.body.delivery_note.note_number);
        expect(await stockOf(page, 'E2E-006')).toBe(product.current_stock - 1);
    });

    test('no puede cancelar notas', async ({ page }) => {
        await page.goto('/delivery-notes');
        await page.locator('#notes-tbody tr', { hasText: customer }).getByRole('button', { name: 'Ver detalle' }).click();
        await expect(page.locator('#detail-modal')).toBeVisible();
        await expect(page.locator('#btn-cancel-note')).toBeHidden();
    });
});

test.describe('Cancelación (administrador)', () => {
    test.use(as('admin'));

    test('cancela la nota y devuelve el stock', async ({ page }) => {
        await page.goto('/delivery-notes');
        const before = await stockOf(page, 'E2E-005');
        await page.locator('#notes-tbody tr', { hasText: customer }).getByRole('button', { name: 'Ver detalle' }).click();
        const detail = page.locator('#detail-modal');
        await expect(detail.locator('#d-customer-name')).toHaveText(customer);
        // Primer clic pide el motivo; sin motivo no cancela.
        await detail.locator('#btn-cancel-note').click();
        await expect(detail.locator('#d-cancel-reason')).toBeVisible();
        await detail.locator('#btn-cancel-note').click();
        await expect(detail.locator('#detail-error')).toContainText('motivo');
        expect(await stockOf(page, 'E2E-005'), 'sin motivo no devuelve stock').toBe(before);
        await detail.locator('#d-cancel-reason').fill('El cliente no retiró la mercancía');
        await detail.locator('#btn-cancel-note').click();
        await expect(detail).toBeHidden();
        expect(await stockOf(page, 'E2E-005')).toBe(before + 2);
        await page.locator('#notes-tbody tr', { hasText: customer }).getByRole('button', { name: 'Ver detalle' }).click();
        await expect(page.locator('#detail-cancelled-alert')).toBeVisible();
        await expect(page.locator('#d-cancel-reason-text')).toHaveText('El cliente no retiró la mercancía');
    });

    test('la API exige el motivo de cancelación', async ({ page }) => {
        const client = await api(page, '/delivery-notes');
        const issued = (await client.get('/api/delivery-notes?status=issued&page=1&per_page=1')).body.items[0];
        expect((await client.post(`/api/delivery-notes/${issued.id}/cancel`, {})).status).toBe(400);
    });

    test('descarga el PDF de una nota', async ({ page }) => {
        await page.goto('/delivery-notes');
        await page.locator('#notes-tbody tr').first().getByRole('button', { name: 'Ver detalle' }).click();
        const download = page.waitForEvent('download');
        await page.locator('#btn-download-pdf').click();
        expect((await download).suggestedFilename()).toMatch(/\.pdf$/);
    });
});

test.describe('Clientes (vendedor)', () => {
    test.use(as('vendedor'));
    const name = `E2E Ferretería Cliente ${uid()}`;

    test('registra un cliente y lo encuentra en la búsqueda', async ({ page }) => {
        await page.goto('/sales/customers');
        await page.locator('#cu-new').click();
        const drawer = page.locator('#cu-drawer');
        await expect(drawer).toBeVisible();
        await drawer.getByLabel('Nombre / Razón Social').fill(name);
        await drawer.getByLabel('Correo').fill('cliente.e2e@example.invalid');
        await drawer.getByRole('button', { name: 'Guardar cliente' }).click();
        await expect(drawer).toBeHidden();
        await page.locator('#cu-search').fill(name);
        await expect(page.locator('#cu-body')).toContainText(name);
    });

    test('rechaza un correo inválido', async ({ page }) => {
        await page.goto('/sales/customers');
        await page.locator('#cu-new').click();
        const drawer = page.locator('#cu-drawer');
        await drawer.getByLabel('Nombre / Razón Social').fill(`E2E Cliente inválido ${uid()}`);
        await drawer.getByLabel('Correo').fill('no-es-un-correo');
        await drawer.getByRole('button', { name: 'Guardar cliente' }).click();
        await expect(drawer).toBeVisible();
    });
});
