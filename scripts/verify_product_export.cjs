// Run against an explicitly isolated server. No product creation or update.
const {chromium}=require('@playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const output='.tmp/product-export-validation';
const origin=process.env.PRODUCT_EXPORT_TEST_URL || 'http://127.0.0.1:5021';
(async()=>{
 const browser=await chromium.launch({channel:'msedge'});
 try {
  fs.mkdirSync(output,{recursive:true});
  const page=await browser.newPage({viewport:{width:1600,height:900},deviceScaleFactor:1,locale:'es-VE'});
  const errors=[],http=[],requests=[],downloads=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('response',r=>{if(r.status()>=400&&r.status()!==400)http.push([r.url(),r.status()]);});
  page.on('request',r=>{if(r.method()!=='GET')requests.push({url:r.url(),method:r.method(),body:r.postDataJSON()});});
  page.on('download',d=>downloads.push(d.suggestedFilename()));
  await page.goto(origin+'/products',{waitUntil:'networkidle'});
  await page.evaluate(()=>document.fonts.ready);
  assert.deepEqual(await page.evaluate(()=>[innerWidth,innerHeight,devicePixelRatio,visualViewport.scale]),[1600,900,1,1]);
  await page.locator('#ix-filter-category').selectOption({index:1});
  await page.waitForFunction(()=>document.querySelector('#ix-catalog-wrap').getAttribute('aria-busy')==='false');
  const expected=await page.evaluate(async()=>{const r=await fetch('/api/products?category_id='+document.querySelector('#ix-filter-category').value+'&include_inactive=1');return (await r.json()).count});
  assert(expected>8,'Fixture must have more than one page');
  await page.locator('#ix-pages').getByRole('button',{name:'2',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#ix-pages [aria-current]')?.textContent==='2');
  const open=async()=>{await page.locator('.iv-prod-toolbar').getByRole('button',{name:'Exportar',exact:true}).click();await page.locator('#pl-dialog').waitFor({state:'visible'});};
  await open();
  assert(await page.locator('#pl-export [name=include_suppliers]').isDisabled());
  assert.equal(await page.locator('#pl-export [name=include_suppliers]').isChecked(),false);
  await page.screenshot({path:output+'/products-export-live-modal.png'});
  for(const format of ['xlsx','csv','pdf']){
   if(!await page.locator('#pl-dialog').isVisible())await open();
   await page.locator(`#pl-export [name=format][value=${format}]`).check();
   await page.screenshot({path:`${output}/products-export-${format}-pass.png`});
   const count=requests.length;
   const promise=page.waitForEvent('download');
   await page.locator('#pl-action').evaluate(n=>{n.click();n.click()});
   const download=await promise;
   assert(download.suggestedFilename().endsWith('.'+format));
   await download.saveAs(`${output}/catalog.${format}`);
   await page.locator('#pl-dialog').waitFor({state:'hidden'});
   assert.equal(requests.length,count+1,'duplicate export');
   const request=requests.at(-1);
   assert(request.url.endsWith('/api/products/export'));
   assert.equal(request.body.format,format);
   assert(request.body.filters.category_id);
   assert.equal(request.body.filters.page,undefined);
   assert.equal(request.body.filters.per_page,undefined);
   assert.equal(request.body.options.include_suppliers,false);
  }
  // Backend validation error stays in the existing modal; retry is possible.
  await open();
  await page.locator('#pl-export [name=include_suppliers]').evaluate(n=>{n.disabled=false;n.checked=true});
  const failure=page.waitForResponse(r=>r.url().endsWith('/api/products/export')&&r.status()===400);
  await page.locator('#pl-action').click();await failure;
  await page.waitForFunction(()=>!document.querySelector('#pl-action').disabled);
  assert(await page.locator('#pl-dialog').isVisible());
  assert(await page.locator('#pl-export-error').isVisible());
  assert.match(await page.locator('#pl-export-error').innerText(),/proveedores/);
  await page.keyboard.press('Escape');
  const beforeRequests=requests.length,beforeDownloads=downloads.length;
  await page.goto(origin+'/products?ref=1',{waitUntil:'networkidle'});await open();
  assert.equal(await page.locator('#pl-export input[type=checkbox]').last().isDisabled(),false);
  await page.locator('#pl-action').click();await page.locator('#pl-dialog').waitFor({state:'hidden'});
  assert.equal(requests.length,beforeRequests);assert.equal(downloads.length,beforeDownloads);
  assert.deepEqual(errors,[]);assert.deepEqual(http,[]);
  fs.writeFileSync(output+'/results.json',JSON.stringify({expected,downloads,requests,errors,http,referenceExportCalls:0},null,2));
  console.log(JSON.stringify({result:'PASS',expected,downloads,errors,http,referenceExportCalls:0,output}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
