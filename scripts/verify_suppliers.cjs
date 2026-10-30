const {chromium,expect}=require('@playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const origin='http://127.0.0.1:5033';
const output='.tmp/suppliers-validation';
(async()=>{
 const browser=await chromium.launch({channel:'msedge'});
 const results={checks:[],overflow:{},errors:[]};
 try{
  const context=await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1,locale:'es-VE'});
  await context.route(/https:\/\/(cdn.jsdelivr.net|fonts.googleapis.com|fonts.gstatic.com)\//,route=>{
   const url=new URL(route.request().url());const name=url.hostname==='fonts.googleapis.com'?'inter.css':path.basename(url.pathname);
   const local=path.join('.tmp/sales-reports-validation',name);
   if(!/\.(css|js|woff2?|ttf)$/.test(name)||!fs.existsSync(local)) return route.abort();
   return route.fulfill({path:local,contentType:name.endsWith('.css')?'text/css':name.endsWith('.js')?'application/javascript':'font/woff2'});
  });
  const page=await context.newPage();page.on('pageerror',error=>results.errors.push(error.message));
  fs.mkdirSync(output,{recursive:true});
  const go=async url=>{await page.goto(origin+url,{waitUntil:'networkidle'});await page.evaluate(()=>document.fonts.ready);};
  const shot=async name=>page.screenshot({path:path.join(output,name+'.png')});
  const api=async url=>{const r=await context.request.get(origin+url);assert.equal(r.status(),200);return r.json();};
  await go('/__suppliers_e2e__/login');
  const ids=await api('/__suppliers_e2e__/context');assert.equal(ids.database,'ferreteria_conejo_e2e');
  const live='/suppliers?search='+ids.run;
  await expect(page.locator('#ix-nav-compras')).toBeVisible();
  await expect(page.locator('#ix-nav-compras [aria-current=page]')).toHaveText('Proveedores');
  await expect(page.locator('#ix-nav-inv a[href*="suppliers"]')).toHaveCount(0);
  await expect(page.locator('#ix-nav-pred a')).toHaveCount(5);
  await shot('suppliers-main-live');results.checks.push('live database main and shared sidebar');
  const before=await api('/__suppliers_e2e__/write-count');
  await go('/suppliers?ref=1');
  await shot('suppliers-main-reference');
  await page.locator('[data-open=sp-export-dialog]').click();await expect(page.locator('#sp-export-dialog')).toBeVisible();await shot('suppliers-export-modal');
  await page.locator('#sp-export-dialog [data-close]').first().click();
  await page.locator('[data-open=sp-filters-dialog]').click();await shot('suppliers-filters-modal');
  await page.locator('#sp-filters-dialog [data-close]').first().click();
  await page.locator('#sp-new').click();await shot('suppliers-new-drawer');
  await page.locator('#sp-editor [data-close]').first().click();
  await page.locator('[data-action=detail]').first().click();await expect(page.locator('#sp-detail')).toBeVisible();
  await page.locator('#sp-detail [data-close]').click();
  const after=await api('/__suppliers_e2e__/write-count');assert.equal(after.count,before.count);results.checks.push('reference UI zero database writes');
  for(const [width,height] of [[1600,900],[1366,768],[1280,720]]){
   await page.setViewportSize({width,height});await go('/suppliers?ref=1');
   const dimensions=await page.evaluate(()=>({scrollWidth:document.documentElement.scrollWidth,innerWidth}));
   assert.ok(dimensions.scrollWidth<=dimensions.innerWidth);results.overflow[width]=dimensions;
   await expect(page.locator('.sp-pagination')).toBeInViewport({ratio:1});
   await expect(page.locator('.sp-table tbody tr').last()).toBeInViewport({ratio:0.9});
   if(width!==1600)await shot('suppliers-main-'+width);
   await page.locator('.sp-table-scroll').evaluate(el=>{el.scrollLeft=el.scrollWidth;});
   await expect(page.locator('.sp-table [data-action=menu]').first()).toBeInViewport();
   await page.locator('.sp-table-scroll').evaluate(el=>{el.scrollLeft=0;});
   await page.locator('#sp-new').click();
   const footer=page.locator('#sp-editor footer');await expect(footer).toBeVisible();
   const box=await footer.boundingBox();assert.ok(box.y+box.height<=height+1);
   await page.locator('#sp-editor .sp-drawer-body').evaluate(el=>{el.scrollTop=el.scrollHeight;});
   await expect(page.locator('#sp-editor textarea')).toBeInViewport();
   await page.locator('#sp-editor [data-close]').first().click();
   for(const modal of ['sp-filters-dialog','sp-export-dialog']){
    await page.locator(`[data-open=${modal}]`).click();await page.locator(`#${modal} footer button`).last().scrollIntoViewIfNeeded();await expect(page.locator(`#${modal} footer button`).last()).toBeInViewport();await page.locator(`#${modal} [data-close]`).first().click();
   }
  }
  results.checks.push('overflow and reachable modal/drawer footers at three sizes');
  await page.setViewportSize({width:1600,height:900});await go(live);
  await page.locator('[data-action=menu]').first().click();await shot('suppliers-actions-menu');
  await page.locator('#sp-menu [data-action=edit]').click();await expect(page.locator('#sp-editor')).toBeVisible();await shot('suppliers-edit-drawer');
  await page.locator('#sp-editor [name=notes]').fill('Actualizado mediante Playwright E2E');
  await page.locator('#sp-editor [type=submit]').click();await expect(page.locator('#sp-detail')).toBeVisible();
  await shot('suppliers-detail-drawer');
  for(const tab of ['purchases','contacts','history','information']){await page.locator(`[data-tab=${tab}]`).click();await expect(page.locator('#sp-panel-'+tab)).toBeVisible();}
  await page.locator('#sp-detail [data-close]').click();results.checks.push('edit persisted; eye detail and all tabs');
  await go(live);
  const firstId=Number(await page.locator('[data-action=menu]').first().getAttribute('data-id'));
  const was=(await api('/api/suppliers/'+firstId)).is_preferred;
  await page.locator('[data-action=menu]').first().click();await page.locator('#sp-menu [data-action=preferred]').click();
  await page.locator('#sp-confirm [type=submit]').click();await page.waitForLoadState('networkidle');
  await expect.poll(async()=>(await api('/api/suppliers/'+firstId)).is_preferred).toBe(!was);results.checks.push('preferred toggle persists');
  await page.locator('.sp-pagination a').filter({hasText:/^2$/}).click();await expect(page.locator('.sp-pagination')).toContainText('Mostrando 9 a 12 de 12 proveedores');
  await page.locator('#sp-per-page').selectOption('16');await expect(page.locator('.sp-table tbody tr')).toHaveCount(12);results.checks.push('pagination and page size');
  await go(live);await page.locator('[data-open=sp-filters-dialog]').click();
  await page.locator('#sp-filters-dialog [name=category]').selectOption('Materiales');await page.locator('#sp-filters-dialog button').filter({hasText:'Aplicar filtros'}).click();
  await expect(page.locator('.sp-table tbody tr')).toHaveCount(4);results.checks.push('advanced filters apply real set');
  await page.locator('#sp-filters .sp-clear').click();await expect(page.locator('#sp-filters [name=search]')).toHaveValue('');results.checks.push('filter reset');
  await go(live);
  for(const format of ['csv','xlsx','pdf']){
   await page.locator('[data-open=sp-export-dialog]').click();await page.locator(`#sp-export-dialog input[value=${format}]`).check();
   const download=page.waitForEvent('download');await page.locator('#sp-export-dialog footer button').last().click();
   assert.ok((await download).suggestedFilename().endsWith('.'+format));results.checks.push('download '+format);
  }
  await page.locator('[data-action=menu]').first().click();await page.locator('#sp-menu a').filter({hasText:'Crear orden de compra'}).click();
  await expect(page.locator('#po-dialog')).toBeVisible();await expect(page.locator('#po-dialog [name=supplier_id]')).toHaveValue(String(firstId));results.checks.push('purchase creation preselected');
  await go(live);await page.locator('[data-action=menu]').first().click();await page.locator('#sp-menu a').filter({hasText:'Ver órdenes de compra'}).click();
  await expect(page.locator('#po-filters [name=supplier_id]')).toHaveValue(String(firstId));results.checks.push('view orders canonical supplier filter');
  await go(live);await page.locator('#sp-new').click();
  for(const [name,value] of Object.entries({name:'UI supplier '+ids.run,tax_id:'UI-'+ids.run,contact_name:'Contacto UI',phone:'0412-5550000',email:'ui@example.invalid',category:'Pruebas UI'}))await page.locator(`#sp-editor [name=${name}]`).fill(value);
  await page.locator('#sp-editor [type=submit]').click();await expect(page.locator('#sp-detail')).toBeVisible();
  const createdId=new URL(page.url()).searchParams.get('detail');assert.ok(createdId);results.checks.push('new supplier persists');
  await page.locator('#sp-detail [data-close]').click();
  const deletion=await context.request.delete(origin+'/api/suppliers/'+createdId,{data:{}});assert.equal(deletion.status(),200);
  assert.deepEqual(results.errors,[]);
  fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
