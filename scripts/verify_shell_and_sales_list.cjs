/* Browser proof: Inventory Summary hardening (long data), global search + bell (shared shell),
   live Sales list + detail, dashboard "Ver todas". Writes only through the guarded local harness
   (scripts/serve_sales_e2e.py -> ferreteria_conejo_e2e); rows are removed at the end.
   1600x900 DPR 1; smoke 1366x768 and 1280x720. Reference (?ref=1) must issue zero /api/ requests. */
const {chromium}=require('@playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const origin=process.env.SALES_E2E_URL || 'http://127.0.0.1:5024';
assert.equal(new URL(origin).hostname,'127.0.0.1','E2E requires a local disposable server');
const output='.tmp/shell-validation';
const FIXTURE_VALUES=['486,250','3,245','524,830','1,284','Importadora Ferremax','Sucursal Norte','142 ventas','VTA-000156','3 sin leer'];

(async()=>{
    const browser=await chromium.launch({channel:'msedge'});
    const results={};
    let req;
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
        req=context.request;
        fs.mkdirSync(output,{recursive:true});

        /* ---------- Long / large data in the disposable DB only ---------- */
        const post=async(url,data,expected=201)=>{const r=await req.post(origin+url,{data});assert.equal(r.status(),expected,`${url}: ${await r.text()}`);return r.json();};
        const longCategory=await post('/api/categories',{name:(`Ventas E2E ${ctx.run} Materiales eléctricos, plomería, herramientas manuales y equipos de seguridad industrial`).slice(0,100)});
        const name150=(`Producto E2E ${ctx.run} con nombre extremadamente largo para validar el recorte con puntos suspensivos en tarjetas, tablas y campana `+'x'.repeat(60)).slice(0,150);
        const low150=await post('/api/products',{code:`${ctx.prefix}-L150`,name:name150,category_id:longCategory.id,current_stock:1,minimum_stock:5,purchase_price:'3.50',sale_price:'4.00'});
        const out=await post('/api/products',{code:`${ctx.prefix}-OUT`,name:`Agotado E2E ${ctx.run}`,category_id:longCategory.id,current_stock:0,minimum_stock:3,purchase_price:'2.00',sale_price:'3.00'});
        const big=await post('/api/products',{code:`${ctx.prefix}-BIG`,name:`Tornillo E2E ${ctx.run} a granel`,category_id:longCategory.id,current_stock:999999,minimum_stock:10,purchase_price:'12.85',sale_price:'15.00'});
        assert.equal(name150.length,150);
        await post('/api/inventory/entry',{product_id:big.id,quantity:50000,reason:('Reposición masiva E2E desde el proveedor principal con guía de despacho, factura y verificación de lote '+'y'.repeat(120)).slice(0,255)});
        const sale=(customer_id,items,payment_method)=>post('/api/sales',{customer_id,payment_method,payment_condition:'cash',idempotency_key:crypto.randomUUID(),items});
        const [p0,p1]=ctx.product_ids;
        const first=await sale(ctx.long_customer_id,[{product_id:ctx.long_product_id,quantity:2},{product_id:p0,quantity:3}],'transfer');
        for(let i=0;i<10;i++)await sale(i%2?ctx.customer_id:ctx.walk_in_id,[{product_id:big.id,quantity:100+i}],i%3?'cash':'debit_card');
        const alerts=await(await req.get(origin+'/api/shell/alerts')).json();
        const salesApi=await(await req.get(origin+'/api/sales')).json();
        results.seed={category:longCategory.name.length,product_name:name150.length,alerts:alerts.count,sales_total:salesApi.total,first_sale:first.sale_number};

        /* ---------- Helpers ---------- */
        const shot=async name=>{await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:`${output}/${name}.png`});};
        const loaded=async url=>{await page.goto(origin+url,{waitUntil:'networkidle'});await page.waitForFunction(()=>document.fonts.check('400 12px Inter'));};
        const overflow=async()=>page.evaluate(()=>{
            const cells=[...document.querySelectorAll('td, .px-product-name, .ix-row-title, .ix-row-desc, .ix-kpi-value, .ix-legend-name, .ix-search-title, .ix-search-sub')];
            const ellipsized=c=>getComputedStyle(c).textOverflow==='ellipsis'&&getComputedStyle(c).overflow==='hidden';
            const bad=cells.filter(c=>c.scrollWidth>c.clientWidth+1&&!ellipsized(c)).map(c=>`${c.tagName}.${c.className}:${c.textContent.trim().slice(0,30)} ${c.scrollWidth}>${c.clientWidth}`);
            const cards=[...document.querySelectorAll('.ix-card')].filter(c=>c.scrollHeight>c.clientHeight+1&&getComputedStyle(c).overflowY==='visible').map(c=>c.className);
            const titled=[...document.querySelectorAll('.ix-row-title, .px-product-name, .ix-legend-name, td.sa-clip, td.iv-clip')].filter(c=>c.scrollWidth>c.clientWidth+1&&!c.title).length;
            const cw=document.documentElement.clientWidth;
            const wide=[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>cw+1&&e.getBoundingClientRect().width>0).slice(0,5).map(e=>`${e.tagName}.${String(e.className).slice(0,40)} right=${Math.round(e.getBoundingClientRect().right)}`);
            return {page:document.documentElement.scrollWidth<=cw,scrollWidth:document.documentElement.scrollWidth,clientWidth:cw,wide,cells:bad.length===0,bad:bad.slice(0,5),cards:cards.slice(0,5),untitled:titled};
        });
        const geometry=()=>page.evaluate(()=>[...document.querySelectorAll('.ix-kpi, .iv-main > .ix-card, .iv-lower > .ix-card')].map(c=>{const r=c.getBoundingClientRect();return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)];}));
        const checkPage=async(url,name,live)=>{
            apiRequests.length=0;
            await loaded(url);
            const html=await page.content();
            const o=await overflow();
            assert.ok(o.page&&o.cells&&!o.cards.length&&o.untitled===0,`${name}: overflow ${JSON.stringify(o)}`);
            const leaked=FIXTURE_VALUES.filter(v=>html.includes(v));
            if(live)assert.deepEqual(leaked,[],`${name}: fixture values leaked into live`);
            else assert.deepEqual(apiRequests,[],`${name}: reference must not call the API`);
            if(name)await shot(name);
            results[name||url]={overflow_free:true,api_requests:apiRequests.length,fixture_leak:leaked};
        };

        /* ---------- PART A: inventory summary with long data ---------- */
        await checkPage('/inventory/summary','inventory-summary-overflow-1600',true);
        const geo=await geometry();
        results.inventory_geometry={kpi_heights:[...new Set(geo.slice(0,4).map(g=>g[3]))],main_heights:[...new Set(geo.slice(4,7).map(g=>g[3]))],lower_heights:[...new Set(geo.slice(7).map(g=>g[3]))],cards:geo.length};
        assert.deepEqual(results.inventory_geometry.kpi_heights,[106]);assert.deepEqual(results.inventory_geometry.main_heights,[281]);assert.deepEqual(results.inventory_geometry.lower_heights,[312]);
        const invHtml=await page.content();
        // The disposable DB already holds many alerts; record which long rows made it into the limited cards.
        results.long_rows_visible={product150:invHtml.includes(name150),category100:invHtml.includes(longCategory.name),long_customer:invHtml.includes(`E2E-CUST-${ctx.run}-LONG`),long_product:invHtml.includes('Cable THW calibre 12 AWG')};
        assert.ok(Object.values(results.long_rows_visible).some(Boolean),'at least one long row rendered');
        await page.locator('.iv-lower').scrollIntoViewIfNeeded();
        await page.screenshot({path:`${output}/inventory-summary-long-data.png`,clip:{x:0,y:0,width:1600,height:900}});
        results.inventory_value=(await page.locator('.ix-kpi').nth(3).innerText()).replace(/\s+/g,' ');

        /* ---------- PART B: global search ---------- */
        const search=page.locator('#ix-search-input');
        await search.fill(`E2E-CUST-${ctx.run}-LONG`);
        await page.waitForSelector('#ix-search-results:not([hidden]) [role="option"]');
        await shot('global-search-live');
        const groups=(await page.locator('#ix-search-results .ix-search-group').allTextContents()).map(t=>t.trim());
        assert.ok(groups.includes('Clientes')&&groups.includes('Ventas'),`search groups ${groups}`);
        assert.ok((await overflow()).cells,'search dropdown clipped');
        await search.press('ArrowDown');
        assert.equal(await page.locator('#ix-search-results [aria-selected="true"]').count(),1);
        await search.press('Enter');
        await page.waitForURL(/\/sales\/customers\?search=/);
        await page.waitForSelector('#cu-body tr[data-customer-id]');
        assert.ok((await page.locator('#cu-summary').innerText()).includes('de 1 clientes'),'customer page filtered by global search');
        results.search_customer=page.url();
        await loaded('/sales');
        await search.fill(`${ctx.prefix}-L150`);
        await page.waitForSelector('#ix-search-results:not([hidden]) [role="option"]');
        await page.locator('#ix-search-results [role="option"]').first().click();
        await page.waitForURL(/\/products\?search=/);
        await page.waitForFunction(()=>document.getElementById('ix-prod-search').value!=='');
        await page.waitForLoadState('networkidle');
        assert.equal(await page.locator('#ix-prod-search').inputValue(),`${ctx.prefix}-L150`);
        assert.ok((await page.locator('#ix-catalog-body').innerText()).includes(name150.slice(0,40)),'product catalog filtered by global search');
        results.search_product=page.url();
        await loaded('/inventory/summary');
        await search.fill(first.sale_number);
        await page.waitForSelector('#ix-search-results:not([hidden]) [role="option"]');
        await search.press('Enter');
        await page.waitForURL(new RegExp(`/sales/${first.sale_number}$`));
        assert.ok((await page.locator('h1').innerText()).includes(first.sale_number),'sale detail reached from search');
        results.search_sale=page.url();
        await loaded('/inventory/summary');
        apiRequests.length=0;
        await search.fill('z');
        await page.waitForTimeout(500);
        assert.ok(await page.locator('#ix-search-results').isHidden(),'1 char must not search');
        assert.ok(!apiRequests.some(r=>r.includes('/api/shell/search')),'no search request under 2 chars');

        /* ---------- PART C: bell ---------- */
        const badge=await page.locator('#ix-bell-badge').innerText();
        assert.equal(badge,String(alerts.count),'badge = real alert count');
        await page.locator('#ix-bell').click();
        await page.waitForSelector('#ix-bell-panel:not([hidden]) .ix-bell-item');
        const items=await page.locator('#ix-bell-panel .ix-bell-item').count();
        assert.equal(items,alerts.items.length);
        const panelText=await page.locator('#ix-bell-panel').innerText();
        assert.ok(panelText.includes('Alertas de inventario')&&panelText.includes('Bajo stock')&&panelText.includes('Agotado')&&!/sin leer|nuevo pedido|mensaje/i.test(panelText));
        assert.ok((await overflow()).cells,'bell panel clipped');
        await shot('notification-bell-live');
        await page.locator('#ix-bell-all').click();
        await page.waitForURL(/\/products\?status=bajo_stock/);
        await page.waitForFunction(()=>document.getElementById('ix-filter-status').value!=='');
        await page.waitForLoadState('networkidle');
        assert.equal(await page.locator('#ix-filter-status').inputValue(),'bajo_stock');
        results.bell={badge,items,ver_todas:page.url()};

        /* ---------- PART D: sales dashboard -> list -> detail ---------- */
        await checkPage('/sales','sales-dashboard-live-final',true);
        assert.ok((await page.locator('.sa-recent-table').innerText()).includes(`${100+9} productos`),'dashboard Productos = units');
        await page.locator('.sa-recent .sa-link',{hasText:'Ver todas'}).click();
        await page.waitForURL(/\/sales\/list\?date_from=/);
        await page.waitForLoadState('networkidle');
        const o=await overflow();assert.ok(o.page&&o.cells,`sales list overflow ${JSON.stringify(o)}`);
        await shot('sales-list-live');
        const rows=await page.locator('.sa-list-live-table tbody tr').count();
        assert.equal(rows,10,'10 rows per page');
        const headers=await page.locator('.sa-list-live-table th').allInnerTexts();
        assert.deepEqual(headers,['Venta','Fecha','Cliente','Productos','Método de pago','Total','Estado','Acciones']);
        const summary=await page.locator('.sa-pagination-summary').innerText();
        assert.match(summary,/Mostrando 1 a 10 de \d+ ventas/);
        assert.ok(!(await page.content()).includes('En proceso')&&!(await page.content()).includes('Cancelada'));
        await page.locator('.sa-pages a[aria-label="Página siguiente"]').click();
        await page.waitForURL(/page=2/);
        assert.match(await page.locator('.sa-pagination-summary').innerText(),/Mostrando 11 a \d+ de \d+ ventas/);
        await page.fill('.sa-list-form input[name="search"]',`${ctx.prefix}-LONG`);
        await page.locator('.sa-list-form button[type="submit"]').click();
        await page.waitForURL(/search=E2E-PROD/);
        const filtered=await page.locator('.sa-list-live-table tbody tr').allInnerTexts();
        assert.ok(filtered.some(t=>t.includes(first.sale_number))&&filtered.length===1,`product search -> ${filtered.length}`);
        await page.selectOption('.sa-list-form select[name="payment_method"]','debit_card');
        await page.fill('.sa-list-form input[name="search"]','');
        await page.locator('.sa-list-form button[type="submit"]').click();
        await page.waitForURL(/payment_method=debit_card/);
        const methods=await page.locator('.sa-list-live-table tbody tr td:nth-child(5)').allInnerTexts();
        assert.ok(methods.length>0&&methods.every(m=>m==='Tarjeta de débito'),`payment filter ${methods}`);
        results.sales_list={rows,summary,filtered:filtered.length,debit_rows:methods.length};
        await page.locator(`.sa-list-live-table a.sa-ref`,{hasText:first.sale_number}).first().click().catch(async()=>{await loaded(`/sales/${first.sale_number}`);});
        await page.waitForURL(new RegExp(`/sales/${first.sale_number}$`));
        const detail=await page.content();
        assert.ok(detail.includes(`Venta #${first.sale_number}`)&&detail.includes('Productos (5)')&&!detail.includes('no disponible'),'live sale detail');
        assert.ok((await overflow()).cells,'detail overflow');
        results.sale_detail=page.url();

        /* ---------- PART F: reference pages are static (zero live requests) ---------- */
        for(const url of ['/inventory/summary?ref=1','/sales?ref=1','/sales/list?ref=1']){
            await checkPage(url,null,false);
            assert.equal(await page.locator('#ix-search-input').count(),0,`${url}: live search must not render in reference`);
            assert.ok((await page.locator('.ix-bell-badge').innerText())==='3',`${url}: reference keeps the static mock badge`);
        }

        /* ---------- PART I: smoke viewports ---------- */
        for(const [w,h] of [[1366,768],[1280,720]]){
            await page.setViewportSize({width:w,height:h});
            for(const url of ['/inventory/summary','/sales','/sales/list']){
                await loaded(url);await page.waitForTimeout(300);const ov=await overflow();
                assert.ok(ov.page&&ov.cells,`${url} @${w}: ${JSON.stringify(ov)}`);
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
        if(req)results.cleanup=(await req.post(origin+'/__sales_e2e__/cleanup')).status();
        fs.writeFileSync(`${output}/results.json`,JSON.stringify(results,null,2));
        console.log(JSON.stringify(results,null,2));
        await browser.close();
    }
    process.exit(results.pass?0:1);
})();
