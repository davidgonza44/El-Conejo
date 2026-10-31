// Recorre el sistema con el servidor de demostración (demo_server.py, puerto 5098) y guarda una
// captura por paso de los videos en public/capturas/, más la zona a resaltar en src/capturas.json.
// Cada tarea se hace con el rol que la tiene asignada; las compras se preparan con la API real.
import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { VIDEOS } from '../src/guion.js';

const BASE = process.env.DEMO_URL || 'http://127.0.0.1:5098';
const HERE = import.meta.dirname;
const ROOT = path.resolve(HERE, '..', '..');
const SHOTS = path.resolve(HERE, '..', 'public', 'capturas');
const PASSWORDS = JSON.parse(fs.readFileSync(path.join(ROOT, 'e2e', 'fixtures', 'users.json'), 'utf8'));
// Mismo mapa que PEOPLE en demo_seed.py.
const USERNAMES = { admin: 'arivas', inventario: 'lmendoza', asistente: 'cperez', vendedor: 'jherrera' };
const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Caracas' }).format(new Date());

const browser = await chromium.launch({ channel: 'chrome' });
const contexts = {};

async function newContext() {
    return browser.newContext({ baseURL: BASE, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2,
        locale: 'es-VE', timezoneId: 'America/Caracas' });
}

async function pageAs(role) {
    if (!contexts[role]) {
        const context = await newContext();
        const login = await context.request.post('/api/auth/login',
            { data: { identifier: USERNAMES[role], password: PASSWORDS[role].password } });
        if (!login.ok()) throw new Error(`login ${role}: ${login.status()}`);
        contexts[role] = { context, page: await context.newPage() };
    }
    return contexts[role].page;
}

async function go(page, url) {
    await page.goto(url);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(500);
}

/** Cliente de la API con el token CSRF de la sesión, como e2e/support/fixtures.mjs. */
async function api(role) {
    const page = await pageAs(role);
    await go(page, '/purchases');
    const csrf = await page.evaluate(() => {
        for (const script of document.querySelectorAll('script[type="application/json"]')) {
            try { const data = JSON.parse(script.textContent); if (data.csrf) return data.csrf; } catch { /* otro JSON */ }
        }
        return document.querySelector('[data-csrf]')?.dataset.csrf || document.querySelector('meta[name="csrf-token"]')?.content;
    });
    const call = async (method, url, data) => {
        const response = await page.request.fetch(url, { method, data, headers: { 'X-CSRFToken': csrf } });
        const body = await response.json().catch(() => ({}));
        if (!response.ok()) throw new Error(`${method} ${url}: ${response.status()} ${JSON.stringify(body)}`);
        return body;
    };
    return { get: (url) => call('GET', url), post: (url, data = {}) => call('POST', url, data) };
}

/** Ciclo de compra real: una orden recibida completa y otra en borrador pendiente de aprobación. */
async function prepararCompras() {
    const inventario = await api('inventario');
    const productId = async (code) => (await inventario.get(`/api/products?search=${code}&page=1&per_page=5`))
        .items.find((row) => row.code === code).id;
    const flows = [];
    for (const [code, quantity, price, supplier] of [['CON-003', 300, '0.42', /Aceros/], ['HER-004', 12, '7.60', /Andina/]]) {
        const request = await inventario.post('/api/replenishment-requests', { items: [{ product_id: await productId(code), quantity }] });
        flows.push({ ...request, price, supplier });
    }
    const asistente = await api('asistente');
    const options = await asistente.get('/api/purchases/options');
    const suppliers = options.suppliers || options.items || [];
    const orders = [];
    for (const request of flows) {
        await asistente.post(`/api/replenishment-requests/${request.id}/start`);
        const supplier = suppliers.find((s) => request.supplier.test(s.name));
        const quote = await asistente.post(`/api/replenishment-requests/${request.id}/quotes`, {
            supplier_id: supplier.id, currency: 'USD', issued_on: today, valid_until: '2099-01-01',
            items: request.items.map((item) => ({ product_id: item.product_id, unit_price: request.price })) });
        orders.push((await asistente.post(`/api/replenishment-requests/${request.id}/prepare`,
            { supplier_quote_id: quote.id })).purchase_order.id);
    }
    await (await api('admin')).post(`/api/purchases/${orders[0]}/approve`);
    const order = await inventario.get(`/api/purchases/${orders[0]}`);
    await inventario.post(`/api/purchases/${orders[0]}/receptions`, { received_at: today,
        items: order.items.map((line) => ({ line_id: line.id, quantity: line.quantity_ordered - line.quantity_received })) });
    return { recibida: orders[0], borrador: orders[1] };
}

