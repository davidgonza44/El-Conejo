/* Inventory Summary + Sales Dashboard browser proof (LIVE vs REFERENCE).
   Writes only through the guarded local harness (scripts/serve_sales_e2e.py, ferreteria_conejo_e2e):
   a few real sales/movements so live cards have something truthful to show.
   1600x900 DPR 1 zoom 100%; smoke 1366x768 and 1280x720. Reference (?ref=1) must issue zero /api/ requests. */
const {chromium}=require('@playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const origin=process.env.SALES_E2E_URL || 'http://127.0.0.1:5024';
assert.equal(new URL(origin).hostname,'127.0.0.1','E2E requires a local disposable server');
const output='.tmp/dashboards-validation';
const FIXTURE_VALUES=['486,250','3,245','524,830','1,284','Importadora Ferremax','Sucursal Norte','Resumen por sucursal','Reportes de ventas','Órdenes de venta'];

(async()=>{
    const browser=await chromium.launch({channel:'msedge'});
    const results={};
    try{
        const context=await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1,locale:'es-VE'});
        await context.route(/https:\/\/(cdn.jsdelivr.net|fonts.googleapis.com|fonts.gstatic.com)\//,route=>{
            const url=new URL(route.request().url());
            const file=url.hostname==='fonts.googleapis.com'?'inter.css':path.basename(url.pathname);
            const local=path.join('.tmp/sales-reports-validation',file);
            if(!/\.(css|js|woff2?|ttf)$/.test(file)||!fs.existsSync(local))return route.abort();
            return route.fulfill({path:local,contentType:file.endsWith('.css')?'text/css':file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':'font/ttf'});
        });
        const page=await context.newPage();
        const errors=[];page.on('pageerror',error=>errors.push(error.message));
        const apiRequests=[];page.on('request',r=>{if(r.url().includes('/api/'))apiRequests.push(r.method()+' '+r.url());});
        const ctx=await(await context.request.get(origin+'/__sales_e2e__/context')).json();
        assert.equal(ctx.database,'ferreteria_conejo_e2e');assert.match(ctx.engine,/MariaDB/);
        assert.equal((await context.request.post(origin+'/api/auth/login',{data:{identifier:ctx.username,password:ctx.password}})).status(),200);
        const req=context.request;
        fs.mkdirSync(output,{recursive:true});

        /* ---------- Seed truthful activity in the disposable DB (harness only) ---------- */
        const sale=async(customer_id,items,payment_method)=>{const r=await req.post(origin+'/api/sales',{data:{customer_id,payment_method,payment_condition:'cash',idempotency_key:crypto.randomUUID(),items}});assert.equal(r.status(),201,await r.text());return r.json();};
        const [p0,p1,p2,p3]=ctx.product_ids;
        await sale(ctx.customer_id,[{product_id:p0,quantity:3},{product_id:p1,quantity:1}],'transfer');
        await sale(ctx.long_customer_id,[{product_id:p2,quantity:2}],'cash');
        await sale(ctx.walk_in_id,[{product_id:ctx.long_product_id,quantity:2},{product_id:p3,quantity:1}],'debit_card');
        assert.equal((await req.post(origin+'/api/inventory/exit',{data:{product_id:p1,quantity:26,reason:'Salida manual E2E'}})).status(),201);
        assert.equal((await req.post(origin+'/api/inventory/entry',{data:{product_id:p0,quantity:4,reason:'Entrada E2E'}})).status(),201);

        const invApi=await(await req.get(origin+'/api/inventory/summary-dashboard')).json();
        const salesApi=await(await req.get(origin+'/api/sales/dashboard')).json();
        results.inventory_api={in_stock:invApi.kpis.products_in_stock.value,value:invApi.kpis.inventory_value.value,rotation_units:invApi.top_rotation.total_units,comparison:invApi.kpis.products_in_stock.comparison.reason};
        results.sales_api={revenue:salesApi.kpis.revenue.value,count:salesApi.kpis.sales_count.value,methods:salesApi.payment_methods.items.map(i=>i.label),period:salesApi.period};

        const shot=async name=>{await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:`${output}/${name}.png`});};
        const loaded=async url=>{await page.goto(origin+url,{waitUntil:'networkidle'});await page.waitForFunction(()=>document.fonts.check('400 12px Inter'));};
        const overflow=async()=>page.evaluate(()=>{
            const cells=[...document.querySelectorAll('td, .px-product-name, .ix-row-title, .ix-row-desc')];
            const ellipsized=c=>getComputedStyle(c).textOverflow==='ellipsis'&&getComputedStyle(c).overflow==='hidden';
            const bad=cells.filter(c=>c.scrollWidth>c.clientWidth+1&&!ellipsized(c)).map(c=>`${c.tagName}.${c.className}:${c.textContent.trim().slice(0,30)} ${c.scrollWidth}>${c.clientWidth}`);
            const cards=[...document.querySelectorAll('.ix-card')].filter(c=>c.scrollHeight>c.clientHeight+1&&getComputedStyle(c).overflowY==='visible').map(c=>c.className);
            return {page:document.documentElement.scrollWidth<=innerWidth,cells:bad.length===0,bad:bad.slice(0,5),cards:cards.slice(0,5)};
        });
        const checkPage=async(url,name,live)=>{
            apiRequests.length=0;
            await loaded(url);
            const html=await page.content();
            const o=await overflow();
            assert.ok(o.page&&o.cells,`${name}: overflow ${JSON.stringify(o)}`);
            const leaked=FIXTURE_VALUES.filter(v=>html.includes(v));
            if(live)assert.deepEqual(leaked,[],`${name}: fixture values leaked into live`);
            else assert.deepEqual(apiRequests,[],`${name}: reference must not call the API`);
            await shot(name);
            results[name]={overflow_free:true,api_requests:apiRequests.length,fixture_leak:leaked,canvases:await page.locator('canvas').count()};
        };

        await checkPage('/inventory/summary','inventory-summary-live',true);
        assert.ok((await page.content()).includes('Productos con mayor rotación')&&!(await page.content()).includes('Resumen por sucursal'));
        assert.equal(await page.locator('#ix-sidebar a[aria-current="page"]').innerText(),'Resumen');
        await checkPage('/inventory/summary?ref=1','inventory-summary-reference',false);
        await checkPage('/sales','sales-dashboard-live',true);
        assert.equal(await page.locator('.sa-dates-live input[name="start"]').inputValue(),salesApi.period.start);
        assert.ok(await page.locator('#ix-sidebar').innerText().then(t=>['Nueva venta','Cotizaciones','Pedidos','Clientes'].every(l=>t.includes(l))&&!t.includes('Reportes de ventas')));
        await checkPage('/sales?ref=1','sales-dashboard-reference',false);

        /* ---------- Smoke viewports ---------- */
        for(const [w,h] of [[1366,768],[1280,720]]){
            await page.setViewportSize({width:w,height:h});
            for(const url of ['/inventory/summary','/sales']){
                await loaded(url);const o=await overflow();
                assert.ok(o.page&&o.cells,`${url} @${w}: ${JSON.stringify(o)}`);
            }
            results[`smoke_${w}`]=true;
        }
        assert.deepEqual(errors,[],'pageerror');
        results.pageerrors=0;
        results.pass=true;
    }catch(error){
        results.pass=false;results.error=String(error && error.stack || error);
        console.error(error);
    }finally{
        fs.writeFileSync(`${output}/results.json`,JSON.stringify(results,null,2));
        console.log(JSON.stringify(results,null,2));
        await browser.close();
    }
    process.exit(results.pass?0:1);
})();
