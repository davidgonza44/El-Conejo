// Ciclo de compra completo con cuatro roles: inventario solicita reposición, el asistente cotiza
// y prepara la orden, el administrador la aprueba e inventario recibe la mercancía. Cada paso se
// hace con la API real (misma que usa la interfaz) y se verifica en las pantallas.
import { test, expect, as, api } from '../support/fixtures.mjs';
import { authFile } from '../support/users.mjs';

const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Caracas' }).format(new Date());
const flow = {};

test.describe.configure({ mode: 'serial' });

async function rolePage(browser, role) {
    const context = await browser.newContext({ storageState: authFile(role) });
    return context.newPage();
}

async function productByCode(client, code) {
    const { body } = await client.get(`/api/products?search=${code}&page=1&per_page=5`);
    return body.items.find((row) => row.code === code);
}

test('inventario solicita reposición de un producto bajo el mínimo', async ({ browser }) => {
    const page = await rolePage(browser, 'inventario');
    const client = await api(page, '/purchases');
    const product = await productByCode(client, 'E2E-003');
    flow.productId = product.id;
    flow.stockBefore = product.current_stock;
    const created = await client.post('/api/replenishment-requests', { items: [{ product_id: product.id, quantity: 20 }] });
    expect(created.status, JSON.stringify(created.body)).toBe(201);
    flow.request = created.body;

    await page.goto('/purchases/requests');
    await expect(page.locator('main table')).toContainText(flow.request.code || 'SR-');
    await page.close();
});

test('inventario no puede crear órdenes de compra', async ({ browser }) => {
    const page = await rolePage(browser, 'inventario');
    const client = await api(page, '/purchases');
    const response = await client.post('/api/purchases', { supplier_id: 1, items: [] });
    expect(response.status).toBe(403);
    await page.close();
});

test('el asistente cotiza y prepara la orden en borrador', async ({ browser }) => {
    const page = await rolePage(browser, 'asistente');
    const client = await api(page, '/purchases');
    expect((await client.post(`/api/replenishment-requests/${flow.request.id}/start`)).status).toBe(200);
    const supplier = (await client.get('/api/purchases/options')).body;
    const supplierId = (supplier.suppliers || supplier.items || []).find((s) => /E2E Proveedor 1$/.test(s.name))?.id ?? 1;
    const quote = await client.post(`/api/replenishment-requests/${flow.request.id}/quotes`, {
        supplier_id: supplierId, currency: 'USD', issued_on: today, valid_until: '2099-01-01',
        items: flow.request.items.map((item) => ({ product_id: item.product_id, unit_price: '8.75' })),
    });
    expect(quote.status, JSON.stringify(quote.body)).toBe(201);
    const prepared = await client.post(`/api/replenishment-requests/${flow.request.id}/prepare`, { supplier_quote_id: quote.body.id });
    expect(prepared.status, JSON.stringify(prepared.body)).toBe(200);
    flow.orderId = prepared.body.purchase_order.id;

    const order = (await client.get(`/api/purchases/${flow.orderId}`)).body;
    expect(order.status_label).toBe('Borrador pendiente de aprobación');
    flow.order = order;

    const approve = await client.post(`/api/purchases/${flow.orderId}/approve`);
    expect(approve.status, 'quien prepara no aprueba').toBe(403);

    await page.goto(`/purchases/${flow.orderId}/print`);
    await expect(page.locator('body')).toContainText('BORRADOR PENDIENTE DE APROBACIÓN');
    await page.close();
});

test('el administrador aprueba la orden y la impresión deja de ser borrador', async ({ browser }) => {
    const page = await rolePage(browser, 'admin');
    const client = await api(page, '/purchases');
    const approved = await client.post(`/api/purchases/${flow.orderId}/approve`);
    expect(approved.status, JSON.stringify(approved.body)).toBe(200);

    await page.goto(`/purchases/${flow.orderId}/print`);
    await expect(page.locator('body')).not.toContainText('BORRADOR');
    await expect(page.locator('body')).toContainText('Aprobada por:');
    await page.close();
});

test('inventario recibe la mercancía y el stock aumenta con su movimiento', async ({ browser }) => {
    const page = await rolePage(browser, 'inventario');
    const client = await api(page, '/purchases');
    const order = (await client.get(`/api/purchases/${flow.orderId}`)).body;
    const received = await client.post(`/api/purchases/${flow.orderId}/receptions`, {
        received_at: today, items: order.items.map((line) => ({ line_id: line.id, quantity: line.quantity_ordered - line.quantity_received })),
    });
    expect(received.status, JSON.stringify(received.body)).toBe(200);
    expect(received.body.status_label).toBe('Cerrada completa');

    const product = await productByCode(client, 'E2E-003');
    expect(product.current_stock).toBe(flow.stockBefore + 20);

    await page.goto('/purchases/receptions');
    await expect(page.locator('main table')).toContainText(order.order_number);
    await page.goto('/inventory');
    await page.getByPlaceholder('Buscar por producto, código o motivo...').fill('Cemento gris');
    await expect(page.locator('main table')).toContainText('Cemento gris 42.5 kg');
    await page.close();
});

test.describe('Pantallas de compras', () => {
    test.use(as('admin'));

    test('filtra órdenes por estado y exporta CSV', async ({ page }) => {
        await page.goto('/purchases?status=closed_complete&start=&end=');
        await expect(page.locator('select[name="status"]')).toHaveValue('closed_complete');
        await expect(page.locator('main table')).toContainText('OC-');
        await page.goto('/purchases?status=rejected&start=&end=');
        await expect(page.locator('main table')).not.toContainText('OC-000001');
        const csv = await page.request.get('/api/purchases/export.csv');
        expect(csv.status()).toBe(200);
        expect(csv.headers()['content-type']).toContain('text/csv');
    });
});

test.describe('Recepciones sin permiso de costos (inventario)', () => {
    test.use(as('inventario'));

    test('no puede filtrar ni ordenar por monto para deducir precios', async ({ page }) => {
        await page.goto('/purchases/receptions');
        await expect(page.locator('input[name="min_amount"]')).toHaveCount(0);
        await expect(page.locator('select[name="sort"] option[value^="amount"]')).toHaveCount(0);
        for (const query of ['min_amount=1', 'max_amount=100', 'sort=amount_desc']) {
            const response = await page.request.get(`/api/purchases/receptions?${query}`);
            expect(response.status(), query).toBe(403);
        }
    });
});

test.describe('Indicadores de órdenes completadas', () => {
    test.use(as('admin'));

    test('Compras y Proveedores cuentan igual las órdenes completadas', async ({ page }) => {
        const purchases = await (await page.request.get('/api/purchases?start=&end=')).json();
        const suppliers = await (await page.request.get('/api/suppliers')).json();
        const fromPurchases = purchases.kpis.completed;
        const fromSuppliers = (suppliers.kpis || suppliers.summary || suppliers).completed;
        expect(fromPurchases).toBeGreaterThanOrEqual(1);
        expect(fromSuppliers).toBe(fromPurchases);
    });
});
