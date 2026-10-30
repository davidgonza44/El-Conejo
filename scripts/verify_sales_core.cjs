/* Sales Core Phase A browser proof. Writes only through the guarded local harness
   (scripts/serve_sales_e2e.py, ferreteria_conejo_e2e). 1600x900, DPR 1, zoom 100%. */
const {chromium,expect}=require('@playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const origin=process.env.SALES_E2E_URL || 'http://127.0.0.1:5024';
assert.equal(new URL(origin).hostname,'127.0.0.1','E2E requires a local disposable server');
const output='.tmp/sales-core-validation';
const GEOMETRY={
    new:['.ix-sidebar','.sa-header','.sa-actionbar','.sa-new-top','.sa-new-top .sa-form-card','.sa-products-card','.sa-lines','.sa-totals-bar'],
    customers:['.ix-sidebar','.sa-header','.sa-toolbar','.sa-table-card','.sa-customers-table','.sa-pagination'],
};

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
        const ctx=await(await context.request.get(origin+'/__sales_e2e__/context')).json();
        assert.equal(ctx.database,'ferreteria_conejo_e2e');assert.match(ctx.engine,/MariaDB/);
        assert.equal((await context.request.post(origin+'/api/auth/login',{data:{identifier:ctx.seller_username,password:ctx.password}})).status(),200);
        fs.mkdirSync(output,{recursive:true});
        const api=async p=>(await context.request.get(origin+p)).json();
        const shot=async name=>{await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:`${output}/${name}.png`});};
        const geometry=selectors=>page.evaluate(sel=>Object.fromEntries(sel.map(s=>{const r=document.querySelector(s).getBoundingClientRect();return[s,[r.x,r.y,r.width,r.height].map(v=>Math.round(v))];})),selectors);
        const noOverflow=async()=>page.evaluate(()=>{
            const cells=[...document.querySelectorAll('td, .sa-customer, .px-product-name, .ix-row-title')];
            return {page:document.documentElement.scrollWidth<=innerWidth,cells:cells.every(c=>c.scrollWidth<=c.clientWidth+1),count:cells.length};
        });
        const mutations=[];
        const recorder=request=>{if(request.url().includes('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method()))mutations.push(request.method()+' '+request.url());};

        /* ---------- Reference: zero writes ---------- */
        const salesBefore=(await api('/api/sales?page=1')).total;
        const customersBefore=(await api('/api/customers?page=1')).total;
        page.on('request',recorder);
        await page.goto(origin+'/sales/new?ref=1',{waitUntil:'networkidle'});
        await page.waitForFunction(()=>document.fonts.check('400 12px Inter'));
        assert.deepEqual(await page.evaluate(()=>[innerWidth,innerHeight,devicePixelRatio,visualViewport.scale]),[1600,900,1,1]);
        const refNew=await geometry(GEOMETRY.new);await shot('new-reference');
        await page.getByRole('button',{name:'Guardar',exact:true}).click();
        await page.getByRole('button',{name:'Agregar producto',exact:true}).click();
        await page.keyboard.press('Escape');
        await expect(page.locator('.sa-lines-table tbody tr')).toHaveCount(3);
        await page.goto(origin+'/sales/customers?ref=1',{waitUntil:'networkidle'});
        const refCustomers=await geometry(GEOMETRY.customers);await shot('customers-reference');
        await page.getByRole('button',{name:'Nuevo cliente',exact:true}).click();
        page.off('request',recorder);
        assert.deepEqual(mutations,[],'reference must not mutate');
        assert.equal((await api('/api/sales?page=1')).total,salesBefore);
        assert.equal((await api('/api/customers?page=1')).total,customersBefore);
        results.reference_zero_writes=true;

        /* ---------- Live Nueva venta ---------- */
        const leaked=[];
        page.on('response',async r=>{if(r.url().includes('/api/sales/products')){const body=await r.text();if(body.includes('purchase_price'))leaked.push(r.url());}});
        await page.goto(origin+'/sales/new',{waitUntil:'networkidle'});
        await page.waitForFunction(()=>document.fonts.check('400 12px Inter'));
        await expect(page.locator('#sv-customer')).toBeVisible();
        // Shared Ferretería Pro shell (pro_shell) with real user footer; no legacy El Conejo links.
        const shell=async(activeText)=>{
            const sidebar=await page.locator('#ix-sidebar').innerText();
            assert.ok(sidebar.includes('Ferretería Pro')&&sidebar.includes('Sistema de Gestión'),'Pro brand');
            for(const legacy of ['El Conejo C.A.','Catálogo visual','Notas de entrega','Importación histórica','Asistente de inventario','Diagnóstico de historial'])assert.ok(!sidebar.includes(legacy),`legacy link present: ${legacy}`);
            await expect(page.locator('#ix-nav-sales')).toBeVisible();
            await expect(page.locator('#ix-nav-sales a')).toHaveText(['Nueva venta','Cotizaciones','Pedidos','Clientes']);
            await expect(page.locator('#ix-nav-sales a[aria-current="page"]')).toHaveText(activeText);
            for(const href of await page.locator('#ix-nav-sales a').evaluateAll(as=>as.map(a=>a.getAttribute('href'))))assert.ok(!href.includes('ref=1'),'live sidebar must not link to ?ref=1');
            await expect(page.locator('#ix-sidebar .ix-user-name')).toHaveText('Vendedor E2E');
            await expect(page.locator('#ix-sidebar .ix-branch')).toBeDisabled();
        };
        await shell('Nueva venta');
        await expect(page.locator('#sv-customer-name')).toHaveText('Público en general');
        assert.equal(await page.locator('.sa-fields .sa-field-box[role="textbox"]').nth(1).textContent(),'Vendedor E2E');
        await expect(page.getByRole('button',{name:'Escanear'})).toBeDisabled();
        await expect(page.getByRole('button',{name:'Más opciones de guardado'})).toBeDisabled();
        await expect(page.locator('#spl-fixtures')).toHaveCount(0);
        await shot('new-live-empty');
        // Customer autocomplete stores the id.
        const customerSearch=page.locator('#sv-customer-search');
        const filteredCustomers=page.waitForResponse(r=>r.url().includes('/api/customers')&&r.url().includes(encodeURIComponent(`E2E-CUST-${ctx.run}-00`)));
        await customerSearch.fill(`E2E-CUST-${ctx.run}-00`);await filteredCustomers;
        await expect(page.locator('#sv-customer-list [role="option"]')).toHaveCount(1);
        await page.locator('#sv-customer-list [role="option"]').first().click();
        assert.equal(await page.locator('#sv-customer-id').inputValue(),String(ctx.customer_id));
        // Product search adds rows; duplicates merge.
        const productSearch=page.locator('#sv-product-search');
        const addProduct=async term=>{
            const filtered=page.waitForResponse(r=>r.url().includes('/api/sales/products')&&r.url().includes('search='+encodeURIComponent(term)));
            await productSearch.fill(term);await filtered;
            await expect(page.locator('#sv-product-list [role="option"]')).toHaveCount(1);
            await page.locator('#sv-product-list [role="option"]').first().click();
        };
        await addProduct(`${ctx.prefix}-00`);
        await addProduct(`${ctx.prefix}-00`);
        await expect(page.locator('#sv-lines tr')).toHaveCount(1);
        await expect(page.locator('#sv-lines input[name="quantity"]')).toHaveValue('2');
        await addProduct(`${ctx.prefix}-01`);
        await addProduct(`${ctx.prefix}-LONG`);
        await expect(page.locator('#sv-lines tr')).toHaveCount(3);
        await expect(page.locator('#sv-lines img')).toHaveCount(0); // products without image use the neutral thumb
        const overflow=await noOverflow();assert.ok(overflow.page&&overflow.cells,JSON.stringify(overflow));
        assert.equal(await page.locator('#sv-lines .px-product-name').nth(2).getAttribute('title'),(await api(`/api/products/${ctx.long_product_id}`)).name);
        await page.locator('#sv-lines tr').nth(0).locator('input[name="discount_percent"]').fill('10');
        await page.locator('#sv-lines tr').nth(0).locator('input[name="discount_percent"]').dispatchEvent('change');
        await expect(page.locator('#sv-subtotal')).toHaveText('$ 360.35');
        await expect(page.locator('#sv-discount')).toHaveText('$ 24.00');
        await expect(page.locator('#sv-total')).toHaveText('$ 336.35');
        const sameGeometry=(reference,live,fixedHeights)=>{
            for(const key of Object.keys(reference)){
                const [rx,ry,rw,rh]=reference[key];const [lx,ly,lw,lh]=live[key];
                assert.deepEqual([lx,ly,lw],[rx,ry,rw],`geometry x/y/width ${key}`);
                if(fixedHeights.includes(key))assert.equal(lh,rh,`height ${key}`);
            }
        };
        const liveNew=await geometry(GEOMETRY.new);
        sameGeometry(refNew,liveNew,['.ix-sidebar','.sa-header','.sa-actionbar','.sa-new-top','.sa-new-top .sa-form-card','.sa-products-card','.sa-lines']);
        results.new_geometry={reference:refNew,live:liveNew};
        await shot('new-live-cart');
        // Insufficient stock: UI warning + backend 409, cart retained.
        const longQty=page.locator('#sv-lines tr').nth(2).locator('input[name="quantity"]');
        const available=(await api(`/api/products/${ctx.long_product_id}`)).current_stock;assert.ok(available>=2,'harness stock');
        await longQty.fill(String(available+4));await longQty.dispatchEvent('change');
        await expect(page.locator('#sv-feedback')).toContainText('Stock insuficiente');
        await expect(page.locator('#sv-feedback')).toContainText(`Disponible: ${available}`);
        const conflict=page.waitForResponse(r=>r.url().endsWith('/api/sales')&&r.request().method()==='POST');
        await page.getByRole('button',{name:'Guardar',exact:true}).click();
        assert.equal((await conflict).status(),409);
        await expect(page.locator('#sv-feedback')).toContainText('Stock insuficiente');
        await expect(page.locator('#sv-lines tr')).toHaveCount(3);
        await shot('new-live-409');
        assert.equal((await api(`/api/products/${ctx.long_product_id}`)).current_stock,available,'409 must not deduct');
        await longQty.fill('2');await longQty.dispatchEvent('change');
        const created=page.waitForResponse(r=>r.url().endsWith('/api/sales')&&r.request().method()==='POST');
        const save=page.getByRole('button',{name:'Guardar',exact:true});
        await save.click();
        const response=await created;assert.equal(response.status(),201);
        const sale=await response.json();
        await expect(page.locator('#sv-feedback')).toHaveText(`Venta ${sale.sale_number} registrada correctamente.`);
        await expect(page.locator('#sv-lines tr')).toHaveCount(0);
        await shot('new-live-success');
        assert.equal(sale.total,'336.70');
        assert.equal(sale.seller.name,'Vendedor E2E');
        assert.equal(sale.customer.id,ctx.customer_id);
        assert.equal((await api(`/api/products/${ctx.long_product_id}`)).current_stock,available-2);
        const detail=await api(`/api/sales/${sale.sale_number}`);
        assert.equal(detail.items.length,3);assert.ok(detail.items.every(i=>i.stock_movement_id));
        assert.deepEqual(leaked,[],'purchase_price must never reach the sale page');
        results.sale=sale.sale_number;

        /* ---------- Live Clientes ---------- */
        await page.goto(origin+'/sales/customers',{waitUntil:'networkidle'});
        await page.waitForFunction(()=>document.fonts.check('400 12px Inter'));
        const settled=()=>expect(page.locator('#cu-body')).toHaveAttribute('aria-busy','false');
        await settled();
        await shell('Clientes');
        for(const route of ['/sales','/sales/list','/sales/quotes','/sales/orders']){
            const html=await(await context.request.get(origin+route)).text();
            assert.ok(html.includes('Ferretería Pro')&&!html.includes('El Conejo C.A.')&&!html.includes('Catálogo visual'),`Pro shell on ${route}`);
        }
        const liveCustomers=await geometry(GEOMETRY.customers);
        sameGeometry(refCustomers,liveCustomers,['.ix-sidebar','.sa-header','.sa-toolbar']);
        results.customers_geometry={reference:refCustomers,live:liveCustomers};
        await expect(page.locator('#cu-body tr')).toHaveCount(5);
        await expect(page.locator('#cu-body tr').first()).toContainText('Público en general');
        await shot('customers-live');
        const searchFor=async value=>{const r=page.waitForResponse(x=>x.url().includes('/api/customers?')&&x.status()===200);await page.locator('#cu-search').fill(value);await r;await settled();};
        await searchFor(`E2E-CUST-${ctx.run}-LONG`);
        await expect(page.locator('#cu-body tr')).toHaveCount(1);
        const longOverflow=await noOverflow();assert.ok(longOverflow.page&&longOverflow.cells,JSON.stringify(longOverflow));
        assert.ok((await page.locator('#cu-body tr td').first().locator('span').getAttribute('title')).includes('Distribuidora'));
        await shot('customers-live-long');
        await searchFor(`E2E-CUST-${ctx.run}`);
        await expect(page.locator('#cu-summary')).toContainText('de 13 clientes');
        await page.getByRole('button',{name:'Página siguiente',exact:true}).click();await settled();
        await expect(page.locator('#cu-pages [aria-current]')).toHaveText('2');
        await expect(page.locator('#cu-body tr')).toHaveCount(5);
        await page.getByRole('button',{name:'Página siguiente',exact:true}).click();await settled();
        await expect(page.locator('#cu-body tr')).toHaveCount(3);
        await expect(page.getByRole('button',{name:'Página siguiente',exact:true})).toBeDisabled();
        // Nuevo cliente: validation, duplicate RIF, success.
        await page.getByRole('button',{name:'Nuevo cliente',exact:true}).click();
        await expect(page.locator('#cu-drawer')).toBeVisible();
        await page.getByRole('button',{name:'Guardar cliente',exact:true}).click();
        await expect(page.locator('#cu-drawer-feedback')).toHaveText('El nombre es obligatorio.');
        const existing=(await api(`/api/customers/${ctx.customer_id}`)).tax_id;
        await page.locator('#cu-name').fill(`E2E-CUST-${ctx.run}-NEW Ferretería San José`);
        await page.locator('#cu-tax-id').fill(existing.toLowerCase().replace(/-/g,''));
        await page.getByRole('button',{name:'Guardar cliente',exact:true}).click();
        await expect(page.locator('#cu-drawer-feedback')).toContainText('Ya existe un cliente con el RIF');
        await shot('customers-live-drawer');
        await page.locator('#cu-tax-id').fill('v 123456 7');
        await page.locator('#cu-email').fill(`E2E_new_${ctx.run}@Example.invalid`);
        const createdCustomer=page.waitForResponse(r=>r.url().endsWith('/api/customers')&&r.request().method()==='POST');
        await page.getByRole('button',{name:'Guardar cliente',exact:true}).click();
        const customer=await(await createdCustomer).json();
        assert.equal(customer.tax_id,'V-123456-7');assert.equal(customer.email,`e2e_new_${ctx.run}@example.invalid`);
        await expect(page.locator('#cu-drawer')).toBeHidden();
        await expect(page.locator('#cu-feedback')).toContainText('creado correctamente');
        await settled();
        await expect(page.locator('#cu-summary')).toContainText('de 15 clientes');
        await shot('customers-live-created');
        results.customer=customer.id;

        assert.deepEqual(errors,[],'no page errors');
        fs.writeFileSync(`${output}/results.json`,JSON.stringify(results,null,2));
        console.log('PASS verify_sales_core: reference zero writes; live sale '+sale.sale_number+' (409 then 201); customers live search/pagination/drawer; no overflow; no page errors.');
    }finally{
        await browser.close();
    }
})().catch(error=>{console.error(error);process.exit(1);});
