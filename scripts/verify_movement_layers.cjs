// Uses the existing no-database reference harness on port 5000.
const {chromium} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const stage = process.env.MOVEMENT_STAGE || 'initial';
const output = '.tmp/movement-layers-validation';
fs.mkdirSync(output, {recursive: true});
(async () => {
    const browser = await chromium.launch({channel: 'msedge'});
    const page = await browser.newPage({viewport: {width: 1600, height: 900}, deviceScaleFactor: 1});
    const errors = [], mutations = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
        requests.push(request.url());
        if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(request.method());
    });
    await page.goto('http://127.0.0.1:5000/inventory?ref=1', {waitUntil: 'networkidle'});
    await page.evaluate(() => document.fonts.ready);
    assert(await page.locator('[aria-controls="ix-nav-pred"]').isVisible());
    const dashboard = await page.locator('.iv-mov-body').innerHTML();
    await page.screenshot({path: `${output}/base-${stage}.png`});
    // Reconstruct the original response for comparison without changing any source file.
    const baseline = await browser.newPage({viewport:{width:1600,height:900},deviceScaleFactor:1});
    await baseline.route('**/inventory?ref=1', async route => {
        const response = await route.fetch();
        const html = (await response.text())
            .replace(/<link[^>]+ix_movement_layers\.css[^>]*>/, '')
            .replace(/<script[^>]+ix_movement_layers\.js[^>]*><\/script>/, '')
            .replace(/<button type="button" class="iv-activity-more mv-activity-trigger" data-mv-open="activities">([^<]+)<\/button>/,
                '<a href="#" class="iv-activity-more" aria-disabled="true">$1</a>');
        // Without layer CSS, the new dialog must not participate in the baseline at all.
        await route.fulfill({response,body:html.replace(/<div id="mv-overlay"[\s\S]*?(?=<\/main>)/, '')});
    });
    await baseline.goto('http://127.0.0.1:5000/inventory?ref=1',{waitUntil:'networkidle'});
    await baseline.evaluate(() => document.fonts.ready);
    await baseline.screenshot({path:`${output}/base-original-${stage}.png`});
    const activityStyle = p => p.locator('.iv-activity-more').evaluate(node => {
        const style = getComputedStyle(node);
        const range = document.createRange(); range.selectNodeContents(node);
        const bounds = range.getBoundingClientRect();
        return {fontFamily:style.fontFamily,fontSize:style.fontSize,fontWeight:style.fontWeight,color:style.color,lineHeight:style.lineHeight,x:bounds.x,y:bounds.y,width:bounds.width,height:bounds.height};
    });
    assert.deepEqual(await activityStyle(page), await activityStyle(baseline), 'Activity action preserves its original visual style');
    await baseline.close();
    const triggers = {
        new: page.locator('.iv-toolbar-actions button').filter({hasText: 'Nuevo movimiento'}),
        detail: page.locator('[data-movement-ref="ENT-0098"] [aria-label="Ver detalle"]'),
        export: page.locator('.iv-toolbar-actions button').filter({hasText: 'Exportar'}),
        activities: page.locator('.mv-activity-trigger'),
        filters: page.locator('.iv-toolbar-actions button').filter({hasText: 'Filtros'}),
    };
    const geometry = {};
    for (const [state, trigger] of Object.entries(triggers)) {
        const start = requests.length;
        await trigger.click();
        await page.waitForTimeout(230);
        const panel = page.locator(state === 'export' ? '#mv-export' : '#mv-drawer');
        assert(await panel.isVisible());
        geometry[state] = await panel.boundingBox();
        await page.screenshot({path: `${output}/${state}-${stage}.png`});
        if (state === 'detail') {
            assert.match(await panel.innerText(), /ENT-0098/);
            assert.match(await panel.innerText(), /Historial del movimiento/);
            assert.match(await panel.innerText(), /138 uds\./);
        }
        if (state !== 'export') assert.equal(await page.locator('#ix-shell').evaluate(node => node.inert), true);
        await page.keyboard.press('Escape');
        assert(await panel.isHidden());
        assert(await trigger.evaluate(node => node === document.activeElement));
        assert.equal(await page.locator('#ix-shell').evaluate(node => node.inert), false);
        // Thumbnails already exist on the base; layers must not contact an API.
        assert(!requests.slice(start).some(url => url.includes('/api/')));
    }
    await triggers.new.click();
    await page.locator('#mv-kind').selectOption('Entrada');
    await page.locator('#mv-product').selectOption('ENT-0098');
    await page.locator('#mv-user').selectOption('Carlos Mendoza');
    await page.locator('#mv-quantity').fill('10');
    await page.getByRole('button', {name:'Guardar movimiento', exact:true}).click();
    assert(await page.locator('#mv-drawer').isHidden());
    const pendingEye = page.locator('[data-movement-ref="AJU-0025"] [aria-label="Ver detalle"]');
    const editPending = async () => {
        await page.getByRole('button', {name:'Editar movimiento', exact:true}).click();
        assert.equal(await page.locator('#mv-drawer').getAttribute('data-state'), 'edit');
        assert.equal(await page.locator('[role="dialog"]:visible').count(), 1);
        assert.equal(await page.locator('#mv-reference').inputValue(), 'AJU-0025');
    };
    await pendingEye.click();
    assert(await page.locator('[data-mv-correction]').count() === 0);
    await editPending();
    assert.equal(await page.locator('#mv-quantity').inputValue(), '3');
    await page.screenshot({path: `${output}/edit-${stage}.png`});
    await page.getByRole('button', {name:'Cancelar', exact:true}).click();
    assert.equal(await page.locator('#mv-drawer').getAttribute('data-state'), 'detail');
    assert.equal(await page.locator('[role="dialog"]:visible').count(), 1);
    await editPending();
    await page.locator('#mv-quantity').fill('17');
    await page.getByRole('button', {name:'Guardar cambios', exact:true}).click();
    assert(await page.locator('#mv-drawer').isHidden());
    assert(await pendingEye.evaluate(node => node === document.activeElement));
    await page.reload({waitUntil:'networkidle'});
    await pendingEye.click();
    await editPending();
    assert.equal(await page.locator('#mv-quantity').inputValue(), '3');
    await page.getByRole('button', {name:'Cancelar', exact:true}).click();
    await page.keyboard.press('Escape');
    await triggers.detail.click();
    assert.equal(await page.locator('[data-mv-edit]').count(), 0);
    await page.getByRole('button', {name:'Crear corrección',exact:true}).click();
    assert.equal(await page.locator('#mv-drawer').getAttribute('data-state'), 'detail');
    assert.equal(await page.locator('[role="dialog"]:visible').count(), 1);
    assert.equal(await page.locator('#mv-content form').count(), 0);
    assert.match(await page.locator('#mv-status').innerText(), /no disponible todavía/);
    await page.keyboard.press('Escape');
    await triggers.filters.click();
    await page.locator('#mv-filter-kind').selectOption('Entrada');
    await page.getByRole('button', {name:'Aplicar filtros', exact:true}).click();
    await triggers.filters.click();
    assert.equal(await page.locator('#mv-filter-kind').inputValue(), 'Entrada');
    await page.getByRole('button', {name:'Restablecer', exact:true}).click();
    assert.equal(await page.locator('#mv-filter-kind').inputValue(), '');
    await page.locator('[data-mv-close]').click();
    await triggers.activities.click();
    await page.locator('#mv-activity-search').fill('ENT-0098');
    assert.equal(await page.locator('[data-activity-ref]:visible').count(), 1);
    await page.keyboard.press('Escape');
    await triggers.export.click();
    await page.mouse.click(700, 90);
    assert(await page.locator('#mv-export').isHidden());
    for (const option of ['Excel','CSV','PDF','Imprimir']) {
        await triggers.export.click();
        await page.getByRole('menuitem').filter({hasText: option}).click();
        assert(await page.locator('#mv-export').isHidden());
    }
    await triggers.export.click();
    await triggers.new.click();
    assert(await page.locator('#mv-export').isHidden());
    await page.getByRole('button', {name:'Cerrar panel',exact:true}).focus();
    await page.keyboard.press('Shift+Tab');
    assert.match(await page.evaluate(() => document.activeElement.textContent), /Guardar movimiento/);
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), 'Cerrar panel');
    await page.mouse.click(800, 450);
    assert(await page.locator('#mv-drawer').isHidden());
    assert.equal(await page.locator('.iv-mov-body').innerHTML(), dashboard);
    assert.equal(await page.evaluate(() => document.body.style.overflow), '');
    for (const row of await page.locator('[data-movement-ref]').all()) {
        const reference = await row.getAttribute('data-movement-ref');
        assert.equal(await row.locator('td:last-child button').count(), 1);
        for (const label of ['Ver detalle']) {
            const button = row.getByRole('button', {name:label,exact:true});
            assert(await button.isVisible());
            assert(await button.locator('svg').isVisible());
            assert.equal(await button.getAttribute('title'), label);
            await button.click();
            assert.equal(await page.locator('#mv-drawer').getAttribute('data-state'), 'detail');
            assert.match(await page.locator('#mv-drawer').innerText(), new RegExp(reference));
            const pending = (await row.innerText()).includes('Pendiente');
            assert.equal(await page.locator('[data-mv-edit]').count(), pending ? 1 : 0);
            assert.equal(await page.locator('[data-mv-correction]').count(), pending ? 0 : 1);
            await page.keyboard.press('Escape');
        }
    }
    const regression = [];
    for (const route of ['/dashboard','/products','/categories','/suppliers','/inventory','/predictive/demand','/predictive/replenishment','/predictive/critical','/predictive/trends','/predictive/accuracy']) {
        const response = await page.goto(`http://127.0.0.1:5000${route}?ref=1`, {waitUntil:'networkidle'});
        regression.push([route,response.status()]);
        assert.equal(response.status(), 200);
        assert(await page.locator('[aria-controls="ix-nav-pred"]').isVisible());
    }
    await page.goto('http://127.0.0.1:5000/inventory?ref=1');
    await page.setViewportSize({width:390,height:844});
    await triggers.new.click();
    await page.waitForTimeout(230);
    assert(Math.abs((await page.locator('#mv-drawer').boundingBox()).width - 390) < 0.01);
    await page.screenshot({path:`${output}/mobile-${stage}.png`});
    await page.keyboard.press('Escape');
    assert.deepEqual(errors, []);
    assert.deepEqual(mutations, []);
    console.log(JSON.stringify({stage,geometry,regression,errors,mutations,result:'PASS'},null,2));
    await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
