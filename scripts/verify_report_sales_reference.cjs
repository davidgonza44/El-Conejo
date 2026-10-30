// Run against scripts/test_settings_reference.py --serve: no DB, synthetic users only.
const {chromium} = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const output = '.tmp/sales-breakdown-validation';
const base = 'http://127.0.0.1:5021';
const stage = process.env.REPORT_STAGE || 'refinement';
const routes = ['dashboard','inventory/summary','products','categories','suppliers','inventory','sales','sales/list','reports','predictive/demand','predictive/trends','settings'];
function measure() {
    const rect = e => {const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
    const box = selector => rect(document.querySelector(selector));
    return {sidebar:box('.ix-sidebar'),content:box('.rs-tabs'),title:box('h1'),toolbar:box('.rs-toolbar'),search:box('.rs-search'),exports:box('.rs-exports'),tabs:box('.rs-tabs'),kpis:box('.rs-kpis'),cards:[...document.querySelectorAll('.rs-card')].map(e=>({title:e.querySelector('h2')?.textContent,...rect(e)})),pagination:document.querySelector('.rs-pagination')?box('.rs-pagination'):null,pageBottom:box('.rs-page').bottom,scrollHeight:document.documentElement.scrollHeight,scrollWidth:document.documentElement.scrollWidth,dpr:devicePixelRatio,zoom:visualViewport.scale,chartCount:Object.keys(Chart.instances).length};
}
async function cacheResources(context) {
    await context.route(/https:\/\/(cdn.jsdelivr.net|fonts.googleapis.com|fonts.gstatic.com)\//, route => {
        const url = new URL(route.request().url());
        let file = url.hostname === 'fonts.googleapis.com' ? 'inter.css' : path.basename(url.pathname);
        const local = path.join('.tmp/sales-reports-validation',file);
        if (!fs.existsSync(local)) return route.abort();
        return route.fulfill({path:local,contentType:file.endsWith('.css')?'text/css':file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':'font/ttf'});
    });
}
(async () => {
    fs.mkdirSync(output,{recursive:true});
    const browser = await chromium.launch({channel:'chrome'});
    const context = await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1});
    await cacheResources(context);
    const page = await context.newPage();
    const errors=[], mutations=[],checks=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(!['GET','HEAD','OPTIONS'].includes(r.method()))mutations.push(r.method()+' '+r.url());});
    for (const tab of ['summary','products','customers','categories','payments']) {
        const response=await page.goto(`${base}/reports/sales?tab=${tab}&ref=1`,{waitUntil:'networkidle'});
        assert.equal(response.status(),200);
        await page.evaluate(()=>document.fonts.ready);
        assert.deepEqual(await page.locator('.rs-tabs a').allTextContents(),['Resumen','Ventas por producto','Ventas por cliente','Ventas por categoría','Métodos de pago']);
        assert.equal(await page.locator('.rs-tabs [aria-current=page]').innerText(),{summary:'Resumen',products:'Ventas por producto',customers:'Ventas por cliente',categories:'Ventas por categoría',payments:'Métodos de pago'}[tab]);
        assert.equal(await page.locator('.rs-tabs').getByText('Tendencias',{exact:true}).count(),0);
        await page.screenshot({path:path.join(output,`${tab}-${stage}.png`)});
        const geometry=await page.evaluate(measure);
        fs.writeFileSync(path.join(output,`${tab}-${stage}-geometry.json`),JSON.stringify(geometry,null,2));
        checks.push({tab,geometry});
        if(stage==='final') {assert.equal(geometry.scrollHeight,900);assert.equal(geometry.scrollWidth,1600);assert.equal(geometry.dpr,1);assert.equal(geometry.zoom,1);assert.equal(geometry.chartCount,tab==='categories'?3:2);}
    }
    if (stage === 'final') {
        for (const route of routes) {
            assert.equal((await page.goto(`${base}/${route}?ref=1`,{waitUntil:'networkidle'})).status(),200);
            await page.evaluate(()=>document.fonts.ready);
            assert(await page.locator('button[aria-controls=ix-nav-pred]').isVisible(),route);
            assert.equal(await page.locator('#ix-nav-pred a').count(),5);
            assert.equal(await page.locator('#ix-nav-pred').getByText('Tendencias',{exact:true}).count(),1);
            await page.screenshot({path:path.join(output,'regression-'+route.replaceAll('/','-')+'.png')});
            checks.push({route,status:'PASS'});
        }
        await page.goto(base+'/sales?ref=1');
        assert.deepEqual(await page.locator('#ix-nav-sales a').allTextContents(),['Nueva venta','Cotizaciones','Pedidos','Clientes']);
        for(const route of ['/dashboard','/reports/sales','/inventory/summary','/sales','/dashboard']) {
            if(route==='/reports/sales') await page.locator('.ix-nav a[href="/reports/sales?ref=1"]').click();
            else if(route==='/inventory/summary') {await page.locator('[aria-controls=ix-nav-inv]').click();await page.locator('#ix-nav-inv').getByRole('link',{name:'Resumen',exact:true}).click();}
            else if(route==='/sales') await page.locator('.ix-nav a[href="/sales?ref=1"]').click();
            else await page.locator('.ix-nav a[href="/dashboard?ref=1"]').click();
            assert.equal(new URL(page.url()).pathname,route);
            assert(await page.locator('[aria-controls=ix-nav-pred]').isVisible());
            assert.equal(await page.locator('#ix-nav-pred a').count(),5);
            assert.equal(await page.locator('#ix-nav-pred').getByText('Tendencias',{exact:true}).count(),1);
        }
        await page.locator('[aria-controls=ix-nav-pred]').click();
        for(const link of await page.locator('#ix-nav-pred a').all()) assert(await link.isVisible());
        const children=await page.locator('#ix-nav-pred a').evaluateAll(links=>links.map(l=>l.href));
        for(const href of children)assert.equal((await page.goto(href)).status(),200);
        await page.goto(base+'/sales/reports?tab=customers&ref=1');
        assert.equal(new URL(page.url()).pathname,'/reports/sales');
        assert.equal(new URL(page.url()).searchParams.get('tab'),'customers');
        await page.locator('[data-rs-detail]').first().click();assert(await page.locator('#rs-dialog').isVisible());
        await page.getByRole('button',{name:'Cerrar',exact:true}).click();
        const downloadPromise=page.waitForEvent('download');await page.locator('[data-rs-export=csv]').click();const download=await downloadPromise;
        assert.equal(download.suggestedFilename(),'referencia-ventas-customers.csv');
        for(const tab of ['summary','products','customers','categories','payments']) {
            await page.goto(`${base}/reports/sales?tab=${tab}`);assert.equal(await page.locator('#rs-fixture').count(),0);assert(await page.locator('.rs-unavailable').isVisible());
        }
        for(const width of [390,768,1024]) {
            await page.setViewportSize({width,height:900});
            for(const tab of ['summary','products','customers','categories','payments']) {
                await page.goto(`${base}/reports/sales?tab=${tab}&ref=1`,{waitUntil:'networkidle'});
                assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),width,`${tab} at ${width}`);
                const toggle=page.locator('#ix-menu-toggle');assert(await toggle.isVisible());await toggle.click();assert.equal(await toggle.getAttribute('aria-expanded'),'true');await toggle.click();
                await page.screenshot({path:path.join(output,`${tab}-${width}.png`)});
            }
        }
    }
    assert.deepEqual(errors,[]);assert.deepEqual(mutations,[]);
    fs.writeFileSync(path.join(output,`${stage}-checks.json`),JSON.stringify({checks,errors,mutations},null,2));
    console.log(JSON.stringify({stage,checks:checks.length,errors,mutations,geometry:checks.filter(c=>c.geometry).map(c=>({tab:c.tab,scrollHeight:c.geometry.scrollHeight,cards:c.geometry.cards}))}));
    await browser.close();
})().catch(error=>{console.error(error);process.exit(1)});
