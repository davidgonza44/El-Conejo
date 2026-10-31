// Separación de funciones en el catálogo: el asistente propone categorías y proveedores,
// el administrador decide. Quien propone no puede aprobar su propia propuesta.
import { test, expect, as, api } from '../support/fixtures.mjs';
import { authFile } from '../support/users.mjs';
import { uid } from '../support/data.mjs';

const category = `E2E Plomería ${uid()}`;
const supplier = { name: `E2E Distribuidora ${uid()}`, rif: `J-${Date.now().toString().slice(-8)}-1` };

test.describe.configure({ mode: 'serial' });

test.describe('Propuestas del asistente', () => {
    test.use(as('asistente'));

    test('propone una categoría que queda pendiente', async ({ page }) => {
        await page.goto('/categories');
        await page.getByRole('button', { name: 'Nueva categoría' }).click();
        const dialog = page.locator('#cl-dialog');
        await expect(dialog).toBeVisible();
        await dialog.getByLabel('Nombre de la categoría').fill(category);
        await dialog.getByLabel('Descripción').fill('Tuberías, conexiones y grifería (E2E)');
        await dialog.locator('[data-cl-action=save]').click();
        await expect(dialog).toBeHidden();
        await page.locator('#ix-cat-search').fill(category);
        await expect(page.locator('main table tbody tr', { hasText: category })).toContainText('Pendiente');
    });

    test('propone un proveedor que queda pendiente', async ({ page }) => {
        await page.goto('/suppliers');
        await page.locator('#sp-new').click();
        const editor = page.locator('#sp-editor');
        await expect(editor).toBeVisible();
        await editor.getByLabel('Nombre o razón social').fill(supplier.name);
        await editor.getByLabel('RIF').fill(supplier.rif);
        await editor.getByLabel('Contacto principal').fill('Contacto E2E');
        await editor.getByLabel('Teléfono').fill('0241-5550000');
        await editor.getByLabel('Correo electrónico').fill('ventas.e2e@example.invalid');
        await editor.getByLabel('Categoría principal').fill('Plomería');
        await editor.getByRole('button', { name: 'Enviar propuesta' }).click();
        await expect(editor).toBeHidden();
        await expect(page.locator('tr', { hasText: supplier.name })).toContainText('Pendiente');
    });

    test('no puede aprobar su propia propuesta', async ({ page }) => {
        await page.goto('/suppliers');
        await page.getByRole('button', { name: `Ver detalle ${supplier.name}` }).click();
        await expect(page.locator('#sp-detail')).toBeVisible();
        await expect(page.locator('#sp-review')).toBeHidden();
    });
});

test.describe('Decisión del administrador', () => {
    test.use(as('admin'));

    test('aprueba la categoría propuesta', async ({ page }) => {
        await page.goto('/categories?approval_status=pending');
        await page.locator('#ix-cat-search').fill(category);
        const row = page.locator('main table tbody tr', { hasText: category });
        await row.getByRole('button', { name: 'Ver detalle' }).click();
        const dialog = page.locator('#cl-dialog');
        await dialog.locator('[data-cl-action=approve]').first().click();
        await expect(page.getByText('Categoría aprobada y activa.')).toBeVisible();
        await page.goto('/categories');
        await page.locator('#ix-cat-search').fill(category);
        await expect(page.locator('main table tbody tr', { hasText: category })).toContainText('Activa');
    });

    test('rechaza el proveedor indicando el motivo', async ({ page }) => {
        await page.goto('/suppliers');
        await page.getByRole('button', { name: `Ver detalle ${supplier.name}` }).click();
        const review = page.locator('#sp-review');
        await expect(review).toBeVisible();
        await review.getByLabel('Motivo de rechazo').fill('Datos fiscales por verificar (E2E)');
        const decided = page.waitForResponse((r) => r.url().endsWith('/reject') && r.request().method() === 'POST');
        await review.getByRole('button', { name: 'Rechazar' }).click();
        expect((await decided).status()).toBe(200);
        await page.waitForLoadState();
        await page.goto('/suppliers');
        await expect(page.locator('tr', { hasText: supplier.name })).toContainText('Rechazado');
    });
});

// Corrección de Compras: modificar un proveedor aprobado crea una propuesta; los datos oficiales
// no cambian hasta que el administrador la aprueba y un rechazo los deja intactos.
test.describe('Modificación de proveedor aprobado', () => {
    async function rolePage(browser, role) {
        const context = await browser.newContext({ storageState: authFile(role) });
        return context.newPage();
    }

    async function supplierTwo(client) {
        const { body } = await client.get('/api/suppliers?search=E2E%20Proveedor%202');
        return (body.items || body.suppliers || []).find((row) => row.name === 'E2E Proveedor 2');
    }

    const proposal = (row, phone) => ({ name: row.name, tax_id: row.tax_id, contact_name: row.contact_name,
        contact_title: row.contact_title || '', phone, email: row.email, address: row.address || '', city: row.city || '',
        state_province: row.state_province || '', category: row.category, notes: row.notes || '', is_active: true });

    test('rechazar la propuesta deja el proveedor intacto; aprobarla aplica el cambio', async ({ browser }) => {
        const asistente = await rolePage(browser, 'asistente');
        const client = await api(asistente, '/suppliers');
        const original = await supplierTwo(client);
        expect(original.approval_status).toBe('approved');

        let sent = await client.patch(`/api/suppliers/${original.id}`, proposal(original, '0241-1112233'));
        expect(sent.status, JSON.stringify(sent.body)).toBe(200);
        let current = (await client.get(`/api/suppliers/${original.id}`)).body;
        expect(current.phone).toBe(original.phone);
        expect(current.is_active).toBe(true);
        expect(current.pending_change.changes).toEqual({ phone: '0241-1112233' });
        expect((await client.post(`/api/suppliers/${original.id}/approve`)).status, 'quien propone no decide').toBe(403);

        const admin = await rolePage(browser, 'admin');
        await admin.goto('/suppliers');
        await admin.getByRole('button', { name: 'Ver detalle E2E Proveedor 2' }).click();
        // El Historial (pestaña oculta) también lista la propuesta pendiente; se revisa el panel visible.
        await expect(admin.locator('.sp-change:visible')).toContainText('0241-1112233');
        await admin.locator('#sp-review-reason').fill('Teléfono no verificado (E2E)');
        const rejected = admin.waitForResponse((r) => r.url().endsWith('/reject'));
        await admin.locator('#sp-reject').click();
        expect((await rejected).status()).toBe(200);
        current = (await client.get(`/api/suppliers/${original.id}`)).body;
        expect(current.phone).toBe(original.phone);
        expect(current.pending_change).toBeNull();

        sent = await client.patch(`/api/suppliers/${original.id}`, proposal(original, '0241-4445566'));
        expect(sent.status).toBe(200);
        // Página propia para la API: tras rechazar, la pantalla navega a ?detail=<id> y cortaría esta navegación.
        const adminClient = await api(await rolePage(browser, 'admin'), '/suppliers');
        expect((await adminClient.post(`/api/suppliers/${original.id}/approve`)).status).toBe(200);
        current = (await client.get(`/api/suppliers/${original.id}`)).body;
        expect(current.phone).toBe('0241-4445566');
        expect(current.is_active).toBe(true);
        await asistente.close();
        await admin.close();
    });
});
