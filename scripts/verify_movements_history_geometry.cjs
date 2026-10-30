/* Read-only visual check. Accept an authenticated Playwright page; fixtures exist
   only in a separate page's intercepted GET responses and DOM, never a database. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {expect} = require('@playwright/test');
const origin = 'http://127.0.0.1:5000';
const reference = 'MOVEMENT-REFERENCE-EXTREMELY-LONG-12345678901234567890';
const product = 'Taladro Percutor Industrial Profesional Edición Especial Ultra Reforzado 750W con Accesorios y Maletín Profesional';
const responsible = 'Administrador de Inventario Regional Principal Turno Nocturno Extraordinario';
const selectors = {history:'.iv-history', header:'.iv-history-table thead', row:'.iv-history-table tbody tr',
    footer:'.iv-history .px-pagination', actions:'.iv-history-table th:last-child', right:'.iv-mov-aside', table:'.iv-history-table'};
async function geometry(page) {
    const result = {};
    for (const [name, selector] of Object.entries(selectors)) result[name] = await page.locator(selector).first().boundingBox();
    result.gap = result.right.x - result.history.x - result.history.width;
    result.columns = await Promise.all((await page.locator('.iv-history-table th').all()).map(th => th.boundingBox()));
    return result;
}
async function normalized(page, url) {
    await page.setViewportSize({width:1600, height:900});
    await page.goto(origin + url, {waitUntil:'networkidle'});
    await page.bringToFront();
    await page.evaluate(async () => { document.documentElement.style.zoom='1'; window.scrollTo(0,0); await document.fonts.ready; });
    if (url === '/inventory') await expect(page.locator('.iv-history-table tbody')).toHaveAttribute('aria-busy','false');
    const viewport = await page.evaluate(() => [innerWidth,innerHeight,devicePixelRatio,visualViewport.scale]);
    assert.deepEqual(viewport.slice(0,2), [1600,900]);
    assert.ok(Math.abs(viewport[2]-1)<0.0001 && viewport[3]===1);
}
async function noOverflow(page) {
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false);
}
function sameGeometry(actual, expected) {
    for (const name of ['history','header','footer','right']) for (const axis of ['x','y','width','height']) {
        assert.ok(Math.abs(actual[name][axis]-expected[name][axis])<1, `${name}.${axis}: ${actual[name][axis]} vs ${expected[name][axis]}`);
    }
    assert.equal(actual.gap, expected.gap);
    assert.equal(actual.table.width, expected.table.width);
    assert.equal(actual.actions.width, expected.actions.width);
}
async function actionsVisible(page) {
    const rows = page.locator('.iv-history-table tbody tr:not(.mv-inline-empty)');
    for (const row of await rows.all()) {
        await expect(row.locator('.iv-icon-btn[aria-label="Ver detalle"]')).toHaveCount(1);
        assert.equal((await row.boundingBox()).height, 47);
    }
    for (const button of await page.locator('.iv-history-table .iv-icon-btn').all()) {
        const b=await button.boundingBox(), cell=await button.locator('xpath=ancestor::td').boundingBox();
        assert.ok(b.x>=cell.x && b.x+b.width<=cell.x+cell.width && b.y>=cell.y && b.y+b.height<=cell.y+cell.height);
        assert.ok(b.x+b.width<=1600);
        const svg=await button.locator('svg').boundingBox();
        assert.ok(svg.x>=b.x && svg.x+svg.width<=b.x+b.width);
    }
}
async function fullValues(page) {
    for (const selector of ['td:nth-child(3)', '.px-product-name', '.px-product-sku', 'td:nth-child(8)', 'td:nth-child(9)']) {
        for (const cell of await page.locator('.iv-history-table tbody tr').locator(selector).all()) {
            assert.ok(await cell.evaluate(element => {
                const full = element.textContent.trim();
                const title = element.closest('[title]')?.getAttribute('title');
                return full && title && title.includes(full);
            }), `Missing full value: ${selector}`);
        }
    }
}
async function numericValues(page) {
    assert.equal(await page.locator('.iv-history-table tbody tr:not(.mv-inline-empty)').evaluateAll(rows => rows.every(row =>
        [4, 5, 6].every(index => {
            const cell = row.children[index];
            const range = document.createRange(); range.selectNodeContents(cell);
            const text = range.getBoundingClientRect(), box = cell.getBoundingClientRect();
            return getComputedStyle(cell).whiteSpace === 'nowrap' && text.left >= box.left && text.right <= box.right;
        })
    )), true, 'Signed quantities and stock must be fully visible');
}
module.exports = async function verify(page, baseline = {}) {
    baseline = JSON.parse(JSON.stringify(baseline));
    const before=baseline.movements || JSON.parse(fs.readFileSync('.tmp/movements-history-before.json','utf8'));
    const result={};
    for (const [name,url] of [['ref','/inventory?ref=1'],['live','/inventory']]) {
        await normalized(page,url); result[name]=await geometry(page);
        sameGeometry(result[name],before[name]); await noOverflow(page); await actionsVisible(page);
        assert.equal(result[name].row.height,47);
        await page.screenshot({path:`.tmp/movements-history-${name === 'ref' ? 'reference' : name}-overflow-final.png`});
    }
    sameGeometry(result.live,result.ref);
    const productBefore=baseline.products || JSON.parse(fs.readFileSync('.tmp/movements-products-before.json','utf8'));
    result.products={};
    for(const [name,url] of [['live','/products'],['ref','/products?ref=1']]) {
        await normalized(page,url);result.products[name]={};
        for(const selector of Object.keys(productBefore[name])) result.products[name][selector]=await page.locator(selector).boundingBox();
        assert.deepEqual(result.products[name],productBefore[name]);await noOverflow(page);
    }
    const fixture=await page.context().newPage();
    let count=2;
    const rows=Array.from({length:8},(_,index)=>({id:9000+index,product_id:9000+index,
        movement_type:['entrada','salida','ajuste'][index%3],quantity:[12,5,3][index%3],delta:[12,-5,3][index%3],
        previous_stock:20,new_stock:[32,15,23][index%3],created_at:'2026-09-11T14:00:00',
        product:index===1?'A'.repeat(64):product,product_code:'CODE-'+ 'X'.repeat(100),
        product_unit:'unidad',user:responsible,reason:'MOTIVO-'+ 'R'.repeat(248)}));
    const mutations=[];
    await fixture.route('**/api/**', async route => {
        const request=route.request(),pathname=new URL(request.url()).pathname;
        if(request.method()!=='GET'){mutations.push(request.method());return route.abort();}
        let data;
        if(pathname.endsWith('/users'))data={items:[{id:9000,name:responsible}]};
        else if(pathname.endsWith('/summary'))data={total:count,daily:{entrada:count,salida:0,ajuste:0},distribution:{entrada:count},commercial_day:'2026-09-11',recent:rows.slice(0,Math.min(count,5))};
        else if(/movements\/\d+$/.test(pathname))data=rows[0];
        else if(pathname.endsWith('/movements'))data={items:rows.slice(0,count),count,total:count,page:1,per_page:8,pages:1};
        else return route.abort();
        await route.fulfill({json:data});
    });
    try {
        result.fixtures={};
        for(count of [0,2,8]) {
            await normalized(fixture,'/inventory');
            const g=await geometry(fixture);sameGeometry(g,result.live);
            assert.equal(await fixture.locator('tr[data-movement-id]').count(),count);
            if(count)assert.equal(g.row.height,47);
            await noOverflow(fixture);await actionsVisible(fixture);result.fixtures[count]=g;
        }
        // Unsupported fields stay neutral in production. These DOM-only values
        // exercise their reserved column boxes without inventing persisted data.
        await fixture.evaluate(({reference})=>{
            for(const row of document.querySelectorAll('.iv-history-table tbody tr')){
                row.children[2].textContent=reference;row.children[2].title=reference;
                row.children[8].textContent='ESTADO-'+'Z'.repeat(80);row.children[8].title=row.children[8].textContent;
            }
        },{reference});
        for(const selector of ['td:nth-child(3)','.px-product-name','.px-product-sku','td:nth-child(8)','td:nth-child(9)']) {
            const cell=fixture.locator('.iv-history-table tbody tr').first().locator(selector);
            assert.ok(await cell.evaluate(e=>e.scrollWidth>e.clientWidth));
            assert.equal(await cell.evaluate(e=>getComputedStyle(e).textOverflow),'ellipsis');
            assert.ok(await cell.getAttribute('title'));
        }
        await expect(fixture.locator('.iv-history-table tbody tr').nth(1).locator('.px-product-name')).toHaveAttribute('title','A'.repeat(64));
        await fullValues(fixture); await numericValues(fixture);
        sameGeometry(await geometry(fixture),result.live);await noOverflow(fixture);await actionsVisible(fixture);
        await fixture.evaluate(()=>{const label=document.createElement('div');label.textContent='PRUEBA AISLADA · Datos sintéticos · Sin cambios en base de datos';label.style.cssText='position:fixed;bottom:2px;left:260px;background:#fff;padding:3px 8px;font:11px sans-serif;color:#475569;z-index:10';document.body.append(label);});
        await fixture.locator('.iv-history-table .iv-icon-btn').first().click();
        await expect(fixture.locator('.mv-detail')).toBeVisible();
        assert.equal(await fixture.locator('.mv-detail-columns dd').last().textContent(),rows[0].reason);
        assert.equal(await fixture.locator('.mv-detail-columns dd').last().evaluate(e=>getComputedStyle(e).overflowWrap),'anywhere');
        assert.equal(await fixture.locator('#mv-content').evaluate(e=>e.scrollWidth>e.clientWidth),false);
        await expect(fixture.getByRole('button',{name:'Crear corrección',exact:true})).toBeVisible();
        await noOverflow(fixture);await fixture.keyboard.press('Escape');
        await fixture.locator('.iv-history .ix-card-title').click();
        await fixture.screenshot({path:'.tmp/movements-history-long-text-final.png', animations:'disabled'});
        // Protect space for both intended icons, even though LIVE currently has one.
        await fixture.evaluate(()=>{const actions=document.querySelector('.iv-history-table .iv-row-actions');const extra=actions.firstElementChild.cloneNode(true);extra.setAttribute('aria-label','Acción sintética');actions.append(extra);});
        await actionsVisible(fixture);
        // Reference markup uses the same containment, with its existing title on
        // the product cell. Keep fixture IDs intact so its detail bindings survive.
        await normalized(fixture,'/inventory?ref=1');
        await fixture.evaluate(({reference,product,responsible})=>{
            for(const row of document.querySelectorAll('.iv-history-table tbody tr:not(.mv-inline-empty)')) {
                for(const [index,value] of [[2,reference],[7,responsible]]) {
                    row.children[index].textContent=value; row.children[index].title=value;
                }
                const name=row.querySelector('.px-product-name'),code=row.querySelector('.px-product-sku');
                name.textContent=product; code.textContent='A'.repeat(100);
                row.children[3].title=`${product} (${code.textContent})`;
                const status=row.children[8].querySelector('.px-badge');
                status.textContent='ESTADO-'+'Z'.repeat(80);row.children[8].title=status.textContent;
            }
        },{reference,product,responsible});
        sameGeometry(await geometry(fixture),result.ref);
        await noOverflow(fixture);await actionsVisible(fixture);await fullValues(fixture);await numericValues(fixture);
        result.referenceLongText='PASS';
        result.longText='PASS';result.detail='PASS';result.mutations=mutations;assert.deepEqual(mutations,[]);
    } finally { await fixture.close(); }
    await normalized(page,'/inventory');
    fs.writeFileSync('.tmp/movements-history-validation.json',JSON.stringify(result,null,2));
    return result;
};
