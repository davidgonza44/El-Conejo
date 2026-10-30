// Run only with the inspected, no-database test_settings_reference.py harness on port 5000.
// All interactions below are on deterministic reference views; mutation/API requests are blocked.
const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const output = process.env.REPLENISHMENT_VALIDATION_OUTPUT || '.tmp/replenishment-layers-validation';
function comparePixels(before, after) {
    const {PNG} = require('playwright-core/lib/utilsBundle');
    const a = PNG.sync.read(fs.readFileSync(before)), b = PNG.sync.read(fs.readFileSync(after));
    assert.deepEqual([a.width,a.height],[b.width,b.height]);
    let changed = 0;
    for (let i=0; i<a.data.length; i+=4) {
        if (a.data.readUInt32LE(i) !== b.data.readUInt32LE(i)) changed++;
    }
    assert(changed <= 10, after + ': ' + changed + ' changed pixels (10-pixel rasterization tolerance)');
    console.log('Pixel comparison:', after.split('/').pop(), changed, 'of 1440000');
}
(async () => {
    fs.mkdirSync(output, {recursive:true});
    const browser = await chromium.launch({channel:'chrome',headless:true});
    const context = await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1});
    const page = await context.newPage();
    const errors = [], mutations = [], downloads = [], interactionRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('download', item => downloads.push(item.suggestedFilename()));
    await context.route('**/*', async route => {
        const request = route.request();
        if (!['GET','HEAD'].includes(request.method()) || new URL(request.url()).pathname.startsWith('/api/')) {
            mutations.push(request.method() + ' ' + new URL(request.url()).pathname);
            return route.abort();
        }
        await route.continue();
    });
    const originalHTML = output + '/baseline-replenishment.html';
    if (fs.existsSync(originalHTML)) {
        const baselinePage = await context.newPage();
        await baselinePage.route('**/predictive/replenishment?ref=1', route =>
            route.fulfill({status:200,contentType:'text/html',body:fs.readFileSync(originalHTML,'utf8')}));
        await baselinePage.goto('http://127.0.0.1:5000/predictive/replenishment?ref=1',{waitUntil:'networkidle'});
        await baselinePage.evaluate(() => document.fonts.ready);
        await baselinePage.screenshot({path:output+'/before-_predictive_replenishment.png'});
        await baselinePage.close();
    }
    await page.goto('http://127.0.0.1:5000/predictive/replenishment?ref=1',{waitUntil:'networkidle'});
    await page.evaluate(() => document.fonts.ready);
    assert.deepEqual(await page.evaluate(() => [innerWidth,innerHeight,devicePixelRatio,visualViewport.scale]),[1600,900,1,1]);
    await page.screenshot({path:output+'/base-after.png'});
    const baselinePath = output+'/before-_predictive_replenishment.png';
    if (fs.existsSync(baselinePath)) comparePixels(baselinePath,output+'/base-after.png');
    const originalTable = (await page.locator('.is-replenishment tbody').innerHTML()).replace(/>\s+</g,'><').trim();
    await expect(page.locator('#ix-sidebar')).toContainText('Análisis predictivo');
    page.on('request', request => interactionRequests.push(request.url()));
    const trigger = (name) => page.getByRole('button',{name,exact:true});
    const open = async (id, name) => {
        await trigger(name).click();
        await expect(page.locator('#rl-'+id)).toBeVisible();
        assert.equal(await page.locator('dialog[open]').count(),1);
    };
    async function verifyLayerLifecycle() {
        for (const [id,name] of [['order','Generar orden de compra'],['export','Exportar CSV'],['filters','Filtros']]) {
            const dialog = page.locator('#rl-'+id);
            for (const closing of ['X','Cancelar','Escape','outside']) {
                await open(id,name);
                assert.equal(await page.evaluate(() => document.body.style.overflow),'hidden');
                if (closing === 'X') {
                    await page.screenshot({path:output+'/'+id+'-final.png'});
                    for (let i=0;i<30;i++) {
                        await page.keyboard.press('Tab');
                        assert(await dialog.evaluate(node => node.contains(document.activeElement)), 'Focus must stay in the dialog');
                    }
                    await dialog.locator('.rl-close').click();
                } else if (closing === 'Cancelar') await dialog.getByRole('button',{name:'Cancelar',exact:true}).click();
                else if (closing === 'Escape') await page.keyboard.press('Escape');
                else await page.mouse.click(300,100);
                await expect(dialog).toBeHidden();
                await expect(trigger(name)).toBeFocused();
                assert.equal(await page.evaluate(() => document.body.style.overflow),'');
            }
        }
        console.log('PASS: three layers, X/Cancel/Escape/outside, focus trap/return, scroll lock');
    }
    await verifyLayerLifecycle();

    async function verifyOrder() {
        await open('order','Generar orden de compra');
        await page.locator('#rl-order-supplier').selectOption('Suministros del Centro');
        await page.locator('#rl-order-branch').selectOption('Sucursal Norte');
        await page.locator('#rl-payment').selectOption('cash');
        await page.locator('#rl-notes').fill('Entrega de prueba visual.');
        await expect(page.locator('#rl-note-count')).toHaveText('25/500');
        assert.equal(await page.locator('#rl-notes').getAttribute('maxlength'),'500');
        await expect(page.locator('#rl-subtotal')).toHaveText('$ 11,150.00');
        await expect(page.locator('#rl-tax')).toHaveText('$ 1,784.00');
        await expect(page.locator('#rl-total')).toHaveText('$ 12,934.00');
        const totalBox = await page.locator('#rl-total').boundingBox();
        const footerBox = await page.locator('#rl-order .rl-footer').boundingBox();
        assert(totalBox.y + totalBox.height <= footerBox.y, 'Total must be visible above footer');
        await page.locator('#rl-delivery').click();
        await expect(page.locator('#rl-calendar')).toBeVisible();
        await page.screenshot({path:output+'/delivery-calendar.png'});
        await page.getByRole('button',{name:'18 de junio de 2024',exact:true}).click();
        await expect(page.locator('#rl-delivery')).toHaveValue('18/06/2024');
        await page.locator('#rl-delivery').press('Enter');
        await page.getByRole('button',{name:'Mes siguiente',exact:true}).click();
        await page.getByRole('button',{name:'Mes anterior',exact:true}).click();
        await page.keyboard.press('Escape');
        await expect(page.locator('#rl-calendar')).toBeHidden();
        await expect(page.locator('#rl-order')).toBeVisible();
        await page.locator('#rl-delivery').click();
        await page.getByRole('button',{name:'Borrar fecha',exact:true}).click();
        await page.locator('#rl-order').getByRole('button',{name:'Generar orden',exact:true}).click();
        await expect(page.locator('#rl-calendar')).toBeVisible();
        await page.getByRole('button',{name:'15 de junio de 2024',exact:true}).click();
        await page.locator('#rl-order').getByRole('button',{name:'Generar orden',exact:true}).click();
        await expect(page.locator('#rl-feedback')).toContainText('no se creó ninguna orden');
        console.log('PASS: order selects, date picker/day/month/keyboard, notes, totals, simulation');
    }
    await verifyOrder();

    async function verifyExport() {
        await open('export','Exportar CSV');
        const exporter = page.locator('#rl-export');
        await expect(exporter.getByRole('radio',{name:'CSV',exact:true})).toBeChecked();
        for (const name of ['Excel (XLSX)','PDF','CSV']) {
            await exporter.getByRole('radio',{name,exact:true}).check();
            await expect(exporter.getByRole('radio',{name,exact:true})).toBeChecked();
        }
        assert.equal(await exporter.getByRole('checkbox').count(),6);
        for (const checkbox of await exporter.getByRole('checkbox').all()) {
            await expect(checkbox).toBeChecked();
            await checkbox.uncheck();
            await expect(checkbox).not.toBeChecked();
            await checkbox.check();
        }
        await expect(exporter.locator('input[value="filtered"]')).toBeChecked();
        await exporter.locator('input[value="page"]').check();
        await expect(exporter.locator('input[value="page"]')).toBeChecked();
        await exporter.getByRole('button',{name:'Exportar',exact:true}).click();
        await expect(page.locator('#rl-feedback')).toContainText('no se generó ni descargó');
        console.log('PASS: formats, six options, scope default/toggle, export simulation');
    }
    await verifyExport();

    async function verifyFilters() {
        await open('filters','Filtros');
        const filters = page.locator('#rl-filters');
        assert.equal(await filters.locator('select').count(),5);
        assert.equal(await filters.locator('input[type="number"]').count(),6);
        for (const [id, day] of [['from',4],['to',6]]) {
            await page.locator('#rl-'+id).click();
            await expect(page.locator('#rl-calendar')).toBeVisible();
            await page.screenshot({path:output+'/filter-'+id+'-calendar.png'});
            await page.getByRole('button',{name:day+' de junio de 2024',exact:true}).click();
            await expect(page.locator('#rl-'+id)).toHaveValue('0'+day+'/06/2024');
        }
        await page.locator('#rl-category').selectOption('Construcción');
        await page.locator('#rl-supplier').selectOption('Distribuidora Andina');
        await page.locator('#rl-branch').selectOption('Sucursal Principal');
        await page.locator('#rl-priority').selectOption('Alta');
        await page.locator('#rl-stock-min').fill('20');
        await page.locator('#rl-stock-max').fill('50');
        await page.locator('#rl-demand-min').fill('100');
        await page.locator('#rl-demand-max').fill('250');
        await page.locator('#rl-cost-min').fill('4000');
        await page.locator('#rl-cost-max').fill('5000');
        await page.locator('#rl-high').check();
        await page.locator('#rl-critical').check();
        await filters.getByRole('button',{name:'Aplicar filtros',exact:true}).click();
        await expect(filters).toBeHidden();
        await expect(page.locator('.is-replenishment tbody tr')).toHaveCount(1);
        await expect(page.locator('.is-replenishment tbody')).toContainText('Varilla');
        await expect(page.locator('.px-pagination-summary')).toHaveText('Mostrando 1 a 1 de 1 productos');
        await expect(page.locator('#rl-inline-category')).toHaveValue('Construcción');
        await page.screenshot({path:output+'/filters-applied.png'});
        await open('filters','Filtros');
        await expect(page.locator('#rl-from')).toHaveValue('04/06/2024');
        await expect(page.locator('#rl-to')).toHaveValue('06/06/2024');
        await page.locator('#rl-category').selectOption('Pinturas');
        await filters.getByRole('button',{name:'Cancelar',exact:true}).click();
        await expect(page.locator('#rl-inline-category')).toHaveValue('Construcción');
        await open('filters','Filtros');
        await filters.getByRole('button',{name:'Limpiar filtros',exact:true}).click();
        await expect(page.locator('#rl-from')).toHaveValue('');
        await expect(page.locator('#rl-to')).toHaveValue('');
        await expect(page.locator('#rl-high')).not.toBeChecked();
        await expect(page.locator('#rl-critical')).not.toBeChecked();
        assert.equal((await page.locator('.is-replenishment tbody').innerHTML()).replace(/>\s+</g,'><').trim(),originalTable);
        await page.locator('#rl-stock-min').fill('50');
        await page.locator('#rl-stock-max').fill('20');
        await filters.getByRole('button',{name:'Aplicar filtros',exact:true}).click();
        await expect(page.locator('#rl-filter-error')).toContainText('mínimo');
        await filters.getByRole('button',{name:'Limpiar filtros',exact:true}).click();
        await page.locator('#rl-from').click();
        await page.getByRole('button',{name:'10 de junio de 2024',exact:true}).click();
        await page.locator('#rl-to').click();
        await page.getByRole('button',{name:'3 de junio de 2024',exact:true}).click();
        await filters.getByRole('button',{name:'Aplicar filtros',exact:true}).click();
        await expect(page.locator('#rl-filter-error')).toContainText('posterior');
        await filters.getByRole('button',{name:'Limpiar filtros',exact:true}).click();
        await filters.getByRole('button',{name:'Aplicar filtros',exact:true}).click();
        const search = page.locator('.px-filters input[type="search"]');
        await search.fill('inexistente');
        await expect(page.locator('.is-replenishment tbody')).toContainText('No hay recomendaciones');
        await expect(page.locator('.px-pagination-summary')).toHaveText('Mostrando 0 de 0 productos');
        await page.locator('.px-filters .px-clear').click();
        await page.locator('#rl-inline-category').selectOption('Pinturas');
        await open('filters','Filtros');
        await expect(page.locator('#rl-category')).toHaveValue('Pinturas');
        await filters.getByRole('button',{name:'Cancelar',exact:true}).click();
        await expect(page.locator('.is-replenishment tbody tr')).toHaveCount(1);
        await page.locator('.px-filters .px-clear').click();
        console.log('PASS: filter calendars, intersection of all criteria, counts, cancel draft, reset, invalid ranges, empty state, inline sync');
    }
    await verifyFilters();

    async function verifyIsolation() {
        await open('filters','Filtros');
        // Native modal background is inert; dispatch the other existing trigger to verify layer replacement.
        await trigger('Exportar CSV').evaluate(button => button.click());
        await expect(page.locator('#rl-filters')).toBeHidden();
        await expect(page.locator('#rl-export')).toBeVisible();
        await trigger('Generar orden de compra').evaluate(button => button.click());
        assert.equal(await page.locator('dialog[open]').count(),1);
        await expect(page.locator('#rl-order')).toBeVisible();
        await page.keyboard.press('Escape');
        assert.equal(interactionRequests.length,0,'Interactions must issue zero requests');
        assert.equal(downloads.length,0);
        assert.equal(mutations.length,0);
        page.removeAllListeners('request');
        await page.reload({waitUntil:'networkidle'});
        await open('order','Generar orden de compra');
        await expect(page.locator('#rl-notes')).toHaveValue('');
        await expect(page.locator('#rl-delivery')).toHaveValue('15/06/2024');
        await page.keyboard.press('Escape');
        assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length),0);
        console.log('PASS: single layer replacement, zero interaction requests/downloads/storage, reload resets simulation');
    }
    await verifyIsolation();

    async function verifyRegressions() {
        const paths=['/predictive/demand','/predictive/critical','/predictive/trends','/predictive/accuracy','/inventory','/products','/categories','/suppliers','/sales/new'];
        for (const path of paths) {
            const response=await page.goto('http://127.0.0.1:5000'+path+'?ref=1',{waitUntil:'networkidle'});
            assert.equal(response.status(),200,path);
            await page.evaluate(()=>document.fonts.ready);
            assert.equal(await page.locator('[id^="rl-"]').count(),0);
            await page.screenshot({path:output+'/after-'+path.replaceAll('/','_')+'.png'});
            const before=output+'/before-'+path.replaceAll('/','_')+'.png';
            if(fs.existsSync(before)) comparePixels(before,output+'/after-'+path.replaceAll('/','_')+'.png');
        }
        await page.goto('http://127.0.0.1:5000/predictive/replenishment',{waitUntil:'networkidle'});
        assert.equal(await page.locator('[id^="rl-"]').count(),0);
        assert.equal(await page.locator('[src*="ix_replenishment_layers"],[href*="ix_replenishment_layers"]').count(),0);
        console.log('PASS: nine regression routes render without replenishment layers, normal mode has no layer markup or assets');
    }
    await verifyRegressions();
    async function verifyMobile() {
        await page.setViewportSize({width:390,height:844});
        await page.goto('http://127.0.0.1:5000/predictive/replenishment?ref=1',{waitUntil:'networkidle'});
        for (const [id,name] of [['order','Generar orden de compra'],['export','Exportar CSV'],['filters','Filtros']]) {
            await trigger(name).evaluate(button=>button.click());
            const dialog=page.locator('#rl-'+id);
            const box=await dialog.boundingBox();
            assert(box.x>=0 && box.x+box.width<=391,'Mobile layer bounds');
            await dialog.getByRole('button',{name:'Cancelar',exact:true}).click();
        }
    }
    await verifyMobile();
    assert.deepEqual(errors,[]);
    assert.deepEqual(mutations,[]);
    assert.deepEqual(downloads,[]);
    await browser.close();
    console.log('PASS: mobile layers usable; no browser errors or mutations');
})().catch(error=>{console.error(error);process.exit(1);});
