// Catálogo: el asistente propone un producto desde el cajón "Nuevo producto", el administrador
// lo aprueba y el producto queda activo con su stock inicial registrado como movimiento.
import { test, expect, as } from '../support/fixtures.mjs';
import { uid } from '../support/data.mjs';

const product = { name: `Llave ajustable E2E ${uid()}`, code: `E2E-LL-${uid()}`, stock: '15' };

test.describe.configure({ mode: 'serial' });

test.describe('Propuesta del asistente', () => {
    test.use(as('asistente'));

    test('valida los campos obligatorios', async ({ page }) => {
        await page.goto('/products');
        await page.getByRole('button', { name: 'Nuevo producto' }).click();
        const drawer = page.locator('#np-drawer');
        await expect(drawer).toBeVisible();
        await drawer.locator('#np-save').click();
        await expect(drawer.locator('#np-name-error')).toBeVisible();
        await expect(drawer).toBeVisible();
    });

    test('propone un producto nuevo', async ({ page }) => {
        await page.goto('/products');
        await page.getByRole('button', { name: 'Nuevo producto' }).click();
        const drawer = page.locator('#np-drawer');
        await drawer.getByLabel('Nombre del producto').fill(product.name);
        await drawer.getByLabel('Código / SKU').fill(product.code);
        await drawer.locator('#np-category').selectOption({ label: 'E2E Herramientas' });
        await drawer.locator('#np-unit').selectOption({ index: 1 });
        await drawer.locator('#np-price').fill('12.50');
        await drawer.locator('#np-price-currency').selectOption('USD');
        await drawer.locator('#np-cost').fill('7.25');
        await drawer.locator('#np-cost-currency').selectOption('USD');
        await drawer.locator('#np-stock').fill(product.stock);
        await drawer.locator('#np-minimum').fill('5');
        await drawer.locator('#np-save').click();
        await expect(drawer).toBeHidden();
        await page.locator('#ix-prod-search').fill(product.code);
        await expect(page.locator('main table')).toContainText(product.name);
    });
});

test.describe('Aprobación del administrador', () => {
    test.use(as('admin'));

    test('el producto aparece como propuesta pendiente en Inicio', async ({ page }) => {
        await page.goto('/dashboard');
        const pending = page.getByRole('link', { name: /Productos pendientes/ });
        await expect(pending.locator('strong')).not.toHaveText('0');
    });

    test('aprueba la propuesta y el producto queda activo', async ({ page }) => {
        await page.goto('/products?approval_status=pending');
        await page.locator('#ix-prod-search').fill(product.code);
        const row = page.locator('main table tbody tr', { hasText: product.name });
        await expect(row).toBeVisible();
        await row.getByRole('button', { name: 'Ver detalle' }).click();
        await page.locator('#pl-approve').click();
        await page.goto('/products');
        await page.locator('#ix-prod-search').fill(product.code);
        await expect(page.locator('main table tbody tr', { hasText: product.name })).toContainText(/Aprobado|Disponible/);
    });

    test('el stock inicial quedó trazado como movimiento', async ({ page }) => {
        await page.goto('/inventory');
        await page.getByPlaceholder('Buscar por producto, código o motivo...').fill(product.name);
        await expect(page.locator('main table')).toContainText(product.name);
        await expect(page.locator('main table')).toContainText(product.stock);
    });
});

test.describe('Lectura del vendedor', () => {
    test.use(as('vendedor'));

    test('ve el catálogo sin acciones de escritura ni costos', async ({ page }) => {
        await page.goto('/products');
        await expect(page.getByRole('button', { name: 'Nuevo producto' })).toHaveCount(0);
        await expect(page.locator('main table')).not.toContainText('Costo');
        const write = await page.request.post('/api/products', { data: { name: 'No permitido' } });
        expect([400, 403]).toContain(write.status());
    });
});
