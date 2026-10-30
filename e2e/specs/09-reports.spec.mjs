// Reportes de ventas: pestañas, período, exportación CSV/PDF, histórico y vista física para
// roles sin acceso a precios. También cubre la página de error HTML ante parámetros inválidos.
import { test, expect, as } from '../support/fixtures.mjs';

const TABS = ['Resumen', 'Ventas por producto', 'Ventas por cliente', 'Ventas por categoría', 'Métodos de pago'];

test.describe('Reporte de ventas (administrador)', () => {
    test.use(as('admin'));

    test('recorre las cinco pestañas con indicadores', async ({ page }) => {
        await page.goto('/reports/sales');
        const tabs = page.getByRole('navigation', { name: 'Secciones del reporte' });
        await expect(tabs.getByRole('link')).toHaveText(TABS);
        for (const name of TABS) {
            await tabs.getByRole('link', { name }).click();
            await expect(tabs.getByRole('link', { name })).toHaveAttribute('aria-current', 'page');
            await expect(page.getByRole('region', { name: 'Indicadores del período' }).locator('article')).toHaveCount(4);
        }
    });

    test('aplica un período desde el formulario', async ({ page }) => {
        await page.goto('/reports/sales?tab=products');
        const toggle = page.locator('[data-rs-period-toggle]');
        await toggle.click();
        await expect(toggle).toHaveAttribute('aria-expanded', 'true');
        const form = page.locator('#rs-period');
        await form.getByLabel('Desde').fill('2026-09-01');
        await form.getByLabel('Hasta').fill('2026-09-30');
        await form.getByRole('button', { name: 'Aplicar' }).click();
        await expect(page).toHaveURL(/tab=products.*start=2026-09-01.*end=2026-09-30/);
        await expect(page.locator('[data-rs-period-toggle]')).toContainText('01 sept. 2026 - 30 sept. 2026');
    });

    for (const [button, ext, type] of [['Exportar CSV', 'csv', 'text/csv'], ['Exportar PDF', 'pdf', 'application/pdf']]) {
        test(`descarga el ${ext.toUpperCase()} tras confirmar en el diálogo`, async ({ page }) => {
            await page.goto('/reports/sales?tab=categories');
            await page.getByRole('button', { name: button }).click();
            const dialog = page.locator(`#rs-export-${ext}`);
            await expect(dialog).toBeVisible();
            await expect(dialog).toContainText('Ventas por categoría');
            const download = page.waitForEvent('download');
            await dialog.locator(`[data-rs-confirm=${ext}]`).click();
            expect((await download).suggestedFilename()).toMatch(new RegExp(`\\.${ext}$`));
            await expect(dialog).toBeHidden();
            const direct = await page.request.get(`/api/reports/sales/export?tab=categories&format=${ext}`);
            expect(direct.headers()['content-type']).toContain(type);
        });
    }

    test('el diálogo de exportación se cancela sin descargar', async ({ page }) => {
        await page.goto('/reports/sales');
        await page.getByRole('button', { name: 'Exportar CSV' }).click();
        await page.locator('#rs-export-csv').getByRole('button', { name: 'Cancelar' }).click();
        await expect(page.locator('#rs-export-csv')).toBeHidden();
    });

    test('"Ver todo" abre el histórico con regreso al reporte', async ({ page }) => {
        await page.goto('/reports/sales?tab=products');
        await page.getByRole('link', { name: 'Ver todo' }).click();
        await expect(page).toHaveURL(/\/reports\/sales\/history\?/);
        await expect(page.getByRole('region', { name: 'Totales del período' })).toBeVisible();
        await page.getByRole('link', { name: /Volver al reporte/ }).click();
        await expect(page).toHaveURL(/\/reports\/sales\?tab=products/);
    });

    test('un período inválido muestra una página de error, no JSON', async ({ page }) => {
        const response = await page.goto('/reports/sales?start=2026-10-10&end=2026-10-01');
        expect(response.status()).toBe(400);
        await expect(page.getByRole('heading', { level: 1 })).toHaveText('No se pudo mostrar la página');
        await expect(page.getByText('La fecha inicial no puede superar la final.')).toBeVisible();
        await page.getByRole('link', { name: 'Ir al inicio' }).click();
        await expect(page).toHaveURL(/\/dashboard$/);
    });

    test('una ruta inexistente muestra la página 404', async ({ page }) => {
        const response = await page.goto('/no-existe-e2e');
        expect(response.status()).toBe(404);
        await expect(page.getByRole('heading', { level: 1 })).toHaveText('Página no encontrada');
    });

    test('advierte que los importes no tienen moneda acreditada y no usa el símbolo $', async ({ page }) => {
        await page.goto('/reports/sales?tab=categories');
        const notes = page.getByRole('list', { name: 'Notas sobre los datos' });
        await expect(notes).toContainText('sin convertir ni asumir USD');
        await expect(notes).toContainText('categoría actual');
        await expect(page.getByRole('region', { name: 'Indicadores del período' })).not.toContainText('$');
    });

    test('"Ver detalle" de categoría filtra por su id exacto', async ({ page }) => {
        const categories = await (await page.request.get('/api/categories')).json();
        const target = (categories.items || categories).find((c) => c.name === 'E2E Herramientas');
        await page.goto(`/reports/sales?tab=products&category=${target.id}`);
        await expect(page.locator('.rs-filter')).toHaveText(/Categoría: E2E Herramientas/);
        expect((await page.goto('/reports/sales?tab=products&category=999999')).status()).toBe(404);
        expect((await page.goto('/reports/sales?tab=products&category=abc')).status()).toBe(400);
    });

    test('Métodos de pago usa el ticket promedio por venta y "Ventas registradas"', async ({ page }) => {
        await page.goto('/reports/sales?tab=payments');
        const kpis = page.getByRole('region', { name: 'Indicadores del período' });
        await expect(kpis).toContainText('Ventas registradas');
        await expect(kpis).toContainText('Ticket promedio por venta');
        await expect(kpis).not.toContainText('Ingresos cobrados');
    });

    test('la API conserva sus errores en JSON', async ({ page }) => {
        const response = await page.request.get('/api/no-existe-e2e');
        expect(response.status()).toBe(404);
        expect(await response.json()).toEqual({ error: 'Ruta no encontrada.' });
    });
});

test.describe('Reporte físico (inventario, sin precios)', () => {
    test.use(as('inventario'));

    test('muestra conteos sin montos', async ({ page }) => {
        await page.goto('/reports/sales?tab=products');
        const kpis = page.getByRole('region', { name: 'Indicadores del período' });
        await expect(kpis).toContainText('Conteo físico del período');
        await expect(page.locator('main')).not.toContainText('$');
    });
});

test.describe('Página de acceso restringido', () => {
    test.use(as('vendedor'));

    test('ofrece volver al inicio del rol y cerrar sesión', async ({ page }) => {
        expect((await page.goto('/reports/sales')).status()).toBe(403);
        await expect(page.getByRole('heading', { level: 1 })).toHaveText('Acceso restringido');
        await expect(page.getByText('No tiene permisos para acceder a esta sección.')).toHaveCount(1);
        await page.getByRole('link', { name: 'Ir al inicio' }).click();
        await expect(page).toHaveURL(/\/products$/);
    });
});
