/* Orders metadata browser proof: priority / payment status / contact / address / notes, target table,
   3-dot actions (detail, edit, status, payment status, duplicate, print, cancel) and Más filtros.
   Writes only through the guarded local harness (scripts/serve_sales_e2e.py, ferreteria_conejo_e2e).
   1600x900, DPR 1. Orders must never change stock or create StockMovement. */
const {chromium,expect}=require('@playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const origin=process.env.SALES_E2E_URL || 'http://127.0.0.1:5024';
assert.equal(new URL(origin).hostname,'127.0.0.1','E2E requires a local disposable server');
const output='.tmp/orders-metadata-validation';

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
        const noOverflow=async()=>page.evaluate(()=>{
            const cells=[...document.querySelectorAll('td, th, .sa-chip, .ol-detail-grid dd')];
            const ellipsized=c=>getComputedStyle(c).textOverflow==='ellipsis'&&getComputedStyle(c).overflow==='hidden'&&(c.title||c.querySelector('[title]'));
            const bad=cells.filter(c=>c.scrollWidth>c.clientWidth+1&&!ellipsized(c)).map(c=>`${c.tagName}.${c.className}:${c.textContent.trim().slice(0,30)} ${c.scrollWidth}>${c.clientWidth}`);
            return {page:document.documentElement.scrollWidth<=innerWidth,cells:bad.length===0,bad:bad.slice(0,5)};
        });
        const assertNoOverflow=async label=>{const o=await noOverflow();assert.ok(o.page&&o.cells,`${label}: ${JSON.stringify(o)}`);};
        const settled=id=>expect(page.locator(id)).toHaveAttribute('aria-busy','false');
        const loaded=async url=>{await page.goto(origin+url,{waitUntil:'networkidle'});await page.waitForFunction(()=>document.fonts.check('400 12px Inter'));};
        const stockSnapshot=async()=>{const s={};for(const id of [...ctx.product_ids,ctx.long_product_id]){const p=await api(`/api/products/${id}`);s[id]=[p.current_stock,(await api(`/api/inventory/products/${id}/movements`)).count];}return s;};
        const row=number=>page.locator('#ov-rows tr',{hasText:number});
        const menu=async(number,action)=>{await row(number).locator('.ov-dots').click();await expect(page.locator('#ol-menu')).toBeVisible();await page.locator(`#ol-menu [data-action="${action}"]`).click();};
        const today=(await api('/api/orders/options')).today;
        const plus=days=>{const d=new Date(`${today}T12:00:00Z`);d.setUTCDate(d.getUTCDate()+days);return d.toISOString().slice(0,10);};
        const stockBefore=await stockSnapshot();
        const longAddress='Avenida Intercomunal Guarenas-Guatire, sector Las Clavellinas, Centro Empresarial Torre Norte, piso 12, oficina 12-B, frente a la estación de servicio, Guatire, estado Miranda, Venezuela — entregar por el portón trasero';
        const longNotes='Llamar 30 minutos antes de llegar. '.repeat(12).trim();

        /* ---------- Reference: deterministic, zero writes ---------- */
        const mutations=[];
        page.on('request',r=>{if(r.url().includes('/api/')&&!['GET','HEAD','OPTIONS'].includes(r.method()))mutations.push(r.method()+' '+r.url());});
        const totalBefore=(await api('/api/orders?page=1')).total;
        await loaded('/sales/orders?ref=1');
        await expect(page.locator('tbody tr').first()).toContainText('PED-000067');
        await page.getByRole('button',{name:'Nuevo pedido',exact:true}).click();
        await page.locator('#ol-save').click();await page.keyboard.press('Escape');
        assert.deepEqual(mutations,[],'reference mode issued a mutation');
        assert.equal((await api('/api/orders?page=1')).total,totalBefore);
        results.reference_zero_writes=true;

        /* ---------- New order with every metadata field (long customer, long product) ---------- */
        await loaded('/sales/orders');await settled('#ov-rows');
        await expect(page.locator('.ov-table thead th')).toHaveText(['#','Fecha','Cliente','Productos','Entrega estimada','Prioridad','Estado de pago','Estado','Acciones']);
        await page.getByRole('button',{name:'Nuevo pedido',exact:true}).click();
        await expect(page.locator('#ol-drawer')).toBeVisible();
        assert.equal(await page.locator('#ol-drawer').getByText('No disponible en esta versión').count(),0);
        await expect(page.locator('#ol-order-priority option')).toHaveText(['Baja','Media','Alta','Urgente']);
        await expect(page.locator('#ol-order-payment option')).toHaveText(['Pendiente','Pagado']);
        let wait=page.waitForResponse(r=>r.url().includes('/api/customers')&&r.url().includes(encodeURIComponent(`E2E-CUST-${ctx.run}-LONG`)));
        await page.locator('#ol-customer').fill(`E2E-CUST-${ctx.run}-LONG`);await wait;
        await page.locator('#ol-customer-list [role="option"]').first().click();
        await expect(page.locator('#ol-order-phone')).toHaveValue('0424-9999999-999999999'); // prefilled from customer, editable
        await page.locator('#ol-order-phone').fill('0414 555 0199');
        await page.locator('#ol-order-priority').selectOption('urgent');
        await page.locator('#ol-order-payment').selectOption('paid');
        await page.locator('#ol-order-address').fill(longAddress);
        await page.locator('#ol-order-notes').fill(longNotes);
        await page.locator('#ol-order-delivery').fill(plus(4));
        wait=page.waitForResponse(r=>r.url().includes('/api/sales/products'));
        await page.locator('#ol-add').click();await wait;await settled('#spl-products');
        wait=page.waitForResponse(r=>r.url().includes('search='+encodeURIComponent(`${ctx.prefix}-LONG`)));
        await page.locator('#spl-search').fill(`${ctx.prefix}-LONG`);await wait;await settled('#spl-products');
        const longRow=page.locator('#spl-products tr',{hasText:`${ctx.prefix}-LONG`});
        await longRow.locator('input').fill('9999');await longRow.locator('button').click();
        await page.locator('#spl-confirm').click();
        await expect(page.locator('#ol-products tr[data-product]')).toHaveCount(1);
        await expect(page.locator('#ol-total')).toHaveText('$ 3,499.65');
        await assertNoOverflow('new order drawer');
        await shot('order-new-full-live');
        let created=page.waitForResponse(r=>r.url().endsWith('/api/orders')&&r.request().method()==='POST');
        await page.locator('#ol-save').click();
        const createdResponse=await created;assert.equal(createdResponse.status(),201,await createdResponse.text());
        const order=await createdResponse.json();
        assert.equal(order.priority,'urgent');assert.equal(order.payment_status,'paid');assert.equal(order.contact_phone,'0414 555 0199');
        assert.equal(order.delivery_address,longAddress);assert.equal(order.notes,longNotes);assert.equal(order.units,9999);
        await settled('#ov-rows');
        const first=row(order.order_number);
        await expect(first.locator('td').nth(5)).toHaveText('Urgente');
        await expect(first.locator('td').nth(6)).toHaveText('Pagado');
        await expect(first.locator('td').nth(7)).toHaveText('Pendiente');
        await expect(first.locator('td').nth(2)).toHaveAttribute('title',/E2E-CUST/);
        assert.equal(await first.locator('td').nth(2).evaluate(td=>getComputedStyle(td).textOverflow),'ellipsis');
        // Second order with defaults (medium / pending) through the API for badge contrast.
        const second=await(await context.request.post(origin+'/api/orders',{data:{customer_id:ctx.customer_id,estimated_delivery_date:plus(2),items:[{product_id:ctx.product_ids[0],quantity:3}]}})).json();
        const third=await(await context.request.post(origin+'/api/orders',{data:{customer_id:ctx.customer_id,estimated_delivery_date:plus(9),priority:'low',items:[{product_id:ctx.product_ids[1],quantity:1}]}})).json();
        await page.reload({waitUntil:'networkidle'});await settled('#ov-rows');
        await expect(row(second.order_number).locator('td').nth(5)).toHaveText('Media');
        await expect(row(third.order_number).locator('td').nth(5)).toHaveText('Baja');
        await expect(row(second.order_number).locator('td').nth(6)).toHaveText('Pendiente');
        await assertNoOverflow('orders list with metadata');
        await shot('orders-metadata-live');
        results.order=order.order_number;

        /* ---------- Más filtros: priority and payment status are real server-side filters ---------- */
        await page.getByRole('button',{name:'Más filtros'}).click();
        await expect(page.locator('#ol-filter-priority option')).toHaveText(['Todas las prioridades','Baja','Media','Alta','Urgente']);
        await expect(page.locator('#ol-filter-payment option')).toHaveText(['Todos los estados de pago','Pendiente','Pagado']);
        await page.locator('#ol-filter-priority').selectOption('urgent');
        await page.locator('#ol-filter-payment').selectOption('paid');
        await shot('orders-more-filters-full-live');
        wait=page.waitForResponse(r=>r.url().includes('priority=urgent')&&r.url().includes('payment_status=paid'));
        await page.locator('#ol-filter-form button[type="submit"]').click();
        const filtered=await(await wait).json();
        assert.ok(filtered.items.every(o=>o.priority==='urgent'&&o.payment_status==='paid'),'server-side priority/payment filter');
        await settled('#ov-rows');
        await expect(row(second.order_number)).toHaveCount(0);
        await expect(row(order.order_number)).toHaveCount(1);
        await expect(page.getByRole('button',{name:/Más filtros \(2\)/})).toBeVisible();
        await page.getByRole('button',{name:/Más filtros/}).click();
        await page.locator('#ol-reset').click();await settled('#ov-rows');
        results.more_filters=true;

        /* ---------- Actions menu ---------- */
        await row(order.order_number).locator('.ov-dots').click();
        await expect(page.locator('#ol-menu [role="menuitem"]')).toHaveText(['Ver detalle','Editar pedido','Cambiar estado','Cambiar estado de pago','Duplicar pedido','Imprimir','Cancelar pedido']);
        await expect(page.locator('#ol-menu hr')).toHaveCount(1);
        await shot('order-actions-live');
        // Detail drawer
        await page.locator('#ol-menu [data-action="detail"]').click();
        await expect(page.locator('#ol-detail')).toBeVisible();
        for(const text of ['Urgente','Pagado','0414 555 0199','Guatire','Llamar 30 minutos'])await expect(page.locator('#ol-detail-fields')).toContainText(text);
        await expect(page.locator('#ol-detail-lines tr')).toHaveCount(1);
        await expect(page.locator('#ol-detail-total')).toHaveText('$ 3,499.65');
        await assertNoOverflow('detail drawer');
        await shot('order-detail-live');
        await page.keyboard.press('Escape');await expect(page.locator('#ol-detail')).toBeHidden();
        // Payment status popover: paid -> pending, persisted after reload
        await menu(order.order_number,'payment');
        await expect(page.locator('#ol-payment-desc')).toHaveText(`Pedido: ${order.order_number}`);
        await expect(page.locator('#ol-payment-options input:checked')).toHaveValue('paid');
        await expect(page.locator('#ol-payment-options label')).toHaveText(['Pendiente','Pagado']);
        await page.locator('#ol-payment-options input[value="pending"]').check();
        await shot('order-payment-status-live');
        wait=page.waitForResponse(r=>r.url().includes(`/api/orders/${order.order_number}/payment-status`));
        await page.locator('#ol-payment-save').click();
        assert.equal((await wait).status(),200);await settled('#ov-rows');
        await expect(row(order.order_number).locator('td').nth(6)).toHaveText('Pendiente');
        await page.reload({waitUntil:'networkidle'});await settled('#ov-rows');
        await expect(row(order.order_number).locator('td').nth(6)).toHaveText('Pendiente');
        assert.equal((await api(`/api/orders/${order.order_number}`)).payment_status,'pending');
        // Edit: pending -> everything editable; change priority, phone, notes and quantity
        await menu(order.order_number,'edit');
        await expect(page.locator('#ol-new-title')).toHaveText('Editar pedido');
        await expect(page.locator('#ol-customer')).toHaveValue(/E2E-CUST/);
        await expect(page.locator('#ol-order-status')).toBeDisabled();
        await expect(page.locator('#ol-order-priority')).toHaveValue('urgent');
        await expect(page.locator('#ol-order-notes')).toHaveValue(longNotes);
        await page.locator('#ol-order-priority').selectOption('high');
        await page.locator('#ol-order-phone').fill('0212-9990001');
        await page.locator('#ol-order-notes').fill('Entregar en horario de oficina.');
        await page.locator('#ol-products [data-step="-1"]').click();
        await shot('order-edit-live');
        wait=page.waitForResponse(r=>r.url().endsWith(`/api/orders/${order.order_number}`)&&r.request().method()==='PATCH');
        await page.locator('#ol-save').click();
        const edited=await(await wait).json();
        assert.equal(edited.priority,'high');assert.equal(edited.contact_phone,'0212-9990001');assert.equal(edited.units,9998);assert.equal(edited.notes,'Entregar en horario de oficina.');
        await settled('#ov-rows');
        await expect(row(order.order_number).locator('td').nth(5)).toHaveText('Alta');
        await expect(row(order.order_number).locator('td').nth(3)).toHaveText('9998');
        // Change status: pending -> processing; then edit is metadata-only (structural locked)
        await menu(order.order_number,'state');
        await expect(page.locator('#ol-state-options label')).toHaveText(['En proceso','Cancelado']);
        await page.locator('#ol-state-options input[value="processing"]').check();
        wait=page.waitForResponse(r=>r.url().includes(`/api/orders/${order.order_number}/status`));
        await page.locator('#ol-state-save').click();assert.equal((await wait).status(),200);await settled('#ov-rows');
        await expect(row(order.order_number).locator('td').nth(7)).toHaveText('En proceso');
        await menu(order.order_number,'edit');
        await expect(page.locator('#ol-customer')).toBeDisabled();
        await expect(page.locator('#ol-add')).toBeDisabled();
        await expect(page.locator('#ol-products [data-remove]')).toBeDisabled();
        await page.locator('#ol-order-address').fill('Depósito central, galpón 3');
        wait=page.waitForResponse(r=>r.url().endsWith(`/api/orders/${order.order_number}`)&&r.request().method()==='PATCH');
        await page.locator('#ol-save').click();
        const patched=await wait;const body=patched.request().postDataJSON();
        assert.ok(!('items' in body)&&!('customer_id' in body),'processing edit must not send structural fields');
        assert.equal((await patched.json()).delivery_address,'Depósito central, galpón 3');
        await settled('#ov-rows');
        // Duplicate: new PED, pending / pago pendiente, copied lines and metadata
        wait=page.waitForResponse(r=>r.url().includes('/duplicate'));
        await menu(order.order_number,'duplicate');
        const dup=await(await wait).json();
        assert.notEqual(dup.order_number,order.order_number);assert.equal(dup.status,'pending');assert.equal(dup.payment_status,'pending');
        assert.equal(dup.priority,'high');assert.equal(dup.units,9998);assert.equal(dup.delivery_address,'Depósito central, galpón 3');
        await settled('#ov-rows');
        await expect(page.locator('#ov-feedback')).toContainText(dup.order_number);
        await expect(row(dup.order_number)).toHaveCount(1);
        // Print: real read-only page in a new tab
        const popup=context.waitForEvent('page');
        await menu(order.order_number,'print');
        const printPage=await popup;await printPage.waitForLoadState('domcontentloaded');
        assert.equal(new URL(printPage.url()).pathname,`/sales/orders/${order.order_number}/print`);
        await expect(printPage.locator('h1')).toHaveText(`Pedido ${order.order_number}`);
        for(const text of ['Alta','Pendiente','0212-9990001','Depósito central'])await expect(printPage.locator('dl')).toContainText(text);
        await expect(printPage.locator('tbody tr')).toHaveCount(1);
        await printPage.close();
        // Cancel the duplicate through the menu (confirm dialog) -> cancelled, no delete
        page.once('dialog',d=>d.accept());
        wait=page.waitForResponse(r=>r.url().includes(`/api/orders/${dup.order_number}/status`));
        await menu(dup.order_number,'cancel');
        assert.equal((await wait).status(),200);await settled('#ov-rows');
        await expect(row(dup.order_number).locator('td').nth(7)).toHaveText('Cancelado');
        await row(dup.order_number).locator('.ov-dots').click();
        await expect(page.locator('#ol-menu [data-action="cancel"]')).toBeDisabled();
        await expect(page.locator('#ol-menu [data-action="edit"]')).toBeDisabled();
        await expect(page.locator('#ol-menu [data-action="payment"]')).toBeEnabled();
        await page.keyboard.press('Escape');
        assert.equal((await api(`/api/orders/${dup.order_number}`)).status,'cancelled');
        await assertNoOverflow('orders list after actions');

        assert.deepEqual(await stockSnapshot(),stockBefore,'orders must never change stock or create StockMovement');
        assert.deepEqual(errors,[],'no page errors');
        results.duplicate=dup.order_number;
        fs.writeFileSync(`${output}/results.json`,JSON.stringify(results,null,2));
        console.log(`PASS verify_orders_metadata: reference zero writes; ${order.order_number} created with full metadata (urgent/paid/phone/address/notes, 150-char customer, long product, 9999 units); table priority/payment badges; Más filtros priority+payment server-side; menu detail/edit/status/payment/duplicate(${dup.order_number})/print/cancel; processing edit metadata-only; no overflow; stock & movements unchanged; no page errors.`);
    }finally{
        await browser.close();
    }
})().catch(error=>{console.error(error);process.exit(1);});
