// Run only against the no-database test_settings_reference harness on port 5000.
const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const output = '.tmp/supplier-modal-validation';
(async () => {
    const browser = await chromium.launch({channel:'chrome', headless:true});
    const context = await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1,ignoreHTTPSErrors:true});
    const page = await context.newPage();
    const errors = [], requests = [], downloads = [], regressions = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('download', download => downloads.push(download.suggestedFilename()));
    await context.route('**/*', async route => {
        if (!['GET','HEAD'].includes(route.request().method()) || new URL(route.request().url()).pathname.startsWith('/api/')) {
            requests.push(route.request().method());
            return route.abort();
        }
        await route.continue();
    });
    await page.goto('http://127.0.0.1:5000/suppliers?ref=1',{waitUntil:'networkidle'});
    assert.deepEqual(await page.evaluate(() => [innerWidth,innerHeight,devicePixelRatio,visualViewport.scale]),[1600,900,1,1]);
    await page.evaluate(() => document.fonts.ready);
    const base = await page.screenshot({path:output+'/base-after.png'});
    const baseline = await context.newPage();
    await baseline.route('**/suppliers?ref=1', async route => {
        const response = await route.fetch();
        const html = (await response.text()).replace(/<link[^>]+ix_supplier_modals\.css[^>]*>/,'')
            .replace(/<script[^>]+ix_supplier_modals\.js[^>]*><\/script>/,'');
        await route.fulfill({response,body:html});
    });
    await baseline.goto('http://127.0.0.1:5000/suppliers?ref=1',{waitUntil:'networkidle'});
    await baseline.evaluate(() => document.fonts.ready);
    assert(base.equals(await baseline.screenshot({path:output+'/base-without-modals.png'})), 'Approved base must remain pixel-identical');
    await baseline.close();
    const table = await page.locator('.iv-sup-table-card').innerHTML();
    const interactionRequests = [];
    const observe = request => interactionRequests.push(request.url());
    page.on('request', observe);
    for (const [id, name] of [['filters','Filtros'],['export','Exportar CSV']]) {
        const trigger = page.getByRole('button',{name,exact:true});
        const modal = page.locator('#sm-'+id);
        for (const close of ['X','Cancelar','Escape']) {
            await trigger.click();
            await expect(modal).toBeVisible();
            assert.equal(await page.locator('dialog[open]').count(),1);
            assert.equal(await page.evaluate(() => document.body.style.overflow),'hidden');
            if (close === 'X') {
                await page.screenshot({path:output+'/'+id+'-'+(process.env.SUPPLIER_MODAL_STAGE || 'initial')+'.png'});
                console.log(id, await modal.boundingBox());
                for (let i=0;i<22;i++) {
                    await page.keyboard.press('Tab');
                    assert(await modal.evaluate(node => node.contains(document.activeElement)));
                }
                await modal.locator('.sm-close').click();
            } else if (close === 'Escape') await page.keyboard.press('Escape');
            else await modal.getByRole('button',{name:close,exact:true}).click();
            await expect(modal).toBeHidden();
            await expect(trigger).toBeFocused();
            assert.equal(await page.evaluate(() => document.body.style.overflow),'');
        }
    }
    const filters = page.locator('#sm-filters');
    await page.getByRole('button',{name:'Filtros',exact:true}).click();
    assert.equal(await filters.locator('select').count(),5);
    assert.equal(await filters.locator('input').count(),5);
    await page.locator('#sm-search').fill('Prueba');
    await page.locator('#sm-status').selectOption('Activo');
    await page.locator('#sm-min').fill('100');
    await filters.getByRole('button',{name:'Limpiar filtros'}).click();
    await expect(page.locator('#sm-search')).toHaveValue('');
    await expect(page.locator('#sm-status')).toHaveValue('');
    await expect(page.locator('#sm-min')).toHaveValue('');
    await filters.getByRole('button',{name:'Aplicar filtros'}).click();
    await expect(filters).toBeHidden();
    await expect(page.locator('#sm-feedback')).toContainText('Filtros simulados');
    await page.getByRole('button',{name:'Exportar CSV',exact:true}).click();
    const exporter = page.locator('#sm-export');
    await expect(exporter.getByRole('radio',{name:'CSV',exact:true})).toBeChecked();
    for (const name of ['Excel (XLSX)','PDF','CSV']) {
        await exporter.getByRole('radio',{name,exact:true}).check();
        await expect(exporter.getByRole('radio',{name,exact:true})).toBeChecked();
    }
    for (const checkbox of await exporter.getByRole('checkbox').all()) {
        await expect(checkbox).toBeChecked();
        await checkbox.uncheck();
        await expect(checkbox).not.toBeChecked();
        await checkbox.check();
    }
    await exporter.getByRole('button',{name:'Exportar',exact:true}).click();
    await expect(exporter).toBeHidden();
    await expect(page.locator('#sm-feedback')).toContainText('Exportación CSV simulada');
    // Programmatic activation covers exclusive-layer coordination despite modal background inertness.
    for (const name of ['Nuevo proveedor','Filtros','Exportar CSV','Nuevo proveedor','Exportar CSV','Filtros']) {
        await page.getByRole('button',{name,exact:true}).evaluate(button => button.click());
        await page.waitForTimeout(60);
        assert.equal(await page.locator('dialog[open]').count(),1);
        assert.equal(await page.evaluate(() => document.body.style.overflow),'hidden');
        assert(await page.locator('dialog[open]').evaluate(node => node.contains(document.activeElement)));
    }
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button',{name:'Filtros',exact:true})).toBeFocused();
    assert.equal(await page.evaluate(() => document.body.style.overflow),'');
    assert.equal(await page.locator('.iv-sup-table-card').innerHTML(),table);
    await page.getByRole('button',{name:'Nuevo proveedor',exact:true}).click();
    const drawer = page.locator('#sl-dialog');
    await drawer.getByRole('button',{name:'Guardar proveedor'}).click();
    await expect(page.locator('#sl-error')).toBeVisible();
    for (const [id,value] of Object.entries({name:'Proveedor visual',rif:'J-12345678-9',contact:'Contacto visual',phone:'0412-5551234',email:'prueba@example.invalid'})) {
        await page.locator('#sl-'+id).fill(value);
    }
    await page.locator('#sl-category').selectOption({label:'Materiales'});
    await drawer.getByRole('button',{name:'Guardar proveedor'}).click();
    await expect(drawer).toBeHidden();
    await expect(page.locator('#sl-feedback')).toContainText('No se ha creado');
    await expect(page.getByRole('button',{name:'Nuevo proveedor',exact:true})).toBeFocused();
    assert.equal(await page.locator('.iv-sup-table-card').innerHTML(),table);
    await page.getByRole('button',{name:'Página siguiente'}).click();
    await page.getByRole('button',{name:'Ver detalle',exact:true}).first().click();
    await page.getByRole('button',{name:'Más acciones',exact:true}).first().click();
    assert.equal(await page.locator('.iv-sup-table-card').innerHTML(),table);
    assert.deepEqual(interactionRequests,[]);
    page.off('request',observe);
    for (const path of ['suppliers','products','categories','inventory','dashboard']) {
        const response = await page.goto('http://127.0.0.1:5000/'+path+'?ref=1',{waitUntil:'networkidle'});
        regressions.push({path,status:response.status()});
        if (response.status() === 200) await expect(page.getByRole('button',{name:/Análisis predictivo/})).toBeVisible();
        if (path !== 'suppliers') assert.equal(await page.locator('.sm-dialog').count(),0);
    }
    await page.goto('http://127.0.0.1:5000/suppliers?ref=0',{waitUntil:'networkidle'});
    assert.equal(await page.locator('.sm-dialog').count(),0);
    await page.goto('http://127.0.0.1:5000/suppliers?ref=1',{waitUntil:'networkidle'});
    await page.setViewportSize({width:390,height:844});
    for (const name of ['Filtros','Exportar CSV']) {
        await page.getByRole('button',{name,exact:true}).click();
        const modal = page.locator('dialog[open]');
        assert((await modal.boundingBox()).width <=390);
        assert(await modal.evaluate(node => node.scrollWidth <= node.clientWidth));
        await modal.locator('.sm-footer button').last().scrollIntoViewIfNeeded();
        await expect(modal.locator('.sm-footer button').last()).toBeInViewport();
        await page.screenshot({path:output+'/'+(name === 'Filtros'?'filters':'export')+'-mobile.png'});
        await page.keyboard.press('Escape');
    }
    assert.deepEqual(requests,[]);
    assert.deepEqual(downloads,[]);
    assert.deepEqual(errors,[]);
    console.log('PASS: identical base; modal fields/reset/simulated apply; export formats/options/simulation; X/Cancel/Escape/focus/trap; one layer including drawer; mobile; no interaction requests/downloads/JS errors; normal mode isolated.');
    console.log('Reference routes:',JSON.stringify(regressions));
    console.log('Existing fixture pagination and row buttons remain static, as before.');
    await browser.close();
})().catch(error => {console.error(error);process.exit(1);});
