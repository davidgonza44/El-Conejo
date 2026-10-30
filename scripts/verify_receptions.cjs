/* Synthetic browser verification; harness asserts DATABASE() before every SQL write. */
const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const origin = 'http://127.0.0.1:5032';
const output = '.tmp/receptions-validation';
(async () => {
 const browser = await chromium.launch({channel:'msedge'});
 try {
  const context = await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1,locale:'es-VE'});
  await context.route(/https:\/\/(cdn.jsdelivr.net|fonts.googleapis.com|fonts.gstatic.com)\//, route => {
   const url = new URL(route.request().url());
   const name = url.hostname === 'fonts.googleapis.com' ? 'inter.css' : path.basename(url.pathname);
   const local = path.join('.tmp/sales-reports-validation',name);
   if (!/\.(css|js|woff2?|ttf)$/.test(name) || !fs.existsSync(local)) return route.abort();
   return route.fulfill({path:local,contentType:name.endsWith('.css')?'text/css':name.endsWith('.js')?'application/javascript':name.endsWith('.woff2')?'font/woff2':'font/ttf'});
  });
  const api = async url => {const r = await context.request.get(origin+url);assert.equal(r.status(),200,url);return r.json();};
  const ids = await api('/__purchases_e2e__/context');assert.equal(ids.database,'ferreteria_conejo_e2e');
  const page = await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const go = async url => {await page.goto(origin+url,{waitUntil:'networkidle'});await page.evaluate(()=>document.fonts.ready);};
  fs.mkdirSync(output,{recursive:true});
  const shot = async name => page.screenshot({path:path.join(output,name+'.png')});
  const close = async id => page.locator('#'+id+' > header [data-close]').click();
  await go('/__purchases_e2e__/login');
  const before = (await api('/__purchases_e2e__/write-count')).count;
  await go('/purchases/receptions?ref=1');
  await expect(page.locator('#ix-nav-compras [aria-current=page]')).toHaveText('Recepciones');
  await expect(page.locator('[aria-controls=ix-nav-compras]')).toHaveAttribute('aria-expanded','true');
  await shot('receptions-main-reference');
  await page.setViewportSize({width:1672,height:941});
  await page.locator('#rc-new').click();await expect(page.locator('#rc-status')).toHaveValue('Completada');
  await shot('receptions-new-drawer');
  await page.locator('#rc-receive-form [type=submit]').click();
  await expect(page.locator('.rc-error')).toContainText('solo lectura');await close('rc-drawer');
  await page.locator('[data-open=rc-export]').click();await shot('receptions-export-modal');await close('rc-export');
  await page.locator('[data-open=rc-filters]').click();await shot('receptions-filters-modal');await close('rc-filters');
  assert.equal((await api('/__purchases_e2e__/write-count')).count,before);
  const blocked = await context.request.post(origin+'/api/purchases/43/receptions?ref=1',{data:{}});assert.equal(blocked.status(),409);
  assert.equal((await api('/__purchases_e2e__/write-count')).count,before);
  await page.setViewportSize({width:1600,height:900});
  await go('/purchases/receptions');
  const today = await page.locator('#rc-context').evaluate(n=>JSON.parse(n.textContent).today);
  const created = await context.request.post(origin+'/api/purchases',{data:{supplier_id:ids.supplier_ids[0],items:[{product_id:ids.product_ids[0],quantity:10,unit_cost:'12.50'},{product_id:ids.product_ids[1],quantity:4,unit_cost:'3.00'}]}});
  assert.equal(created.status(),201);const order=await created.json();
  await go('/purchases/receptions?order_id='+order.id);
  await expect(page.locator('#rc-receive-form [name=order_id]')).toHaveValue(String(order.id));
  await expect(page.locator('[name=received_at]')).toHaveAttribute('min',order.ordered_date);
  await expect(page.locator('[name=received_at]')).toHaveAttribute('max',today);
  await expect(page.locator('[name=supplier]')).toHaveAttribute('readonly','');
  await page.locator('[data-line]').first().fill('6');
  await expect(page.locator('#rc-status')).toHaveValue('Parcial');
  await page.locator('#rc-receive-form [type=submit]').click();
  await page.waitForURL(url=>url.searchParams.has('detail'));
  await expect(page.locator('#rc-drawer')).toBeVisible();
  const partialId = Number(new URL(page.url()).searchParams.get('detail'));
  assert.equal((await api('/api/purchases/receptions/'+partialId)).status,'partial');
  await shot('receptions-detail-drawer');await close('rc-drawer');
  await go('/purchases/receptions?order_id='+order.id);
  const quantities=page.locator('[data-line]');await expect(quantities).toHaveCount(2);
  for (const input of await quantities.all()) await input.fill(await input.getAttribute('max'));
  await expect(page.locator('#rc-status')).toHaveValue('Completada');
  await page.locator('#rc-receive-form [type=submit]').click();await page.waitForURL(url=>url.searchParams.has('detail'));
  await expect(page.locator('#rc-drawer')).toBeVisible();const fullId = Number(new URL(page.url()).searchParams.get('detail'));
  assert.equal((await api('/api/purchases/receptions/'+fullId)).status,'completed');
  assert.equal((await api('/api/purchases/receptions/'+partialId)).status,'partial');
  await close('rc-drawer');await go('/purchases/receptions');await shot('receptions-main-live');
  await page.locator('[data-menu]').first().click();await shot('receptions-actions-menu');
  await page.locator('#rc-menu [data-movements]').click();await expect(page.locator('#rc-inventory')).toBeFocused();
  await expect(page.locator('#rc-drawer-body')).toContainText('MOV-');await close('rc-drawer');
  await page.locator('[data-detail]').first().click();await expect(page.locator('#rc-drawer')).toBeVisible();await close('rc-drawer');
  await go('/purchases/receptions/'+fullId+'/print');await shot('receptions-print');
  await page.evaluate(()=>{window.print=()=>{window.printCalled=true;};});await page.getByRole('button',{name:'Imprimir',exact:true}).click();assert.equal(await page.evaluate(()=>window.printCalled),true);
  await go('/purchases/receptions');
  await page.locator('#rc-inline [name=status]').selectOption('partial');await page.waitForURL(url=>url.searchParams.get('status')==='partial');
  await expect(page.locator('.po-table tbody')).not.toContainText('Completada');
  await page.locator('[data-open=rc-filters]').click();await page.locator('#rc-filters [name=responsible_id]').selectOption(String(ids.admin_id));
  await page.locator('#rc-filters [name=min_amount]').fill('1');await page.locator('#rc-filters [name=sort]').selectOption('amount_desc');
  await page.locator('#rc-filters button[type=submit],#rc-filters .is-primary').click();await page.waitForURL(url=>url.searchParams.get('sort')==='amount_desc');
  for (const format of ['csv','xlsx','pdf']) {
   await page.locator('[data-open=rc-export]').click();await page.locator(`[name=format][value=${format}]`).check();
   const pending=page.waitForEvent('download');await page.locator('#rc-export-form .is-primary').click();const download=await pending;assert.ok(download.suggestedFilename().endsWith('.'+format));
  }
  await go('/purchases/receptions');await page.locator('#rc-per-page').selectOption('16');await page.waitForURL(url=>url.searchParams.get('per_page')==='16');
  await page.locator('#rc-per-page').selectOption('8');await page.waitForURL(url=>url.searchParams.get('per_page')==='8');
  if (await page.getByRole('link',{name:'Página siguiente'}).count()) {await page.getByRole('link',{name:'Página siguiente'}).click();await page.waitForURL(url=>url.searchParams.get('page')==='2');}
  await go('/purchases/receptions');await page.locator('.po-table tbody tr').first().locator('td:nth-child(2) a').click();await expect(page.locator('#po-dialog')).toBeVisible();
  const geometry={};
  for (const [width,height] of [[1600,900],[1366,768],[1280,720]]) {
   await page.setViewportSize({width,height});
   for (const ref of [false,true]) {
    await go('/purchases/receptions'+(ref?'?ref=1':''));
    const result=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,boxes:Object.fromEntries(['.po-kpis','.rc-inline','.rc-table-card'].map(s=>{const r=document.querySelector(s).getBoundingClientRect();return [s,{x:r.x,y:r.y,width:r.width,height:r.height}];}))}));
    assert.equal(result.overflow,false,`${width} reference=${ref}`);geometry[`${width}-${ref}`]=result;
    if (!ref && width!==1600) await shot('receptions-main-'+width);
    for (const id of ['rc-filters','rc-export']) {await page.locator(`[data-open=${id}]`).click();assert.ok(await page.locator('#'+id).evaluate(n=>{const r=n.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight;}));await close(id);}
    await page.locator('#rc-new').click();await expect(page.locator('#rc-drawer')).toBeVisible();assert.ok(await page.locator('#rc-drawer footer').evaluate(n=>n.getBoundingClientRect().bottom<=innerHeight));await close('rc-drawer');
   }
  }
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'verification.json'),JSON.stringify({referenceWrites:0,playwright:'PASS',geometry},null,2));
  console.log('PASS: reference zero writes; partial/full saves; filters; exports; detail; movements; menu; PO; print; pagination; three viewport layouts.');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
