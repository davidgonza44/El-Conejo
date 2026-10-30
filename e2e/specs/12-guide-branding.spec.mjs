// Guía de uso por rol, membrete con logo y RIF en impresiones/exportaciones, y pantallas que
// pasaron del menú antiguo al shell moderno sin perder acceso.
import { test, expect, as } from '../support/fixtures.mjs';

test.describe('Guía de uso', () => {
    test.use(as('admin'));

    test('se abre desde el menú y muestra todas las secciones del administrador', async ({ page }) => {
        await page.goto('/dashboard');
        await page.getByRole('complementary', { name: 'Navegación principal' }).getByRole('link', { name: 'Guía de uso' }).click();
        await expect(page).toHaveURL(/\/ayuda$/);
        await expect(page.getByRole('heading', { level: 1 })).toHaveText('Guía de uso');
        const toc = page.getByRole('navigation', { name: 'Contenido de la guía' });
        for (const name of ['Primeros pasos', 'Compras', 'Configuración', 'Preguntas frecuentes']) {
            await expect(toc.getByRole('link', { name: new RegExp(name) })).toBeVisible();
        }
    });

    test('la búsqueda filtra las secciones', async ({ page }) => {
        await page.goto('/ayuda');
        await page.getByRole('searchbox', { name: 'Buscar en la guía' }).fill('recepción');
        await expect(page.locator('#compras')).toBeVisible();
        await expect(page.locator('#asistente')).toBeHidden();
        await page.getByRole('searchbox', { name: 'Buscar en la guía' }).fill('zzzz-sin-coincidencias');
        await expect(page.locator('#hp-empty')).toBeVisible();
    });
});

for (const [role, hidden, shown] of [['vendedor', ['#compras', '#configuracion', '#reportes'], ['#notas', '#productos']],
    ['soporte_ti', ['#productos', '#compras', '#notas'], ['#configuracion']]]) {
    test.describe(`Guía de uso (${role})`, () => {
        test.use(as(role));

        test('solo muestra los módulos que el rol puede usar', async ({ page }) => {
            expect((await page.goto('/ayuda')).status()).toBe(200);
            for (const id of hidden) await expect(page.locator(id)).toHaveCount(0);
            for (const id of shown) await expect(page.locator(id)).toBeVisible();
        });
    });
}

test.describe('Membrete de la empresa', () => {
    test.use(as('admin'));
    test.describe.configure({ mode: 'serial' });

    test('el RIF se valida y se guarda en Configuración › General', async ({ page }) => {
        await page.goto('/settings?tab=general');
        const rif = page.getByLabel('RIF de la empresa');
        await rif.fill('j-30123456-7');
        await page.locator('#st-save').click();
        await expect(page.locator('.st-toast')).toBeVisible();
        await page.reload();
        await expect(rif).toHaveValue('J-30123456-7');
        await rif.fill('ABC-123');
        await page.locator('#st-save').click();
        await expect(page.locator('.st-toast')).toContainText('J-12345678-9');
        await page.reload();
        await expect(rif).toHaveValue('J-30123456-7');
    });

    test('las páginas imprimibles muestran logo, empresa y RIF', async ({ page }) => {
        await page.goto('/inventory/print');
        const letterhead = page.locator('.print-letterhead');
        await expect(letterhead).toContainText('Ferretería y Construcciones El Conejo C.A.');
        await expect(letterhead).toContainText('RIF: J-30123456-7');
        await expect(letterhead.locator('img')).toHaveJSProperty('complete', true);
        expect(await letterhead.locator('img').evaluate((img) => img.naturalWidth)).toBeGreaterThan(0);
    });

    test('el Excel exportado incluye el membrete antes de la tabla', async ({ page }) => {
        const response = await page.request.get('/api/categories/export?format=xlsx');
        expect(response.status()).toBe(200);
        expect(response.headers()['content-type']).toContain('spreadsheetml');
        const pdf = await page.request.get('/api/inventory/movements/export?format=pdf');
        expect(pdf.status()).toBe(200);
        expect((await pdf.body()).subarray(0, 4).toString()).toBe('%PDF');
    });
});

test.describe('Pantallas del menú antiguo dentro del shell moderno', () => {
    test.use(as('admin'));

    for (const [path, group, link] of [['/catalog', 'Inventario', 'Catálogo visual'],
        ['/historical-imports', 'Reportes y análisis', 'Importación histórica'],
        ['/predictions', 'Reportes y análisis', 'Diagnóstico de historial']]) {
        test(`${path} usa el menú moderno y queda marcado como activo`, async ({ page }) => {
            await page.goto(path);
            const nav = page.getByRole('complementary', { name: 'Navegación principal' });
            await expect(nav.getByRole('button', { name: group })).toHaveAttribute('aria-expanded', 'true');
            await expect(nav.getByRole('link', { name: link })).toHaveAttribute('aria-current', 'page');
            await expect(page.locator('#ix-search-input')).toBeVisible();
        });
    }
});
