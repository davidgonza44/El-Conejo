// Matriz de acceso: cada pantalla con cada rol. "ok" = renderiza su título sin errores;
// "deny" = página 403 del servidor. Refleja la separación de funciones de app/utils/permissions.py.
import { test, expect, as } from '../support/fixtures.mjs';

const A = 'admin', I = 'inventario', S = 'asistente', V = 'vendedor', T = 'soporte_ti';
const ALL = [A, I, S, V, T];

// ruta, título visible (h1), roles autorizados
const PAGES = [
    ['/dashboard', 'Inicio', [A, I, S]],
    ['/inventory/summary', /Resumen de inventario|Resumen/, [A, I, S]],
    ['/products', 'Gestión de Productos', [A, I, S, V]],
    ['/categories', 'Categorías', [A, I, S, V]],
    ['/catalog', /Catálogo/, [A, I, S, V]],
    ['/inventory', /Movimientos/, [A, I, S]],
    ['/suppliers', 'Proveedores', [A, S]],
    ['/purchases', 'Compras', [A, I, S]],
    ['/purchases/requests', /Solicitudes de reposición/, [A, I, S]],
    ['/purchases/receptions', /Recepciones/, [A, I, S]],
    ['/delivery-notes', /Notas de entrega/, [A, I, V]],
    ['/sales/customers', 'Clientes', [A, V]],
    ['/historical-imports', /Importaci/, [A, I]],
    ['/reconciliation', 'Cortes y conciliaciones', [A]],
    ['/historical-2026', 'Histórico real 2026', [A, I]],
    ['/predictive/demand', /Pronóstico/, [A, I, S]],
    ['/predictive/replenishment', /Reabastecimiento/, [A, I, S]],
    ['/predictive/critical', /críticos/i, [A, I, S]],
    ['/predictive/trends', /Tendencias/, [A, I, S]],
    ['/predictive/accuracy', /Precisión/, [A, I, S]],
    ['/reports/sales', 'Reportes de ventas', [A, I, S]],
    ['/reports/sales/history', /Operaciones|Histórico|Sin operaciones/, [A, I, S]],
    ['/settings', 'Configuración', [A, T]],
    ['/chatbot', /Asistente|Chatbot/, ALL],
    ['/profile', /Mi perfil|Administrador/, ALL],
];

// Ventas a clientes está retirada para todos los roles (rbac_service.WITHDRAWN_CUSTOMER_SALES).
const WITHDRAWN = ['/sales', '/sales/list', '/sales/new', '/sales/quotes', '/sales/orders'];

for (const role of ALL) {
    test.describe(`Acceso como ${role}`, () => {
        test.use(as(role));

        for (const [path, title, allowed] of PAGES) {
            const ok = allowed.includes(role);
            test(`${ok ? 'abre' : 'bloquea'} ${path}`, async ({ page }) => {
                const response = await page.goto(path);
                if (ok) {
                    expect(response.status()).toBe(200);
                    await expect(page.locator('main h1, h1').first()).toHaveText(title);
                } else {
                    expect(response.status()).toBe(403);
                    await expect(page.getByText('No tiene permisos para acceder a esta sección.').first()).toBeVisible();
                }
            });
        }

        test('ventas a clientes retiradas', async ({ page }) => {
            for (const path of WITHDRAWN) {
                expect((await page.goto(path)).status(), path).toBe(403);
            }
        });
    });
}

test.describe('Sin sesión', () => {
    test('las páginas protegidas redirigen al login', async ({ page }) => {
        for (const [path] of PAGES) {
            await page.goto(path);
            await expect(page, path).toHaveURL(/\/login$/);
        }
    });

    test('la API protegida responde 401 en JSON', async ({ request }) => {
        const response = await request.get('/api/products');
        expect(response.status()).toBe(401);
        expect(await response.json()).toHaveProperty('error');
    });
});
