// Run against the disposable no-database reference harness on port 5000.
const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const out = '.tmp/sale-controls-validation';
(async () => {
    fs.mkdirSync(out, {recursive:true});
    const browser = await chromium.launch({channel:'chrome', headless:true});
    const context = await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1});
    const page = await context.newPage();
    const errors = [], writes = [];
    page.on('pageerror', e => errors.push(e.message));
    await context.route('**/*', route => {
        if (!['GET','HEAD'].includes(route.request().method())) { writes.push(route.request().method()); return route.abort(); }
        return route.continue();
    });
    const go = async (path = '/sales/new?ref=1') => {
        const response = await page.goto('http://127.0.0.1:5000' + path, {waitUntil:'networkidle'});
        assert.equal(response.status(), 200); await page.evaluate(() => document.fonts.ready);
    };
    const button = name => page.getByRole('button', {name, exact:true});
    const panel = page.locator('#sc-popover');
    const open = name => button(name).click();
    const snap = name => page.screenshot({path:`${out}/${name}.png`});
    const hidden = () => expect(panel).toBeHidden();
    await go();
    assert.deepEqual(await page.evaluate(() => [innerWidth,innerHeight,devicePixelRatio,visualViewport.scale]), [1600,900,1,1]);
    const base = await snap('base-after');
    if (fs.existsSync(`${out}/base-before.png`)) assert(fs.readFileSync(`${out}/base-before.png`).equals(base), 'Frozen base pixel equality');
    await expect(page.locator('#ix-sidebar')).toContainText('Análisis predictivo');
    await open('Fecha de venta'); await snap('date');
    await expect(panel.locator('[data-day][aria-pressed="true"]')).toHaveText('31');
    await expect(panel.locator('.sc-month')).toHaveCount(2);
    await expect(panel).toContainText('mayo 2024'); await expect(panel).toContainText('junio 2024');
    await panel.locator('[data-day]').first().click(); await panel.locator('[data-cancel]').click();
    await expect(button('Fecha de venta')).toHaveText('31/05/2024'); await hidden();
    await open('Fecha de venta'); await panel.locator('[data-day]').first().click(); await panel.locator('[data-apply]').click();
    await expect(button('Fecha de venta')).toHaveText('01/05/2024'); await hidden();
    for (const label of ['Hoy','Últimos 7 días','Últimos 30 días','Este mes','Mes pasado','Rango personalizado']) {
        await open('Fecha de venta'); await panel.getByRole('button',{name:label,exact:true}).click();
        await expect(panel.locator('[data-day][aria-pressed="true"]')).toHaveCount(1);
        await page.keyboard.press('Escape'); await hidden();
    }
    await open('Fecha de venta'); await panel.getByRole('button',{name:'Mes siguiente',exact:true}).click();
    await expect(panel).toContainText('junio 2024'); await panel.getByRole('button',{name:'Mes anterior',exact:true}).click();
    await page.mouse.click(800,850); await hidden();
    await open('Vendedor'); await snap('seller');
    await panel.getByRole('searchbox').fill('maria'); await expect(panel.getByRole('menuitemradio')).toHaveCount(1);
    await panel.getByRole('menuitemradio').click(); await expect(button('Vendedor')).toContainText('María Rodríguez'); await hidden();
    await open('Vendedor'); await panel.getByRole('searchbox').fill('xyz'); await expect(panel).toContainText('No se encontraron vendedores.');
    await page.keyboard.press('Escape'); await hidden();
    for (const [label, capture, choice] of [['Método de pago','method','Tarjeta de crédito'], ['Condición de pago','condition','60 días']]) {
        await open(label); await snap(capture); await expect(panel.getByRole('menuitemradio')).toHaveCount(6);
        await panel.getByRole('menuitemradio',{name:choice,exact:true}).click(); await expect(button(label)).toContainText(choice); await hidden();
        await open(label); await expect(panel.getByRole('menuitemradio',{name:choice,exact:true})).toHaveAttribute('aria-checked','true');
        await page.keyboard.press('Escape');
    }
    const controls = ['Fecha de venta','Vendedor','Método de pago','Condición de pago','Más opciones de guardado'];
    for (const label of controls) {
        await open(label); assert.equal(await page.locator(':popover-open').count(),1);
        assert.equal(await page.locator('.sc-trigger[aria-expanded="true"]').count(),1);
    }
    await snap('save'); await panel.getByRole('menuitem',{name:'Cancelar',exact:true}).click(); await hidden();
    await open('Guardar'); await expect(page.getByRole('status')).toContainText('Guardado simulado');
    for (const action of ['Guardar y nueva','Guardar y ver detalle','Guardar y enviar']) {
        await open('Más opciones de guardado'); await panel.getByRole('menuitem',{name:new RegExp('^'+action)}).click();
        await hidden(); await expect(page.getByRole('status')).toContainText('simulación completada');
    }
    await snap('save-feedback');
    await open('Vendedor'); await page.keyboard.press('Escape'); await expect(button('Vendedor')).toBeFocused();
    await page.keyboard.press('Enter'); await expect(panel).toBeVisible(); await page.keyboard.press('Escape');
    await go(); await open('Método de pago'); await open('Agregar producto'); await hidden();
    await expect(page.locator('#spl-drawer')).toBeVisible();
    await page.locator('#spl-search').fill('TAL-750W');
    await page.locator('#spl-products button').click(); await page.locator('#spl-confirm').click();
    await expect(page.locator('.sa-lines-table tbody tr').first().locator('.sa-qty')).toHaveAttribute('aria-valuenow','3');
    await open('Escanear'); await page.locator('#spl-simulate').click(); await page.locator('#spl-scan-confirm').click();
    await expect(page.locator('.sa-lines-table tbody tr').first().locator('.sa-qty')).toHaveAttribute('aria-valuenow','4');
    for (const path of ['/sales/orders','/sales/quotes','/sales/customers','/sales']) {
        await go(path+'?ref=1'); await expect(panel).toHaveCount(0); await snap('regression-'+path.replaceAll('/','-'));
    }
    await go('/sales/new'); await expect(panel).toHaveCount(0);
    await expect(page.locator('script[src*="ix_sale_controls"]')).toHaveCount(0);
    await go(); await expect(button('Fecha de venta')).toHaveText('31/05/2024');
    await page.setViewportSize({width:390,height:844});
    for (const label of controls) {
        await open(label); const box = await panel.boundingBox(); assert(box.x >= 0 && box.x + box.width <= 390);
        await page.keyboard.press('Escape');
    }
    assert.deepEqual(errors, []); assert.deepEqual(writes, []);
    console.log('PASS: frozen base; all five controls; search; selection; calendar; save simulations; single popover; keyboard; closures; add/scan; four sales routes; normal-mode isolation; mobile; zero JS errors; zero write requests.');
    await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
