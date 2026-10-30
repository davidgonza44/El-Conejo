/* Browser proof: shared header parity (global search + bell) LIVE vs REFERENCE on the ix shell.
   Runs only against the guarded local harness (scripts/serve_sales_e2e.py -> ferreteria_conejo_e2e);
   the few synthetic rows it adds are removed at the end. 1600x900, DPR 1, zoom 100%.
   Reference (?ref=1) must issue zero /api/ requests; live must show the real search and bell. */
const {chromium}=require('@playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const origin=process.env.SALES_E2E_URL || 'http://127.0.0.1:5024';
assert.equal(new URL(origin).hostname,'127.0.0.1','E2E requires a local disposable server');
const output='.tmp/shell-header-parity';
const FAKE=/sin leer|nuevo pedido|mensaje|seguridad|nueva venta/i;

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

        /* ---------- Seed (disposable DB only): one low-stock, one out-of-stock, one sale ---------- */
        const post=async(url,data,expected=201)=>{const r=await req.post(origin+url,{data});assert.equal(r.status(),expected,`${url}: ${await r.text()}`);return r.json();};
        const low=await post('/api/products',{code:`${ctx.prefix}-LOW`,name:`Cemento E2E ${ctx.run} Portland 50kg`,category_id:ctx.category_id,current_stock:8,minimum_stock:10,purchase_price:'5.00',sale_price:'7.00'});
        const out=await post('/api/products',{code:`${ctx.prefix}-OUT`,name:`Agotado E2E ${ctx.run}`,category_id:ctx.category_id,current_stock:0,minimum_stock:3,purchase_price:'2.00',sale_price:'3.00'});
        const sale=await post('/api/sales',{customer_id:ctx.long_customer_id,payment_method:'cash',payment_condition:'cash',idempotency_key:crypto.randomUUID(),items:[{product_id:ctx.product_ids[0],quantity:1}]});
        const alerts=await(await req.get(origin+'/api/shell/alerts')).json();
        assert.ok(alerts.low_stock>=1&&alerts.out_of_stock>=1&&alerts.items.length>=2,'seeded alerts counted');
        assert.equal(alerts.count,alerts.low_stock+alerts.out_of_stock,'badge = low + out');
        assert.ok(alerts.items.every(i=>['bajo_stock','agotado'].includes(i.status)&&!('purchase_price' in i)),'alert rows are real stock states without purchase_price');
        results.seed={alerts:alerts.count,low_stock:alerts.low_stock,out_of_stock:alerts.out_of_stock,sale:sale.sale_number};

        /* ---------- Helpers ---------- */
        const shot=async name=>{await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:`${output}/${name}.png`});};
        const loaded=async url=>{await page.goto(origin+url,{waitUntil:'networkidle'});await page.waitForFunction(()=>document.fonts.check('400 12px Inter'));};
        /* [x, y, w, h, gap-to-layout-right]: the header is right-aligned inside .ix-main, so `gap` is the parity
           measure that does not depend on the vertical scrollbar real data may add (the fixture fits 900px and
           reference mode hides its scrollbar; live shows the normal 15px one when content is taller). */
        const box=sel=>page.evaluate(s=>{const e=document.querySelector(s);if(!e)return null;const r=e.getBoundingClientRect();const m=document.querySelector('.ix-main').getBoundingClientRect();return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height),Math.round(m.right-r.right)];},sel);
        const header=async()=>({title:await box('.ix-header .ix-title'),search:await box('.ix-header .ix-search'),bell:await box('.ix-header .ix-bell'),scrollbar:await page.evaluate(()=>window.innerWidth-Math.round(document.querySelector('.ix-main').getBoundingClientRect().right))});
        const overflow=()=>page.evaluate(()=>{
            const cw=document.documentElement.clientWidth;
            const clipped=[...document.querySelectorAll('.ix-search-title, .ix-search-sub, .ix-bell-item .ix-row-title, .ix-bell-item .ix-row-desc')]
                .filter(c=>c.scrollWidth>c.clientWidth+1&&!(getComputedStyle(c).textOverflow==='ellipsis'&&getComputedStyle(c).overflow==='hidden')).length;
            const inside=[...document.querySelectorAll('#ix-search-results:not([hidden]), #ix-bell-panel:not([hidden])')]
                .every(p=>{const r=p.getBoundingClientRect();return r.left>=0&&r.right<=cw+1&&r.top>=0&&r.bottom<=document.documentElement.clientHeight+1;});
            const wide=[...document.querySelectorAll('body *')].filter(e=>{const r=e.getBoundingClientRect();return r.right>cw+1&&r.width>0;});
            const headerWide=wide.filter(e=>e.closest('.ix-header')).length;
            // Pre-existing, unrelated to the header: the live catalog footer renders every page number (ix_products_live.js).
            const preexisting=wide.length>0&&wide.every(e=>e.closest('#ix-pagination'));
            return {page:document.documentElement.scrollWidth<=cw,clipped,inside,headerWide,preexisting};
        });
        const checkPage=async(url,live)=>{
            apiRequests.length=0;
            await loaded(url);
            const html=await page.content();
            const o=await overflow();
            assert.ok((o.page||o.preexisting)&&o.headerWide===0&&o.clipped===0,`${url}: overflow ${JSON.stringify(o)}`);
            if(!o.page)results.preexisting_overflow={...(results.preexisting_overflow||{}),[url]:'#ix-pagination renders every page number (catalog pagination, not the header)'};
            const h=await header();
            assert.ok(h.title&&h.search&&h.bell,`${url}: header controls missing ${JSON.stringify(h)}`);
            assert.ok(await page.locator('.ix-header .ix-search').isVisible()&&await page.locator('.ix-header .ix-bell').isVisible(),`${url}: search/bell not visible`);
            assert.ok(h.search[0]>h.title[0]&&h.bell[0]>h.search[0]+h.search[2]-1,`${url}: order title < search < bell ${JSON.stringify(h)}`);
            if(live){
                assert.ok(!html.includes('3 sin leer')&&!FAKE.test(await page.locator('.ix-header').innerText()),`${url}: fake notification text in live`);
                assert.equal(await page.locator('#ix-search-input').count(),1,`${url}: live combobox`);
                assert.equal(await page.locator('#ix-bell').count(),1,`${url}: live bell`);
                assert.ok(apiRequests.some(r=>r.includes('/api/shell/alerts')),`${url}: live bell loads real alerts`);
            }else{
                assert.deepEqual(apiRequests,[],`${url}: reference must not call the API`);
                assert.equal(await page.locator('#ix-search-input').count(),0,`${url}: reference keeps the static mock`);
                assert.equal(await page.locator('.ix-bell-badge').innerText(),'3',`${url}: reference keeps static badge`);
            }
            results[url]={header:h,api_requests:apiRequests.length,overflow_free:o.page,header_overflow_free:true};
            return h;
        };
        const sameGeometry=(a,b,label)=>{
            assert.deepEqual(a.title.slice(0,4),b.title.slice(0,4),`${label}: title x/y/w/h live vs reference`);
            for(const k of ['search','bell'])assert.deepEqual(a[k].slice(1),b[k].slice(1),`${label}: ${k} y/w/h/right-gap live vs reference`);
        };

        /* ---------- Header parity ---------- */
        const dashLive=await checkPage('/dashboard',true);await shot('dashboard-header-live');
        const dashRef=await checkPage('/dashboard?ref=1',false);await shot('dashboard-header-reference');
        sameGeometry(dashLive,dashRef,'/dashboard');
        const invLive=await checkPage('/inventory/summary',true);await shot('inventory-summary-header-live');
        const invRef=await checkPage('/inventory/summary?ref=1',false);
        sameGeometry(invLive,invRef,'/inventory/summary');
        await checkPage('/products',true);
        await checkPage('/sales',true);
        results.parity=true;

        /* ---------- Live search on the dashboard ---------- */
        await loaded('/dashboard');
        const search=page.locator('#ix-search-input');
        apiRequests.length=0;
        await search.fill('z');await page.waitForTimeout(500);
        assert.ok(await page.locator('#ix-search-results').isHidden()&&!apiRequests.some(r=>r.includes('/api/shell/search')),'1 char must not search');
        await search.fill(`${ctx.prefix}-LOW`);
        await page.waitForSelector('#ix-search-results:not([hidden]) [role="option"]');
        assert.ok((await page.locator('#ix-search-results .ix-search-group').allTextContents()).map(t=>t.trim()).includes('Productos'));
        await search.press('ArrowDown');
        assert.equal(await page.locator('#ix-search-results [aria-selected="true"]').count(),1,'ArrowDown highlights');
        assert.ok((await overflow()).inside,'search dropdown inside viewport');
        await shot('global-search-live');
        await search.press('Escape');assert.ok(await page.locator('#ix-search-results').isHidden(),'Escape closes');
        await search.fill(sale.sale_number);
        await page.waitForSelector('#ix-search-results:not([hidden]) [role="option"]');
        assert.ok((await page.locator('#ix-search-results .ix-search-group').allTextContents()).some(t=>t.trim()==='Ventas'));
        await search.press('ArrowDown');await search.press('Enter');
        await page.waitForURL(new RegExp(`/sales/${sale.sale_number}$`));
        assert.ok((await page.locator('h1').innerText()).includes(sale.sale_number),'Enter opens the real sale detail');
        results.search={sale:page.url()};
        await loaded('/dashboard');
        await search.fill(`E2E-CUST-${ctx.run}-LONG`);
        await page.waitForSelector('#ix-search-results:not([hidden]) [role="option"]');
        await page.locator('#ix-search-results .ix-search-option').first().click();
        await page.waitForURL(/\/sales\/customers\?search=/);
        results.search.customer=page.url();
        await loaded('/dashboard');
        await search.fill(`${ctx.prefix}-LOW`);
        await page.waitForSelector('#ix-search-results:not([hidden]) [role="option"]');
        await page.locator('#ix-search-results .ix-search-option').first().click();
        await page.waitForURL(/\/products\?search=/);
        results.search.product=page.url();

        /* ---------- Live bell on the dashboard ---------- */
        await loaded('/dashboard');
        assert.equal(await page.locator('#ix-bell-badge').innerText(),String(alerts.count),'badge = real alert count');
        apiRequests.length=0;
        await page.locator('#ix-bell').click();
        await page.waitForSelector('#ix-bell-panel:not([hidden]) .ix-bell-item');
        assert.ok(apiRequests.every(r=>r.startsWith('GET ')),'bell opens with GET only');
        const items=await page.locator('#ix-bell-panel .ix-bell-item').count();
        assert.equal(items,alerts.items.length,'real product rows');
        assert.equal(await page.locator('#ix-bell-panel .ix-bell-item .ix-thumb').count(),items,'thumbnail (or fallback) per row');
        const panelText=await page.locator('#ix-bell-panel').innerText();
        assert.ok(!FAKE.test(panelText),'no fake notifications');
        const rowTitles=await page.locator('#ix-bell-panel .ix-bell-item .ix-row-title').allInnerTexts();
        assert.deepEqual(rowTitles,alerts.items.map(i=>i.name),'panel rows = real alert products from the API');
        const rowDescs=await page.locator('#ix-bell-panel .ix-bell-item .ix-row-desc').allInnerTexts();
        alerts.items.forEach((i,k)=>assert.ok(rowDescs[k].startsWith(`${i.status==='agotado'?'Agotado':'Bajo stock'} · ${i.current_stock} unidades`),`row ${k}: ${rowDescs[k]}`));
        const o=await overflow();assert.ok(o.inside&&o.clipped===0,`bell panel ${JSON.stringify(o)}`);
        await shot('notification-bell-live');
        await page.locator('#ix-bell-all').click();
        await page.waitForURL(/\/products\?status=bajo_stock/);
        await page.waitForLoadState('networkidle');
        assert.equal(await page.locator('#ix-filter-status').inputValue(),'bajo_stock','Ver todas applies the alert filter');
        results.bell={badge:alerts.count,items,ver_todas:page.url()};

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
