// Menú lateral: cada enlace visible para un rol abre una página autorizada (nunca 403/5xx),
// el elemento activo usa aria-current y la búsqueda global y las alertas responden.
import { test, expect, as } from '../support/fixtures.mjs';
import { ROLES, USERS } from '../support/users.mjs';

const START = { admin: '/dashboard', inventario: '/dashboard', asistente: '/dashboard', vendedor: '/products', soporte_ti: '/settings' };

async function sidebarLinks(page) {
    const nav = page.getByRole('complementary', { name: 'Navegación principal' });
    for (const group of await nav.locator('button[aria-controls^="ix-nav-"]').all()) {
        if (await group.getAttribute('aria-expanded') !== 'true') await group.click();
    }
    // Sin el fragmento (#change-password): un salto dentro de la misma página no es una navegación.
    const hrefs = await nav.locator('a[href^="/"]').evaluateAll((links) => links.map((a) => a.getAttribute('href').split('#')[0]));
    return [...new Set(hrefs)];
}

for (const role of ROLES) {
    test.describe(`Menú de ${role}`, () => {
        test.use(as(role));

        test('todos los enlaces visibles abren páginas autorizadas', async ({ page }) => {
            await page.goto(START[role]);
            const links = await sidebarLinks(page);
            expect(links.length).toBeGreaterThan(0);
            for (const href of links) {
                const response = await page.goto(href);
                expect(response.status(), `${role} → ${href}`).toBe(200);
                await expect(page.locator('h1').first(), href).toBeVisible();
            }
        });

        test('la cuenta muestra el usuario real', async ({ page }) => {
            await page.goto(START[role]);
            await expect(page.locator('#ix-account-toggle')).toContainText(USERS[role].username);
        });
    });
}

test.describe('Navegación del administrador', () => {
    test.use(as('admin'));

    test('marca la página activa con aria-current', async ({ page }) => {
        await page.goto('/reports/sales');
        const nav = page.getByRole('complementary', { name: 'Navegación principal' });
        await expect(nav.getByRole('button', { name: 'Reportes y análisis' })).toHaveAttribute('aria-expanded', 'true');
        await expect(nav.getByRole('link', { name: 'Ventas', exact: true })).toHaveAttribute('aria-current', 'page');
    });

    test('las gráficas viven en Reportes y análisis, no en Inicio', async ({ page }) => {
        await page.goto('/dashboard');
        await expect(page.locator('#chart-sales')).toHaveCount(0);
        await page.goto('/reportes');
        await expect(page.locator('main h1')).toHaveText('Panorama');
        await expect(page.locator('#chart-sales')).toBeVisible();
        await expect(page.locator('#chart-category')).toBeVisible();
        await expect(page.locator('.cj-legend')).toContainText('Bajo stock');
        await expect(page.locator('#table-top tr').first()).toBeVisible();
        const index = page.getByRole('navigation', { name: 'Reportes' });
        await expect(index.getByRole('link', { name: /Ventas/ })).toHaveAttribute('href', '/reports/sales');
    });

    test('la búsqueda global encuentra productos del catálogo', async ({ page }) => {
        await page.goto('/dashboard');
        await page.locator('#ix-search-input').fill('Martillo');
        const results = page.locator('#ix-search-results');
        await expect(results).toBeVisible();
        await expect(results).toContainText('Martillo de uña 16 oz');
    });

    test('las alertas de inventario listan productos bajo el mínimo', async ({ page }) => {
        await page.goto('/dashboard');
        await page.locator('#ix-bell').click();
        const panel = page.locator('#ix-bell-panel');
        await expect(panel).toBeVisible();
        await expect(panel).toContainText(/Arena lavada m3|Cemento gris|Cinta aislante/);
    });

    test('en móvil el menú se abre y se cierra con el botón', async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto('/dashboard');
        const toggle = page.locator('#ix-menu-toggle');
        await expect(toggle).toBeVisible();
        await toggle.click();
        await expect(toggle).toHaveAttribute('aria-expanded', 'true');
        await expect(page.getByRole('complementary', { name: 'Navegación principal' }).getByRole('link', { name: 'Inicio' })).toBeVisible();
        await toggle.click();
        await expect(toggle).toHaveAttribute('aria-expanded', 'false');
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    });
});
