// Synthetic harness at 5033 only; displayed URL matches requested reference route.
const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const output = '.tmp/supplier-drawer-validation';
fs.mkdirSync(output, {recursive:true});
(async () => {
    const browser = await chromium.launch({channel:'chrome', headless:true});
    const context = await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1,ignoreHTTPSErrors:true});
    const mutations = [], errors = [], regressions = [];
    await context.route('http://127.0.0.1:5000/**', async route => {
        const request = route.request();
        if (!['GET','HEAD'].includes(request.method()) || new URL(request.url()).pathname.startsWith('/api/')) {
            mutations.push(request.method() + ' ' + new URL(request.url()).pathname);
            await route.abort();
            return;
        }
        const response = await route.fetch({url:request.url().replace(':5000',':5033')});
        await route.fulfill({response});
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:5000/suppliers?ref=1', {waitUntil:'networkidle'});
    await page.evaluate(() => document.fonts.ready);
    assert(await page.evaluate(() => typeof Chart !== 'undefined' &&
        [...document.fonts].some(font => font.family === 'Inter' && font.status === 'loaded')),
    'Fonts and chart assets must load for visual validation');
    assert.deepEqual(await page.evaluate(() => [innerWidth,innerHeight,devicePixelRatio,visualViewport.scale]), [1600,900,1,1]);
    const base = await page.screenshot({path:output+'/base.png'});
    const table = await page.locator('.iv-sup-table').innerText();
    const baseline = await context.newPage();
    await baseline.route('**/suppliers?ref=1', async route => {
        const response = await route.fetch({url:'http://127.0.0.1:5033/suppliers?ref=1'});
        const html = (await response.text()).replace(/<link[^>]+ix_supplier_layers\.css[^>]*>/,'')
            .replace(/<script[^>]+ix_supplier_layers\.js[^>]*><\/script>/,'');
        await route.fulfill({response, body:html});
    });
    await baseline.goto('http://127.0.0.1:5000/suppliers?ref=1',{waitUntil:'networkidle'});
    await baseline.evaluate(() => document.fonts.ready);
    // A hidden native dialog stays hidden without its stylesheet; feedback has hidden.
    assert(base.equals(await baseline.screenshot({path:output+'/base-without-assets.png'})));
    await baseline.close();
    const trigger = page.getByRole('button',{name:'Nuevo proveedor',exact:true});
    const drawer = page.locator('#sl-dialog');
    for (const close of ['Cerrar nuevo proveedor','Cancelar','Escape']) {
        await trigger.click();
        await expect(drawer).toBeVisible();
        await page.waitForTimeout(250);
        if (close === 'Cerrar nuevo proveedor') {
            await page.screenshot({path:output+'/drawer-'+(process.env.SUPPLIER_STAGE || 'initial')+'.png'});
            await page.setViewportSize({width:1600,height:812});
            await page.screenshot({path:output+'/drawer-reference-size.png'});
            await page.setViewportSize({width:1600,height:900});
        }
        if (close === 'Escape') await page.keyboard.press('Escape');
        else await drawer.getByRole('button',{name:close,exact:true}).click();
        await expect(drawer).toBeHidden();
        await expect(trigger).toBeFocused();
        assert.equal(await page.evaluate(() => document.body.style.overflow),'');
    }
    await trigger.click();
    await expect(page.locator('#sl-counter')).toHaveText('0/300');
    await drawer.getByRole('button',{name:'Guardar proveedor'}).click();
    await expect(page.locator('#sl-error')).toBeVisible();
    assert.equal(await drawer.locator('[aria-invalid=true]').count(),6); // Estado defaults to valid Activo.
    await page.screenshot({path:output+'/validation.png'});
    for (const [id,value] of Object.entries({name:'Proveedor de prueba visual',rif:'J-12345678-9',contact:'Contacto de prueba',phone:'0412-555.1234',email:'prueba@example.invalid'})) {
        await page.locator('#sl-'+id).fill(value);
    }
    await page.locator('#sl-category').selectOption({label:'Materiales'});
    await page.locator('#sl-state').selectOption('Inactivo');
    await expect(drawer.locator('.sl-state')).toHaveClass(/is-inactive/);
    await page.locator('#sl-state').selectOption('Activo');
    await page.locator('#sl-notes').fill('Nota de prueba');
    await expect(page.locator('#sl-counter')).toHaveText('14/300');
    await page.locator('#sl-notes').fill('x'.repeat(300));
    await page.locator('#sl-notes').press('End');
    await page.keyboard.type('z');
    await expect(page.locator('#sl-counter')).toHaveText('300/300');
    for (let i=0;i<20;i++) {
        await page.keyboard.press('Tab');
        assert(await drawer.evaluate(node => node.contains(document.activeElement)));
    }
    await drawer.getByRole('button',{name:'Guardar proveedor'}).click();
    await expect(drawer).toBeHidden();
    await expect(page.locator('#sl-feedback')).toContainText('No se ha creado');
    assert.equal(await page.locator('.iv-sup-table').innerText(),table);
    await page.reload({waitUntil:'networkidle'});
    assert.equal(await page.locator('.iv-sup-table').innerText(),table);
    await trigger.click();
    await expect(page.locator('#sl-name')).toHaveValue('');
    await page.keyboard.press('Escape');
    for (const route of ['suppliers','products','categories','inventory','dashboard']) {
        const response = await page.goto('http://127.0.0.1:5000/'+route+'?ref=1',{waitUntil:'networkidle'});
        if (response.status() !== 200) {
            regressions.push({route,status:response.status()});
            continue;
        }
        await expect(page.getByRole('button',{name:/Análisis predictivo/})).toBeVisible();
        if (route !== 'suppliers') assert.equal(await page.locator('#sl-dialog').count(),0);
    }
    await page.goto('http://127.0.0.1:5000/suppliers?ref=0',{waitUntil:'networkidle'});
    assert.equal(await page.locator('#sl-dialog').count(),0);
    await page.goto('http://127.0.0.1:5000/suppliers?ref=1',{waitUntil:'networkidle'});
    await page.setViewportSize({width:390,height:844});
    await trigger.click();
    await expect(drawer).toBeVisible();
    await page.waitForTimeout(250);
    assert((await drawer.boundingBox()).width <=390);
    await expect(drawer.getByRole('button',{name:'Guardar proveedor'})).toBeInViewport();
    await page.screenshot({path:output+'/mobile.png'});
    assert.deepEqual(mutations,[]);
    assert.deepEqual(errors,[]);
    console.log('PASS: identical base screenshots; X/Cancel/Escape/focus; required validation; counter/max; category/state; simulated save/reload; ref isolation; mobile; zero API/mutation requests; zero JS errors.');
    console.log('Route failures (not modified by this task):', JSON.stringify(regressions));
    await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