const main = (page) => page.locator('main');
const table = (page) => page.locator('main table').first();
const text = (page, value) => page.getByText(value, { exact: true }).first();

// id -> { as: rol, go: ruta, act: acción previa, focus: zona a resaltar (uno o varios localizadores) }
const PASOS = {
    'login-vacio': { as: null, go: '/login', focus: (p) => [p.locator('#login-identifier'), p.locator('#login-submit')] },
    'login-datos': { as: null, act: async (p) => {
        await p.locator('#login-identifier').fill(USERNAMES.admin);
        await p.locator('#login-password').fill(PASSWORDS.admin.password);
    }, focus: (p) => p.locator('#login-submit') },
    'inicio-propuestas': { as: 'admin', go: '/dashboard', focus: (p) => p.locator('[aria-label="Propuestas pendientes"]') },
    'inicio-indicadores': { as: 'admin', focus: (p) => p.locator('#kpi-row') },
    'inicio-listas': { as: 'admin', focus: (p) => [p.locator('#card-activity'), p.locator('#card-lowstock')] },
    'inicio-menu': { as: 'admin', focus: (p) => p.locator('#ix-sidebar nav').first() },
    'inicio-busqueda': { as: 'admin', act: async (p) => {
        await p.getByPlaceholder(/Buscar productos y clientes/).first().fill('cemento');
        await p.waitForTimeout(1200);
    }, focus: (p) => p.getByPlaceholder(/Buscar productos y clientes/).first() },

    'prod-lista': { as: 'admin', go: '/products', focus: table },
    'prod-filtros': { as: 'admin', focus: (p) => [text(p, 'Categoría'), text(p, 'Limpiar filtros')] },
    'prod-nuevo': { as: 'asistente', go: '/products', act: async (p) => {
        await p.getByRole('button', { name: 'Nuevo producto' }).click();
        await p.locator('#np-name').fill('Pala cuadrada con mango');
        await p.locator('#np-sku').fill('HER-006');
        await p.locator('#np-category').selectOption({ label: 'Herramientas' });
        await p.locator('#np-unit').selectOption({ index: 1 });
        await p.locator('#np-price').fill('16.50');
        await p.locator('#np-price-currency').selectOption('USD');
        await p.locator('#np-cost').fill('9.80');
        await p.locator('#np-cost-currency').selectOption('USD');
        await p.locator('#np-stock').fill('12');
        await p.locator('#np-minimum').fill('4');
    }, focus: (p) => p.locator('#np-drawer') },
    'prod-pendiente': { as: 'admin', go: '/dashboard', act: async (p) => {
        await p.getByRole('link', { name: /Productos pendientes/ }).click();
        await p.waitForLoadState('networkidle');
        await p.waitForTimeout(600);
    }, focus: (p) => p.locator('main table tbody tr', { hasText: 'Juego de brocas' }) },
    'prod-aprobar': { as: 'admin', act: async (p) => {
        await p.locator('main table tbody tr', { hasText: 'Juego de brocas' }).getByRole('button', { name: 'Ver detalle' }).click();
        await p.waitForTimeout(700);
    }, focus: (p) => p.locator('#pl-approve') },

    'cat-lista': { as: 'admin', go: '/categories', focus: table },
    'cat-nueva': { as: 'asistente', go: '/categories', act: async (p) => {
        await p.getByRole('button', { name: 'Nueva categoría' }).click();
        const dialog = p.locator('#cl-dialog');
        await dialog.getByLabel('Nombre de la categoría').fill('Pinturas');
        await dialog.getByLabel('Descripción').fill('Pinturas, brochas y rodillos');
    }, focus: (p) => p.locator('#cl-dialog') },
    'catalogo': { as: 'vendedor', go: '/catalog', focus: (p) => [text(p, 'Buscar y filtrar'), p.getByText('Ver detalles').nth(3)] },

    'mov-lista': { as: 'inventario', go: '/inventory', focus: table },
    'mov-nuevo': { as: 'inventario', act: async (p) => {
        await p.getByRole('button', { name: 'Nuevo movimiento' }).click();
        await p.locator('#mv-kind').selectOption('entrada');
        await p.getByRole('searchbox', { name: 'Buscar producto' }).fill('Cinta aislante');
        await p.waitForTimeout(800);
        await p.locator('#mv-product').selectOption({ label: 'Cinta aislante negra (ELE-003)' });
        await p.locator('#mv-quantity').fill('30');
        await p.locator('#mv-notes').fill('Reposición de proveedor');
    }, focus: (p) => p.locator('#mv-drawer') },
    'mov-guardado': { as: 'inventario', act: async (p) => {
        await p.getByRole('button', { name: 'Guardar movimiento' }).click();
        await p.getByText(/registrado\. El stock se actualizó correctamente/).waitFor();
        await p.waitForTimeout(600);
    }, focus: (p) => p.getByText(/registrado\. El stock se actualizó correctamente/) },
    'mov-error': { as: 'inventario', act: async (p) => {
        await p.getByRole('button', { name: 'Nuevo movimiento' }).click();
        await p.locator('#mv-kind').selectOption('salida');
        await p.getByRole('searchbox', { name: 'Buscar producto' }).fill('Llave ajustable');
        await p.waitForTimeout(800);
        await p.locator('#mv-product').selectOption({ label: 'Llave ajustable 10 in (HER-004)' });
        await p.locator('#mv-quantity').fill('50');
        await p.locator('#mv-notes').fill('Despacho a obra');
        await p.getByRole('button', { name: 'Guardar movimiento' }).click();
        await p.locator('#mv-drawer [data-live-error]').first().waitFor();
        await p.waitForTimeout(600);
    }, focus: (p) => [p.locator('#mv-quantity'), p.locator('#mv-drawer [data-live-error]').first()] },
    'mov-exportar': { as: 'admin', go: '/inventory', act: async (p) => {
        await p.getByRole('button', { name: /^Exportar/ }).first().click();
        await p.waitForTimeout(500);
    }, focus: (p) => p.getByRole('menu').first() },

    'conc-inicio': { as: 'admin', go: '/reconciliation', focus: (p) => [text(p, 'Programación de cortes'), text(p, '18:30')] },

    'cli-lista': { as: 'vendedor', go: '/sales/customers', focus: table },
    'cli-nuevo': { as: 'vendedor', act: async (p) => {
        await p.locator('#cu-new').click();
        const drawer = p.locator('#cu-drawer');
        await drawer.getByLabel('Nombre / Razón Social').fill('Ferretería Tocuyito');
        await drawer.getByLabel('RIF').fill('J-30777888-2');
        await drawer.getByLabel('Teléfono').fill('0241-8990011');
        await drawer.getByLabel('Correo').fill('compras@tocuyito.example');
    }, focus: (p) => p.locator('#cu-drawer') },
    'nota-lista': { as: 'vendedor', go: '/delivery-notes', focus: table },
    'nota-nueva': { as: 'vendedor', act: async (p) => {
        await p.locator('#btn-new-note').click();
        await p.getByLabel('Nombre del cliente').fill('Ferretería Tocuyito');
        for (const [code, quantity] of [['CON-001', '4'], ['PLO-002', '2']]) {
            const value = await p.locator('#c-product option', { hasText: `${code} — ` }).getAttribute('value');
            await p.locator('#c-product').selectOption(value);
            await p.locator('#c-quantity').fill(quantity);
            await p.locator('#btn-add-line').click();
        }
        await p.waitForTimeout(400);
    }, focus: (p) => p.locator('#create-modal .modal-dialog') },
    'nota-revisar': { as: 'vendedor', act: async (p) => {
        await p.locator('#create-save-btn').click();
        await p.locator('#create-confirm').waitFor();
        await p.waitForTimeout(400);
    }, focus: (p) => p.locator('#create-confirm') },
    'nota-emitida': { as: 'vendedor', act: async (p) => {
        await p.locator('#create-save-btn').click();
        await p.locator('#notes-tbody tr', { hasText: 'Ferretería Tocuyito' }).waitFor();
        await p.waitForTimeout(800);
    }, focus: (p) => p.locator('#notes-tbody tr', { hasText: 'Ferretería Tocuyito' }) },
    'nota-detalle': { as: 'vendedor', act: async (p) => {
        await p.locator('#notes-tbody tr', { hasText: 'Ferretería Tocuyito' }).getByRole('button', { name: 'Ver detalle' }).click();
        await p.locator('#detail-modal').waitFor();
        await p.waitForTimeout(600);
    }, focus: (p) => p.locator('#btn-download-pdf') },

    'comp-solicitudes': { as: 'inventario', go: '/purchases/requests', focus: table },
    'comp-ordenes': { as: 'admin', go: '/purchases?start=&end=', focus: table },
    'comp-borrador': { as: 'asistente', go: (ids) => `/purchases/${ids.borrador}/print`,
        focus: (p) => p.getByText(/BORRADOR PENDIENTE DE APROBACIÓN/).first() },
    'comp-aprobada': { as: 'admin', go: (ids) => `/purchases/${ids.recibida}/print`,
        focus: (p) => p.getByText(/Aprobada por/).first() },
    'comp-recepciones': { as: 'inventario', go: '/purchases/receptions', focus: table },
    'comp-proveedores': { as: 'asistente', go: '/suppliers', focus: table },

    'rep-salud': { as: 'admin', go: '/reportes', focus: (p) => p.locator('main section, main .ix-card').first() },
    'rep-graficas': { as: 'admin', focus: (p) => [text(p, 'Ventas de los últimos 7 días'), p.locator('main canvas').first(), p.locator('main canvas').last()] },
    'rep-indice': { as: 'admin', focus: (p) => [text(p, 'Reportes'), p.getByText(/riesgo de quiebre/).last()] },
    'rep-ventas': { as: 'admin', go: '/reports/sales', focus: (p) => [main(p).getByText('Resumen', { exact: true }).first(),
        text(p, 'Métodos de pago'), p.getByRole('button', { name: /Exportar CSV/ })] },
    'rep-inventario': { as: 'admin', go: '/inventory/summary', focus: (p) => [main(p).locator('h1').first(), p.getByText(/Movimientos de inventario en los/).first()] },

    'pred-demanda': { as: 'admin', go: '/predictive/demand', focus: (p) => [text(p, 'Pronóstico no disponible'), p.getByText(/Fuente de demanda:/).first()] },
    'pred-fuente': { as: 'admin', focus: (p) => [main(p).getByText(/Demanda histórica independiente/).first(),
        main(p).getByText(/Pronóstico del entorno actual/).first()] },
    'pred-reabastecimiento': { as: 'admin', go: '/predictive/replenishment', focus: (p) => [text(p, 'Reabastecimiento predictivo no disponible'), p.getByText(/Fuente de demanda:/).first()] },
    'pred-criticos': { as: 'admin', go: '/predictive/critical', focus: (p) => [main(p).locator('h1').first(), p.locator('main table tbody tr').nth(1)] },
    'pred-precision': { as: 'admin', go: '/predictive/accuracy', focus: (p) => [text(p, 'Error global (WAPE)'), text(p, 'Predicciones evaluadas')] },

    'hist-importar': { as: 'admin', go: '/historical-imports', focus: (p) => [p.getByText('Nueva importación').first(),
        text(p, 'Requisitos del archivo'), p.getByText(/50\.000 filas/).first()] },
    'hist-diagnostico': { as: 'admin', go: '/predictions', focus: (p) => [text(p, 'Diagnóstico por producto'), p.locator('main table tbody tr').nth(3)] },

    'chat-inicio': { as: 'vendedor', go: '/chatbot', focus: (p) => text(p, 'Consultas rápidas') },
    'chat-pregunta': { as: 'vendedor', act: async (p) => {
        await p.locator('#chatbot-input').fill('¿Cuál es el stock del producto CON-001?');
    }, focus: (p) => p.locator('#chatbot-input') },
    'chat-respuesta': { as: 'vendedor', act: async (p) => {
        await p.locator('#chatbot-send').click();
        await p.locator('#chatbot-log').getByText(/Cemento gris/).first().waitFor({ timeout: 20_000 });
        await p.waitForTimeout(800);
    }, focus: (p) => p.locator('#chatbot-log article.chatbot-message').last() },
    'chat-agotados': { as: 'vendedor', act: async (p) => {
        await p.locator('main').getByRole('button', { name: 'Productos agotados' }).click();
        await p.locator('#chatbot-log').getByText(/Teflón/).last().waitFor({ timeout: 20_000 });
        await p.waitForTimeout(800);
    }, focus: (p) => p.locator('#chatbot-log article.chatbot-message').last() },

    'conf-general': { as: 'admin', go: '/settings', focus: (p) => [text(p, 'Información general'), p.getByText('Subir logo').first()] },
    'conf-usuarios': { as: 'admin', go: '/settings?tab=users', focus: (p) => [p.getByPlaceholder(/Buscar usuario/),
        p.getByRole('button', { name: /Nuevo usuario/ }), p.getByText(/Mostrando 1 a/).first()] },
    'conf-seguridad': { as: 'admin', go: '/settings?tab=security', focus: (p) => [text(p, 'Autenticación y acceso'), p.getByText('Editar política').first()] },
    'conf-respaldo': { as: 'admin', go: '/settings?tab=backup', act: async (p) => {
        // La ubicación muestra una carpeta local de esta computadora: se difumina en la captura.
        await p.evaluate(() => document.querySelectorAll('main *').forEach((el) => {
            const local = /AppData|conejo_e2e/i;
            const shown = [el.value, el.placeholder, ...[...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent)];
            if (shown.some((value) => local.test(value ?? ''))) el.style.filter = 'blur(6px)';
        }));
    }, focus: (p) => [text(p, 'Resumen de respaldos'), text(p, 'Configuración de respaldo automático'), p.getByText(/Crear respaldo ahora/).first()] },
    'perfil': { as: 'admin', go: '/profile', focus: (p) => [text(p, 'Información del administrador'), p.getByRole('button', { name: /Guardar cambios/ })] },

    'guia-inicio': { as: 'vendedor', go: '/ayuda', focus: (p) => [main(p).locator('h2').first(), main(p).locator('table').first()] },
    'guia-buscar': { as: 'vendedor', act: async (p) => {
        await p.locator('#hp-filter').fill('nota');
        await p.waitForTimeout(500);
    }, focus: (p) => p.locator('#hp-filter') },
};

