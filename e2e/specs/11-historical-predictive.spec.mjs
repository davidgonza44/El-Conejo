// Datos históricos → análisis predictivo. El administrador importa un CSV v1 de ventas 2025
// (validar, simular y confirmar) y las pantallas predictivas pasan del estado "historial
// insuficiente" a reconocer el historial importado. Sin datos inventados: todo sale del CSV.
import { test, expect, as } from '../support/fixtures.mjs';

const HEADERS = ['event_date', 'product_code', 'product_name', 'quantity', 'record_type', 'record_status',
    'document_number', 'source_record_id', 'source_line_id', 'unit_price'];
const PRODUCTS = [['E2E-001', 'Martillo de uña 16 oz', '11.90'], ['E2E-005', 'Cable THW 12 AWG rollo', '54.00'],
    ['E2E-006', 'Interruptor sencillo', '2.60']];

function historyCsv() {
    const rows = [HEADERS.join(';')];
    const start = Date.UTC(2025, 0, 1);
    for (let day = 0; day < 365; day++) {
        const date = new Date(start + day * 86_400_000).toISOString().slice(0, 10);
        PRODUCTS.forEach(([code, name, price], index) => {
            const quantity = 2 + ((day + index * 3) % 5) + (day % 7 === 5 ? 3 : 0);
            const key = `E2E-H-${day}-${index}`;
            rows.push([date, code, name, quantity, 'sale', 'issued', key, key, '1', price].join(';'));
        });
    }
    return Buffer.from('﻿' + rows.join('\n') + '\n', 'utf8');
}

test.describe.configure({ mode: 'serial' });

test.describe('Importación histórica (administrador)', () => {
    test.use(as('admin'));

    test('descarga la plantilla CSV vacía', async ({ page }) => {
        await page.goto('/historical-imports');
        const download = page.waitForEvent('download');
        await page.locator('#btn-download-template').click();
        expect((await download).suggestedFilename()).toMatch(/\.csv$/);
    });

    test('rechaza un archivo que no es CSV v1', async ({ page }) => {
        await page.goto('/historical-imports');
        await page.locator('#btn-new-import').click();
        await page.locator('#upload-file').setInputFiles({ name: 'mal.csv', mimeType: 'text/csv', buffer: Buffer.from('a;b\n1;2\n') });
        await page.locator('#btn-upload-submit').click();
        await expect(page.locator('#upload-error, #page-alert, #detail-alert').filter({ hasText: /.+/ }).first()).toBeVisible();
    });

    test('importa un año de ventas: validar, simular y confirmar', async ({ page }) => {
        test.setTimeout(240_000); // 1 095 filas: validar y simular toma más con la base ya cargada.
        await page.goto('/historical-imports');
        await page.locator('#btn-new-import').click();
        await page.locator('#upload-file').setInputFiles({ name: 'e2e-historico-2025.csv', mimeType: 'text/csv', buffer: historyCsv() });
        await page.locator('#upload-source-system').fill('E2E_SISTEMA_ANTERIOR');
        await page.locator('#btn-upload-submit').click();
        const detail = page.locator('#import-detail-view');
        await expect(detail).toBeVisible({ timeout: 90_000 });
        await expect(page.locator('#detail-count-errors')).toHaveText('0');

        await page.locator('#btn-preview-import').click();
        await expect(page.locator('#detail-count-valid')).not.toHaveText('0', { timeout: 90_000 });
        await page.locator('#btn-dry-run').click();
        await expect(page.locator('#dry-run-summary-card')).toBeVisible({ timeout: 90_000 });
        await page.locator('#btn-confirm-import').click();
        const confirm = page.locator('#confirm-modal');
        await expect(confirm).toBeVisible();
        const atomic = confirm.locator('#confirm-atomic');
        if (await atomic.count()) await atomic.check();
        await confirm.locator('#btn-confirm-submit').click();
        await expect(page.locator('#detail-status-badge')).toHaveText(/Confirmad/, { timeout: 60_000 });
    });

    test('inventario consulta lotes pero no puede importar', async ({ browser }) => {
        const context = await browser.newContext({ storageState: as('inventario').storageState });
        const page = await context.newPage();
        await page.goto('/historical-imports');
        await expect(page.locator('#imports-tbody')).toContainText('Confirmado');
        await expect(page.locator('#imports-tbody')).toContainText('No disponible para este rol');
        await expect(page.locator('#btn-new-import')).toBeHidden();
        await context.close();
    });
});

