// Disposable reference harness only; synthetic files stay in browser memory.
const {chromium} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const output = process.env.PRODUCT_VALIDATION_OUTPUT || `.tmp/product-csv-filters-validation-${Date.now()}`;
fs.mkdirSync(output, {recursive:true});
let browser;
(async () => {
    browser = await chromium.launch({channel:'msedge'});
    const page = await browser.newPage({viewport:{width:1600,height:900},deviceScaleFactor:1,locale:'es-VE'});
    const origin = process.env.PRODUCT_PREVIEW_URL || 'http://127.0.0.1:5000';
    const mutations = [], downloads = [], errors = [], interactionRequests = [];
    let watching = false;
    page.on('request', r => {if (!['GET','HEAD','OPTIONS'].includes(r.method())) mutations.push(r.method()); if (watching) interactionRequests.push(r.url());});
    page.on('download', d => downloads.push(d.suggestedFilename()));
    page.on('pageerror', e => errors.push(e.message));
    const shot = name => page.screenshot({path:path.join(output, `${name}.png`)});
    if (process.env.PRODUCT_BASELINE_DIR) {
        for (const asset of ['ix_product_layers.css','ix_product_layers.js']) {
            await page.route(`**/${asset}*`, r => r.fulfill({body:fs.readFileSync(path.join(process.env.PRODUCT_BASELINE_DIR,asset)),contentType:asset.endsWith('.css')?'text/css':'application/javascript'}));
        }
        await page.goto(`${origin}/products?ref=1`,{waitUntil:'networkidle'});
        await shot('base-before');
        await page.unrouteAll();
    }
    const response = await page.goto(`${origin}/products?ref=1`,{waitUntil:'networkidle'});
    assert.equal(response.status(),200,'Products must render before UI checks');
    await shot('base-after');
    assert.deepEqual(await page.evaluate(()=>[innerWidth,innerHeight,devicePixelRatio,visualViewport.scale]),[1600,900,1,1]);
    const markup = () => page.locator('.iv-prod-page').evaluate(n => { const copy=n.cloneNode(true);copy.querySelectorAll('[style=""]').forEach(e=>e.removeAttribute('style'));return copy.innerHTML; });
    const base = await markup();
    const sidebar = await page.locator('#ix-sidebar').innerHTML();
    const toolbar = page.locator('.iv-prod-toolbar');
    const importer = toolbar.getByRole('button',{name:'Importar CSV',exact:true});
    const filters = toolbar.getByRole('button',{name:'Filtros',exact:true});
    const dialog = page.locator('#pl-dialog');
    const action = page.locator('#pl-action');
    const cancel = page.locator('#pl-cancel');
    const file = page.locator('#pl-csv-file');
    async function one() {assert.equal(await page.locator('dialog[open]').count(),1); assert.equal(await page.locator('body').evaluate(n=>n.style.overflow),'hidden');}
    async function closed(trigger) {await dialog.waitFor({state:'hidden'});await page.waitForTimeout(40);assert.equal(await page.locator('body').evaluate(n=>n.style.overflow),'');if(trigger)assert(await trigger.evaluate(n=>document.activeElement===n));}
    watching = true;
    await importer.click(); await one(); await shot('import-initial');
    assert.equal(await page.locator('#pl-title').innerText(),'Importar productos desde CSV');
    await action.click(); assert(await dialog.isVisible());
    await file.setInputFiles({name:'incorrecto.txt',mimeType:'text/plain',buffer:Buffer.from('not csv')});
    await action.click(); assert(await dialog.isVisible());
    await file.setInputFiles({name:'productos.csv',mimeType:'text/csv',buffer:Buffer.from('nombre,codigo\nProducto,TST')});
    assert.match(await page.locator('#pl-csv-feedback').innerText(),/productos\.csv/);
    await shot('import-selected');
    await page.locator('#pl-template-download').click();
    assert(await page.locator('#pl-template-feedback').isVisible());
    await action.click();await closed(importer);
    assert.match(await page.locator('#pl-feedback').innerText(),/simula/i);
    await importer.click();
    await page.locator('#pl-drop-zone').evaluate(n=>{
        const transfer=new DataTransfer();const file=new File(['a,b'],'grande.csv',{type:'text/csv'});
        Object.defineProperty(file,'size',{value:10*1024*1024+1});transfer.items.add(file);
        n.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));
    });
    await action.click();assert(await dialog.isVisible());
    await page.locator('#pl-drop-zone').evaluate(n=>{
        const transfer=new DataTransfer();transfer.items.add(new File(['nombre,codigo\nDemo,ABC'],'arrastrado.csv',{type:'text/csv'}));
        n.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:transfer}));
        n.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));
    });
    assert.match(await page.locator('#pl-csv-feedback').innerText(),/arrastrado\.csv/);
    await cancel.click();await closed(importer);
    for(const trigger of [importer,filters]) {
        await trigger.click();await page.getByRole('button',{name:'Cerrar capa',exact:true}).click();await closed(trigger);
        await trigger.click();await page.keyboard.press('Escape');await closed(trigger);
    }
    await filters.click();await one();await shot('filters-initial');
    for(const id of ['price-min','price-max','date-min','date-max']) assert.equal(await page.locator(`#pl-advanced-${id}`).evaluate(n=>getComputedStyle(n).paddingLeft),'46px');
    assert.equal(await page.locator('#pl-title').innerText(),'Filtros avanzados');
    assert.equal(await page.locator('#pl-advanced select').count(),6);
    assert.equal(await page.locator('#pl-advanced input').count(),6);
    await page.locator('#pl-advanced-category').selectOption({index:1});
    await page.locator('#pl-advanced-price-min').fill('10');
    await page.locator('#pl-advanced-date-min').fill('2024-04-01');
    await page.locator('#pl-reset-filters').click();
    assert.equal(await page.locator('#pl-advanced-category').inputValue(),'');
    assert.equal(await page.locator('#pl-advanced-price-min').inputValue(),'');
    assert.equal(await page.locator('#pl-advanced-date-min').inputValue(),'');
    assert.equal(await page.locator('#pl-advanced-size').inputValue(),'8');
    await page.locator('#pl-advanced-status').selectOption({index:1});
    await action.click();await closed(filters);
    assert.match(await page.locator('#pl-feedback').innerText(),/simula/i);
    await filters.click();await cancel.click();await closed(filters);
    const triggers = [importer,filters,toolbar.getByRole('button',{name:'Nuevo producto',exact:true}),page.locator('.iv-catalog-table [aria-label="Ver detalle"]').first(),page.locator('.iv-catalog-table [aria-label="Editar"]').first(),page.getByRole('link',{name:'Ver todo el catálogo',exact:true}),toolbar.getByRole('button',{name:'Exportar',exact:true})];
    for(const from of triggers) for(const to of triggers) {
        await from.evaluate(n=>n.click());await to.evaluate(n=>n.click());await page.waitForTimeout(30);await one();
    }
    await page.keyboard.press('Escape');await closed();
    watching = false;
    assert.equal(await markup(),base);
    assert.equal(await page.locator('#ix-sidebar').innerHTML(),sidebar);
    assert(await page.locator('[aria-controls="ix-nav-pred"]').isVisible());
    await page.mouse.click(1590,890);await shot('base-final');
    for(const route of ['products','categories','inventory','suppliers','dashboard']) {
        const response=await page.goto(`${origin}/${route}?ref=1`,{waitUntil:'networkidle'});assert.equal(response.status(),200);
        assert(await page.locator('[aria-controls="ix-nav-pred"]').isVisible());await shot(`regression-${route}`);
    }
    assert.deepEqual(mutations,[]);assert.deepEqual(downloads,[]);assert.deepEqual(errors,[]);assert.deepEqual(interactionRequests,[]);
    console.log(JSON.stringify({result:'PASS',output,viewport:'1600x900 DPR1 zoom100',csvSelection:true,dragDrop:true,csvSimulation:true,filtersSimulation:true,filtersReset:true,orderedLayerTransitions:49,baseMarkupUnchanged:true,sidebarUnchanged:true,mutationRequests:mutations.length,downloads:downloads.length,interactionRequests:interactionRequests.length,errors},null,2));
    await browser.close();
})().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1;});