/** Caja (en px CSS del viewport) que une todas las zonas, recortada a la pantalla. */
async function zona(page, focus) {
    const locators = [focus(page)].flat();
    await locators[0].scrollIntoViewIfNeeded({ timeout: 5000 });
    const boxes = (await Promise.all(locators.map((l) => l.boundingBox({ timeout: 5000 })))).filter(Boolean);
    if (!boxes.length) return null;
    const x = Math.max(0, Math.min(...boxes.map((b) => b.x)));
    const y = Math.max(0, Math.min(...boxes.map((b) => b.y)));
    const right = Math.min(1440, Math.max(...boxes.map((b) => b.x + b.width)));
    const bottom = Math.min(900, Math.max(...boxes.map((b) => b.y + b.height)));
    return { x: Math.round(x), y: Math.round(y), w: Math.round(right - x), h: Math.round(bottom - y) };
}

const needed = VIDEOS.flatMap((video) => video.pasos.map((paso) => paso.captura));
const missing = needed.filter((id) => !PASOS[id]);
if (missing.length) throw new Error(`Pasos del guion sin captura definida: ${missing.join(', ')}`);

fs.mkdirSync(SHOTS, { recursive: true });
fs.copyFileSync(path.join(ROOT, 'app', 'static', 'img', 'logo_oficial_el_conejo.png'), path.join(SHOTS, '..', 'logo.png'));
const ids = await prepararCompras();
const anon = await (await newContext()).newPage();
const result = {};
const failures = [];
for (const id of needed) {
    const step = PASOS[id];
    const page = step.as ? await pageAs(step.as) : anon;
    try {
        if (step.go) await go(page, typeof step.go === 'function' ? step.go(ids) : step.go);
        if (step.act) await step.act(page);
        const box = await zona(page, step.focus).catch(() => null);
        await page.screenshot({ path: path.join(SHOTS, `${id}.png`) });
        result[id] = { archivo: `capturas/${id}.png`, url: new URL(page.url()).pathname, zona: box };
        console.log(`ok   ${id}${box ? '' : '  (sin zona)'}`);
    } catch (error) {
        failures.push(id);
        console.log(`FALLA ${id}: ${error.message.split('\n')[0]}`);
    }
}
fs.writeFileSync(path.resolve(HERE, '..', 'src', 'capturas.json'), `${JSON.stringify(result, null, 2)}\n`);
await browser.close();
if (failures.length) {
    console.log(`\n${failures.length} captura(s) fallaron: ${failures.join(', ')}`);
    process.exitCode = 1;
}