test.describe('Análisis predictivo con historial', () => {
    test.use(as('admin'));

    // El CSV v1 solo admite 2025: sin ventas recientes, esos productos quedan con demanda
    // "inactiva" y el sistema lo dice en lugar de inventar un pronóstico.
    test('el pronóstico reconoce el historial importado y explica por qué no pronostica', async ({ page }) => {
        await page.goto('/predictive/demand');
        const state = page.locator('main');
        await expect(state).toContainText('Historial insuficiente para generar un pronóstico confiable.');
        await expect(state).toContainText('3 producto(s): Inactivo');
        await expect(state).toContainText('Fuente de demanda: históricos confirmados');
    });

    for (const [path, title] of [['/predictive/replenishment', 'Reabastecimiento'], ['/predictive/critical', 'críticos'],
        ['/predictive/trends', 'Tendencias'], ['/predictive/accuracy', 'Precisión']]) {
        test(`${path} se calcula sin errores`, async ({ page }) => {
            expect((await page.goto(path)).status()).toBe(200);
            await expect(page.locator('main h1').first()).toContainText(new RegExp(title, 'i'));
        });
    }
});

test.describe('Asistente virtual y catálogo', () => {
    test.use(as('inventario'));

    test('el chatbot responde el stock de un producto por código', async ({ page }) => {
        await page.goto('/chatbot');
        await page.locator('#chatbot-input').fill('¿Cuál es el stock del producto E2E-001?');
        await page.locator('#chatbot-send').click();
        await expect(page.locator('#chatbot-log')).toContainText('Martillo de uña 16 oz', { timeout: 20_000 });
    });

    test('el atajo "Productos bajo stock" lista productos bajo el mínimo', async ({ page }) => {
        await page.goto('/chatbot');
        await page.getByRole('button', { name: 'Productos bajo stock' }).click();
        await page.locator('#chatbot-send').click();
        await expect(page.locator('#chatbot-log')).toContainText(/Cinta aislante negra|Arena lavada m3/, { timeout: 20_000 });
    });

    test('el catálogo filtra por texto y por existencia', async ({ page }) => {
        await page.goto('/catalog');
        const cards = page.locator('main').getByRole('button', { name: 'Ver detalles del producto' });
        await page.locator('#catalog-search').fill('Cable');
        await expect(cards).toHaveCount(1);
        await expect(page.locator('main')).toContainText('Cable THW 12 AWG rollo');
        await page.locator('#catalog-clear-filters').click();
        await page.locator('#catalog-stock-status').selectOption({ label: 'Sin existencia' });
        await expect(cards).toHaveCount(1);
        await expect(page.locator('main')).toContainText('Arena lavada m3');
    });

    test('sin permiso de precio, el catálogo no muestra precios inventados', async ({ page }) => {
        await page.goto('/catalog');
        await expect(page.locator('main').getByRole('button', { name: 'Ver detalles del producto' }).first()).toBeVisible();
        await expect(page.locator('.catalog-card-fact', { hasText: 'Precio' })).toHaveCount(0);
        await expect(page.locator('#catalog-sort option[value^="price-"]')).toHaveCount(0);
    });
});

test.describe('Conciliación (administrador)', () => {
    test.use(as('admin'));

    test('muestra cortes y conciliaciones sin errores', async ({ page }) => {
        expect((await page.goto('/reconciliation')).status()).toBe(200);
        await expect(page.locator('main h1')).toHaveText('Cortes y conciliaciones');
    });
});
