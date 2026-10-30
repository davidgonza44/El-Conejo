// Only run with the no-database test_settings_reference.py harness on port 5000.
// Baselines must be captured before implementation in this same browser environment.
const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {execFileSync} = require('node:child_process');
const out = '.tmp/order-layers-validation';
(async () => {
    const browser = await chromium.launch({channel:'chrome',headless:true});
    const context = await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1});
    const writes = [], errors = [];
    await context.route('**/*', route => {
        if (!['GET','HEAD'].includes(route.request().method())) { writes.push(route.request().method()); return route.abort(); }
        return route.continue();
    });
    const p = await context.newPage(); p.on('pageerror', e => errors.push(e.message));
    const go = async () => { await p.goto('http://127.0.0.1:5000/sales/orders?ref=1',{waitUntil:'networkidle'}); await p.evaluate(() => document.fonts.ready); };
    await go();
    assert.deepEqual(await p.evaluate(() => [innerWidth,innerHeight,devicePixelRatio,visualViewport.scale]), [1600,900,1,1]);
    const baseline = fs.readFileSync(out + '/before-sales-orders.png');
    assert(baseline.equals(await p.screenshot({path:out+'/base-after.png'})), 'Orders base pixels unchanged');
    const triggers = ['.sa-toolbar .is-primary','.sa-chip:nth-child(1)','.sa-chip:nth-child(2)','.sa-chip:nth-child(3)'];
    const ids = ['drawer','dates','status','more'];
    const open = async i => { await p.locator(triggers[i]).click(); await expect(p.locator('#ol-'+ids[i])).toBeVisible(); };
    for (let i = 0; i < 4; i++) {
        await open(i); await p.screenshot({path:out+'/'+ids[i]+'-final.png'});
        await p.keyboard.press('Escape'); await expect(p.locator('#ol-'+ids[i])).toBeHidden();
        await expect(p.locator(triggers[i])).toBeFocused();
    }
    for (const i of [0,3]) {
        for (const method of ['Cancelar','X','outside']) {
            await open(i); const layer = p.locator('#ol-'+ids[i]);
            assert.equal(await p.evaluate(() => document.body.style.overflow),'hidden');
            if (method === 'Cancelar') await layer.getByRole('button',{name:'Cancelar',exact:true}).click();
            if (method === 'X') await layer.getByRole('button',{name:/^Cerrar/}).click();
            if (method === 'outside') await p.mouse.click(3,3);
            await expect(layer).toBeHidden(); await expect(p.locator(triggers[i])).toBeFocused();
            assert.equal(await p.evaluate(() => document.body.style.overflow),'');
        }
    }
    for (const i of [1,2]) { await open(i); await p.mouse.click(850,100); await expect(p.locator('#ol-'+ids[i])).toBeHidden(); }
    await open(0);
    await expect(p.locator('#ol-customer')).toHaveValue('Constructora del Norte S.A.');
    await expect(p.locator('#ol-order-date')).toHaveValue('31/05/2024');
    await expect(p.locator('#ol-order-delivery')).toHaveValue('07/06/2024');
    await expect(p.locator('#ol-order-status')).toHaveValue('Pendiente');
    await expect(p.locator('#ol-products tr')).toHaveCount(2);
    await expect(p.locator('#ol-total')).toHaveText('$100.34');
    assert(await p.locator('#ol-total').evaluate(el => el.getBoundingClientRect().bottom <= document.querySelector('#ol-drawer > .ol-footer').getBoundingClientRect().top),'Total visible above footer');
    await p.locator('[data-product="0"] [data-qty="1"]').click();
    await expect(p.locator('#ol-total')).toHaveText('$109.04');
    await p.locator('[data-product="0"] [data-qty="-1"]').click();
    await expect(p.locator('#ol-total')).toHaveText('$100.34');
    const quantity = p.locator('[data-product="0"] input');
    await quantity.fill('2'); await quantity.press('Tab'); await expect(p.locator('#ol-total')).toHaveText('$30.74');
    await quantity.fill('0'); await quantity.press('Tab'); await expect(quantity).toHaveValue('2');
    await p.locator('[data-product="1"] [data-remove]').click(); await expect(p.locator('#ol-total')).toHaveText('$17.40');
    await p.locator('#ol-add').click(); await p.locator('#ol-catalog').selectOption('1'); await p.locator('#ol-add-confirm').click();
    await expect(p.locator('#ol-total')).toHaveText('$20.07');
    await p.getByRole('button',{name:'Guardar pedido',exact:true}).click(); await expect(p.locator('#ol-drawer')).toBeHidden();
    await expect(p.locator('#ol-notice')).toContainText('No se creó ningún pedido real');
    await go(); await open(0); await expect(p.locator('#ol-total')).toHaveText('$100.34');
    for (let n=0;n<32;n++) { await p.keyboard.press('Tab'); assert(await p.evaluate(() => !!document.activeElement.closest('#ol-drawer')), 'Drawer focus trap'); }
    await p.keyboard.press('Escape');
    const expectedRanges = ['31/05/2024 → 31/05/2024','25/05/2024 → 31/05/2024','02/05/2024 → 31/05/2024','01/05/2024 → 31/05/2024','01/04/2024 → 30/04/2024'];
    await open(1);
    for(let i=0;i<5;i++) { await p.locator(`[data-ol-quick="${i}"]`).click(); await expect(p.locator('#ol-range')).toContainText(expectedRanges[i]); }
    await p.locator('#ol-dates').getByRole('button',{name:'Cancelar',exact:true}).click(); await expect(p.locator(triggers[1])).toContainText('Todas las fechas');
    await open(1); await p.locator('[data-ol-quick="0"]').click(); await p.locator('#ol-date-apply').click();
    await expect(p.locator('.sa-pagination-summary')).toHaveText('Mostrando 1 a 1 de 1 pedidos');
    await expect(p.locator('.sa-table tbody')).toContainText('PED-000067');
    await open(1); await p.locator('[data-ol-quick="5"]').click();
    await p.locator('[data-ol-day="2024-05-29"]').first().click(); await expect(p.locator('#ol-date-apply')).toBeDisabled();
    await p.locator('[data-ol-day="2024-05-31"]').first().click();
    await p.screenshot({path:out+'/custom-range.png'}); await p.locator('#ol-date-apply').click();
    await expect(p.locator('.sa-table tbody tr')).toHaveCount(5);
    await open(2); await expect(p.locator('#ol-status [role="option"]')).toHaveCount(5);
    await p.locator('[data-ol-status="2"]').click(); await expect(p.locator('.sa-table tbody tr')).toHaveCount(2);
    await open(3); await p.locator('#ol-filter-client').selectOption('Constructora del Norte S.A.');
    await p.locator('#ol-filter-priority').selectOption('Normal'); await p.getByRole('button',{name:'Aplicar filtros',exact:true}).click();
    await expect(p.locator('.sa-table tbody tr')).toHaveCount(1); await expect(p.locator('.sa-table tbody')).toContainText('PED-000067');
    await expect(p.locator(triggers[1])).toContainText('29/05/2024'); await expect(p.locator(triggers[2])).toContainText('En proceso');
    await p.screenshot({path:out+'/combined-filters.png'});
    await open(3); await p.locator('#ol-filter-client').selectOption('Público en general');
    await p.locator('#ol-more').getByRole('button',{name:'Cancelar',exact:true}).click();
    await open(3); await expect(p.locator('#ol-filter-client')).toHaveValue('Constructora del Norte S.A.');
    await p.locator('#ol-filter-min').fill('20'); await p.locator('#ol-filter-max').fill('2');
    await p.getByRole('button',{name:'Aplicar filtros',exact:true}).click(); await expect(p.locator('#ol-filter-error')).not.toBeEmpty();
    await p.locator('#ol-reset').click(); await expect(p.locator('.sa-pagination-summary')).toHaveText('Mostrando 1 a 10 de 67 pedidos');
    assert(baseline.equals(await p.screenshot({path:out+'/cleared.png'})), 'Clear restores exact base');
    // Exercise every additional criterion together, then every status independently.
    await open(3);
    for (const [key,value] of [['product','Cemento Gris 42.5 kg'],['seller','Administrador'],['payment','Pendiente'],['priority','Normal']]) await p.locator('#ol-filter-'+key).selectOption(value);
    await p.locator('#ol-filter-from').fill('2024-06-05'); await p.locator('#ol-filter-to').fill('2024-06-05');
    await p.locator('#ol-filter-min').fill('15'); await p.locator('#ol-filter-max').fill('15');
    await p.getByRole('button',{name:'Aplicar filtros',exact:true}).click();
    await expect(p.locator('.sa-table tbody')).toContainText('PED-000067');
    const rows = await p.locator('.sa-table tbody tr').allTextContents(); assert(rows.every(t => t.includes('Constructora del Norte S.A.') && t.includes('05/06/2024')));
    await open(3); await p.locator('#ol-reset').click();
    for (const [i,name] of [[1,'Pendiente'],[2,'En proceso'],[3,'Completado'],[4,'Cancelado']]) {
        await open(2); await p.locator(`[data-ol-status="${i}"]`).click();
        const values = await p.locator('.sa-table tbody .sa-badge').allTextContents(); assert(values.length && values.every(v => v === name));
        await open(2); await expect(p.locator(`[data-ol-status="${i}"]`)).toHaveAttribute('aria-selected','true'); await p.keyboard.press('Escape');
    }
    await open(2); await p.locator('[data-ol-status="0"]').click();
    await expect(p.locator('.sa-pagination-summary')).toHaveText('Mostrando 1 a 10 de 67 pedidos');
    await p.locator('.sa-pages').getByRole('button',{name:'Página 7',exact:true}).click(); await expect(p.locator('.sa-table tbody tr')).toHaveCount(7);
    await expect(p.locator('.sa-pagination-summary')).toHaveText('Mostrando 61 a 67 de 67 pedidos');
    await go();
    await open(1); await p.locator(triggers[2]).click(); await expect(p.locator('#ol-dates')).toBeHidden();
    await p.locator(triggers[3]).click(); await expect(p.locator('#ol-status')).toBeHidden();
    await p.evaluate(() => document.querySelector('.sa-toolbar .is-primary').click());
    await expect(p.locator('#ol-more')).toBeHidden(); await expect(p.locator('#ol-drawer')).toBeVisible();
    assert.equal(await p.locator('dialog[open]').count(),1); await p.keyboard.press('Escape');
    // The existing movements screen is /inventory, not /movements.
    const routes = ['sales/new','sales/quotes','sales/customers','sales','products','inventory','categories','suppliers'];
    for (const route of routes) {
        const response = await p.goto('http://127.0.0.1:5000/'+route+'?ref=1',{waitUntil:'networkidle'}); assert.equal(response.status(),200,route);
        await p.evaluate(() => document.fonts.ready); await expect(p.locator('#ol-fixture')).toHaveCount(0);
        await expect(p.locator('#ix-sidebar')).toContainText('Análisis predictivo');
        const shot = await p.screenshot({path:out+'/after-'+route.replaceAll('/','-')+'.png'});
        const before = out+'/before-'+route.replaceAll('/','-')+'.png', after = out+'/after-'+route.replaceAll('/','-')+'.png';
        if (!fs.readFileSync(before).equals(shot)) {
            const count = Number(execFileSync('.venv/Scripts/python.exe',['-c', 'from PIL import Image,ImageChops; import sys; d=ImageChops.difference(Image.open(sys.argv[1]),Image.open(sys.argv[2])); print(sum(1 for p in d.get_flattened_data() if any(p)))',before,after],{encoding:'utf8'}).trim());
            console.log('Regression raster difference:',route,count,'pixels');
            assert(count <= 10,'Regression pixel drift: '+route);
        }
    }
    await p.goto('http://127.0.0.1:5000/sales/orders',{waitUntil:'networkidle'});
    await expect(p.locator('#ol-fixture')).toHaveCount(0); await expect(p.locator('script[src*="ix_order_layers"]')).toHaveCount(0);
    await go(); await p.setViewportSize({width:390,height:844});
    for (const i of [0,1,2,3]) { await open(i); const r = await p.locator('#ol-'+ids[i]).boundingBox(); assert(r.x >= 0 && r.x+r.width <= 390); await p.screenshot({path:out+'/'+ids[i]+'-mobile.png'}); await p.keyboard.press('Escape'); }
    assert.deepEqual(writes,[]); assert.deepEqual(errors,[]);
    console.log('PASS: unchanged base; four layers; product quantities/add/remove/totals; simulated save/reload; date presets/custom/cancel/apply; five statuses; all advanced filters; intersection/reset; pagination; closures/focus/one layer; all nine requested routes (maximum 10 pixel raster tolerance); normal isolation; mobile; no write requests; no JS errors.');
    await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
