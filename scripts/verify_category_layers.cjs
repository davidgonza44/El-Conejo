// Synthetic reference server only. Never sends requests to the real port 5000 server.
const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const output = '.tmp/category-layers-validation';
const stage = process.env.CATEGORY_STAGE || 'initial';
fs.mkdirSync(output, {recursive:true});

(async () => {
    const browser = await chromium.launch({channel:'chrome',headless:true});
    const context = await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1});
    const mutations = [], downloads = [], errors = [], apiCalls = [];
    await context.route('http://127.0.0.1:5000/**', async route => {
        const req = route.request();
        if (!['GET','HEAD'].includes(req.method())) {
            mutations.push(req.method() + ' ' + new URL(req.url()).pathname);
            await route.abort();
            return;
        }
        if (new URL(req.url()).pathname.startsWith('/api/')) {
            apiCalls.push(new URL(req.url()).pathname);
            await route.abort();
            return;
        }
        const response = await route.fetch({url:req.url().replace(':5000',':5027')});
        await route.fulfill({response});
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.on('download', download => downloads.push(download.suggestedFilename()));
    await page.goto('http://127.0.0.1:5000/categories?ref=1',{waitUntil:'networkidle'});
    await page.evaluate(() => document.fonts.ready);
    assert(await page.evaluate(() => typeof Chart !== 'undefined' &&
        [...document.fonts].some(font => font.family === 'Inter' && font.status === 'loaded')),
    'Load the real chart and font assets before visual comparisons');
    const viewport = await page.evaluate(() => ({width:innerWidth,height:innerHeight,dpr:devicePixelRatio,scale:visualViewport.scale}));
    assert.deepEqual(viewport,{width:1600,height:900,dpr:1,scale:1});
    const base = await page.screenshot({path:`${output}/base-${stage}.png`});
    const baseTable = await page.locator('.iv-cat-table').innerText();
    const baseline = await context.newPage();
    await baseline.route('**/categories?ref=1', async route => {
        const response = await route.fetch({url:'http://127.0.0.1:5027/categories?ref=1'});
        const html = (await response.text()).replace(/<link[^>]+ix_category_layers\.css[^>]*>/,'')
            .replace(/<script[^>]+ix_category_layers\.js[^>]*><\/script>/,'');
        await route.fulfill({response,body:html});
    });
    await baseline.goto('http://127.0.0.1:5000/categories?ref=1',{waitUntil:'networkidle'});
    await baseline.evaluate(() => document.fonts.ready);
    const original = await baseline.screenshot({path:`${output}/base-without-layers-${stage}.png`});
    assert(base.equals(original),'Base screenshot must be pixel-identical without layer assets');
    await baseline.close();
    const dialog = page.locator('#cl-dialog');
    const menu = page.locator('#cl-menu');
    const triggers = {
        new:page.getByRole('button',{name:'Nueva categoría',exact:true}),
        detail:page.locator('.iv-cat-table [aria-label="Ver detalle"]').first(),
        edit:page.locator('.iv-cat-table [aria-label="Editar"]').first(),
        menu:page.locator('.iv-cat-table [aria-label="Más acciones"]').first(),
        export:page.locator('.iv-actions').getByRole('button',{name:'Exportar',exact:true}),
    };
    const geometry = {};
    for (const state of ['new','detail','edit','menu','export']) {
        await triggers[state].click();
        const panel = state === 'menu' ? menu : dialog;
        await expect(panel).toBeVisible();
        geometry[state] = await panel.boundingBox();
        await page.screenshot({path:`${output}/${state}-${stage}.png`});
        if (state !== 'menu') {
            assert.equal(await page.locator('dialog:visible').count(),1);
            await expect(menu).toBeHidden();
            assert.equal(await page.evaluate(() => document.body.style.overflow),'hidden');
            for (let i=0;i<22;i++) {
                await page.keyboard.press('Tab');
                assert(await dialog.evaluate(node => node.contains(document.activeElement)),'Focus remains in dialog');
            }
        }
        await page.keyboard.press('Escape');
        await expect(panel).toBeHidden();
        await expect(triggers[state]).toBeFocused();
    }
    await triggers.new.click();
    await dialog.getByRole('button',{name:'Guardar categoría',exact:true}).click();
    await expect(dialog).toBeVisible(); // Required name prevents an empty simulation.
    await page.locator('#cl-name').fill('Categoría de prueba');
    await page.locator('#cl-description').fill('Descripción de prueba');
    await dialog.getByRole('button',{name:'Verde',exact:true}).click();
    await page.locator('#cl-state').selectOption('Inactiva');
    await expect(page.locator('[data-cl-preview-name]')).toHaveText('Categoría de prueba');
    await expect(page.locator('[data-cl-preview-description]')).toHaveText('Descripción de prueba');
    await expect(page.locator('#cl-counter')).toHaveText('21/200');
    await expect(page.locator('[data-cl-status]')).toHaveText('Inactiva');
    assert.equal(await page.locator('[data-cl-preview-avatar]').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(32, 170, 88)');
    await dialog.getByRole('button',{name:'Guardar categoría',exact:true}).click();
    await expect(dialog).toBeHidden();
    await expect(page.locator('#cl-feedback')).toContainText('guardado simulado');
    await triggers.new.click();
    await expect(page.locator('#cl-name')).toHaveValue('');
    await dialog.locator('[data-cl-cancel]').click();

    await triggers.detail.click();
    await dialog.getByRole('button',{name:'Editar categoría',exact:true}).click();
    await expect(dialog).toHaveAttribute('data-state','edit');
    assert.equal(await page.locator('dialog:visible').count(),1);
    await page.locator('#cl-name').fill('Edición descartada');
    await dialog.locator('[data-cl-cancel]').click();
    await expect(dialog).toHaveAttribute('data-state','detail');
    await expect(dialog).toContainText('Herramientas eléctricas');
    await dialog.getByRole('button',{name:'Editar categoría',exact:true}).click();
    await expect(page.locator('#cl-name')).toHaveValue('Herramientas eléctricas');
    await page.locator('#cl-name').fill('Edición simulada');
    await dialog.getByRole('button',{name:'Guardar cambios',exact:true}).click();
    await expect(dialog).toBeHidden();
    await expect(triggers.detail).toBeFocused();
    for (const action of ['detail','edit','duplicate','delete','active','inactive']) {
        await triggers.menu.click();
        if (['active','inactive'].includes(action)) await menu.locator('[data-cl-action=status]').click();
        await menu.locator(`[data-cl-action=${action}]`).click();
        await expect(menu).toBeHidden();
        if (['detail','edit'].includes(action)) {
            await expect(dialog).toHaveAttribute('data-state',action);
            await dialog.locator('[data-cl-cancel]').click();
            await expect(triggers.menu).toBeFocused();
        } else await expect(page.locator('#cl-feedback')).toContainText('Demostración');
    }
    await triggers.menu.click();
    await page.locator('h1').click();
    await expect(menu).toBeHidden();
    for (const state of ['new','export']) {
        await triggers.menu.click();
        await triggers[state].click();
        await expect(menu).toBeHidden();
        assert.equal(await page.locator('dialog:visible').count(),1);
        await dialog.locator('[data-cl-close]').click();
        await expect(dialog).toBeHidden();
        await expect(triggers[state]).toBeFocused();
    }
    for (const state of ['new','detail','edit','export']) {
        await triggers[state].click();
        await dialog.locator('[data-cl-cancel]').click();
        await expect(dialog).toBeHidden();
        await expect(triggers[state]).toBeFocused();
    }
    for (const format of ['xlsx','csv','pdf']) {
        await triggers.export.click();
        await page.locator(`input[name=cl-format][value=${format}]`).check();
        assert.equal(await page.locator('input[name=cl-format]:checked').count(),1);
        await page.getByRole('checkbox',{name:'Incluir descripción',exact:true}).uncheck();
        await dialog.getByRole('button',{name:'Exportar',exact:true}).click();
        await expect(dialog).toBeHidden();
        await expect(page.locator('#cl-feedback')).toContainText('exportación simulada');
    }
    assert.equal(await page.locator('.iv-cat-table').innerText(),baseTable);
    assert.equal(await page.evaluate(() => document.body.style.overflow),'');
    for (const route of ['products','suppliers','inventory','dashboard']) {
        const response = await page.goto(`http://127.0.0.1:5000/${route}?ref=1`,{waitUntil:'networkidle'});
        assert.equal(response.status(),200);
        await expect(page.locator('[aria-controls="ix-nav-pred"]')).toBeVisible();
        assert.equal(await page.locator('#cl-dialog').count(),0);
        await page.screenshot({path:`${output}/regression-${route}-${stage}.png`});
    }
    await page.goto('http://127.0.0.1:5000/categories?ref=1',{waitUntil:'networkidle'});
    // Narrow viewport: fields and footer stay reachable without horizontal overflow.
    for (const state of ['new','detail','edit','export']) {
        await triggers[state].click();
        await page.setViewportSize({width:390,height:844});
        const bounds = await dialog.boundingBox();
        assert(bounds.width <= 390);
        assert(await dialog.evaluate(n=>n.scrollWidth<=n.clientWidth));
        await expect(dialog.locator('[data-cl-cancel]')).toBeInViewport();
        await dialog.locator('[data-cl-cancel]').click();
        await page.setViewportSize({width:1600,height:900});
    }
    assert.deepEqual(mutations,[]);
    assert.deepEqual(downloads,[]);
    assert.deepEqual(apiCalls,[]);
    assert.deepEqual(errors,[]);
    fs.writeFileSync(`${output}/results-${stage}.json`,JSON.stringify({browser:browser.version(),viewport,geometry,basePixelIdentical:true,mutations,downloads,apiCalls,errors,checks:'All category interactions, preview, closure, focus, single layer, simulations, reference routes and narrow viewport passed'},null,2));
    await browser.close();
    console.log('PASS: category browser validation; screenshots and results in '+output);
})().catch(error => {console.error(error);process.exit(1);});
