// Movimientos de inventario: entradas y salidas registradas desde la interfaz, control de
// disponibilidad, solicitud de ajuste (sin tocar stock) y trazabilidad en el listado.
import { test, expect, as, api } from '../support/fixtures.mjs';

async function stockOf(page, code) {
    const response = await page.request.get(`/api/products?search=${encodeURIComponent(code)}&page=1&per_page=5`);
    expect(response.ok()).toBeTruthy();
    const item = (await response.json()).items.find((row) => row.code === code);
    return item.current_stock;
}

async function openNewMovement(page, kind, productQuery, code) {
    await page.goto('/inventory');
    await page.getByRole('button', { name: 'Nuevo movimiento' }).click();
    const form = page.locator('#mv-drawer form[data-mv-form]');
    await expect(form).toBeVisible();
    await form.locator('#mv-kind').selectOption(kind);
    await form.getByRole('searchbox', { name: 'Buscar producto' }).fill(productQuery);
    // La búsqueda recarga la lista con debounce: se espera a que quede solo la opción filtrada.
    await expect(form.locator('#mv-product option')).toHaveCount(2);
    await form.locator('#mv-product').selectOption({ label: `${productQuery} (${code})` });
    return form;
}

test.describe('Movimientos (rol inventario)', () => {
    test.use(as('inventario'));

    test('una entrada suma stock y deja el movimiento trazado', async ({ page }) => {
        const before = await stockOf(page, 'E2E-006');
        const form = await openNewMovement(page, 'entrada', 'Interruptor sencillo', 'E2E-006');
        await expect(form.locator('#mv-stock')).toHaveValue(String(before));
        await form.locator('#mv-quantity').fill('7');
        await form.locator('#mv-notes').fill('Entrada E2E desde la interfaz');
        await form.getByRole('button', { name: 'Guardar movimiento' }).click();
        await expect(form).toBeHidden();
        await expect(page.getByText(/registrado\. El stock se actualizó correctamente/)).toBeVisible();
        expect(await stockOf(page, 'E2E-006')).toBe(before + 7);
        await page.getByPlaceholder('Buscar por producto, código o motivo...').fill('Entrada E2E desde la interfaz');
        await expect(page.locator('main table')).toContainText('Interruptor sencillo');
    });

    test('una salida resta stock', async ({ page }) => {
        const before = await stockOf(page, 'E2E-008');
        const form = await openNewMovement(page, 'salida', 'Nivel de burbuja 24 in', 'E2E-008');
        await form.locator('#mv-quantity').fill('2');
        await form.locator('#mv-notes').fill('Salida E2E desde la interfaz');
        await form.getByRole('button', { name: 'Guardar movimiento' }).click();
        await expect(form).toBeHidden();
        expect(await stockOf(page, 'E2E-008')).toBe(before - 2);
    });

    test('no permite sacar más unidades de las disponibles', async ({ page }) => {
        const form = await openNewMovement(page, 'salida', 'Arena lavada m3', 'E2E-004');
        await form.locator('#mv-quantity').fill('1');
        await form.locator('#mv-notes').fill('Salida imposible E2E');
        await form.getByRole('button', { name: 'Guardar movimiento' }).click();
        await expect(form.locator('[data-live-error]')).toContainText(/stock|disponible|insuficiente/i);
        expect(await stockOf(page, 'E2E-004')).toBe(0);
    });

    test('una solicitud de ajuste no modifica el stock hasta su aprobación', async ({ page }) => {
        const before = await stockOf(page, 'E2E-002');
        const form = await openNewMovement(page, 'ajuste', 'Destornillador plano 1/4', 'E2E-002');
        await form.locator('#mv-quantity').fill(String(before + 4));
        await form.locator('#mv-notes').fill('Conteo físico E2E');
        await form.getByRole('button', { name: /Solicitar ajuste/ }).click();
        await expect(page.getByText(/pendiente de aprobación\. El stock permanece intacto/)).toBeVisible();
        expect(await stockOf(page, 'E2E-002')).toBe(before);
    });

    test('valida identificadores y límites antes de tocar el stock', async ({ page }) => {
        const client = await api(page, '/purchases');
        const id = (await client.get('/api/products?search=E2E-006&page=1&per_page=5')).body.items[0].id;
        const before = await stockOf(page, 'E2E-006');
        for (const productId of [`${id}.0`, id + 0.5, true, -id, 'abc']) {
            const response = await client.post('/api/inventory/entry', { product_id: productId, quantity: 1, reason: 'E2E' });
            expect(response.status, String(productId)).toBe(400);
        }
        const huge = await client.post('/api/inventory/entry', { product_id: id, quantity: 99999999999, reason: 'E2E' });
        expect(huge.status, 'una cantidad fuera de rango no es un error 500').toBe(400);
        const nearMax = await client.post('/api/inventory/entry', { product_id: id, quantity: 2147483600, reason: 'E2E' });
        expect(nearMax.status, 'el stock resultante no puede desbordar la columna').toBe(400);
        expect(await stockOf(page, 'E2E-006')).toBe(before);
    });

    test('rechaza movimientos sin token CSRF', async ({ page }) => {
        await page.goto('/inventory');
        const response = await page.request.post('/api/inventory/entry', { data: { product_id: 1, quantity: 1 } });
        expect(response.status()).toBe(400);
    });

    test('el error de stock insuficiente queda junto a Cantidad', async ({ page }) => {
        const form = await openNewMovement(page, 'salida', 'Arena lavada m3', 'E2E-004');
        await form.locator('#mv-quantity').fill('5');
        await form.locator('#mv-notes').fill('Salida imposible E2E');
        await form.getByRole('button', { name: 'Guardar movimiento' }).click();
        const error = form.locator('#mv-live-error');
        await expect(error).toBeVisible();
        await expect(error).toBeInViewport();
        await expect(form.locator('#mv-quantity')).toHaveAttribute('aria-describedby', 'mv-live-error');
    });

    test('exporta e imprime el listado filtrado', async ({ page }) => {
        const response = await page.goto('/inventory/print');
        expect(response.status()).toBe(200);
        await expect(page.locator('table')).toContainText(/Martillo|Interruptor|Destornillador/);
    });
});

test.describe('Resumen de inventario', () => {
    test.use(as('admin'));

    test('muestra indicadores y alertas calculados desde la base', async ({ page }) => {
        await page.goto('/inventory/summary');
        await expect(page.getByRole('region', { name: 'Indicadores' })).toBeVisible();
        await expect(page.locator('main')).toContainText(/Arena lavada m3|Cemento gris/);
    });
});
