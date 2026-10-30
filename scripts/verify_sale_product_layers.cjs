// Run only against scripts/test_settings_reference.py --serve (no database).
const {chromium,expect}=require('@playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const out='.tmp/sale-layers-validation';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const context=await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1,ignoreHTTPSErrors:true});
 const page=await context.newPage(); const errors=[],writes=[];
 page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',route=>{if(!['GET','HEAD'].includes(route.request().method())){writes.push(route.request().url());return route.abort()}return route.continue()});
 await context.addInitScript(()=>{window.cameraCalls=0;if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=()=>{window.cameraCalls++;throw Error('Camera prohibited')};});
 const go=async()=>{await page.goto('http://127.0.0.1:5000/sales/new?ref=1',{waitUntil:'networkidle'});await page.evaluate(()=>document.fonts.ready)};
 await go();
 assert.deepEqual(await page.evaluate(()=>[innerWidth,innerHeight,devicePixelRatio,visualViewport.scale]),[1600,900,1,1]);
 assert(fs.readFileSync(out+'/base-before.png').equals(await page.screenshot({path:out+'/base-after.png'})),'Base pixel equality');
 const drawer=page.locator('#spl-drawer'), scanner=page.locator('#spl-scanner');
 const openDrawer=()=>page.getByRole('button',{name:'Agregar producto',exact:true}).click();
 const openScan=()=>page.getByRole('button',{name:'Escanear',exact:true}).click();
 for(const [open,layer] of [[openDrawer,drawer],[openScan,scanner]]){
  for(const close of ['Cancelar','X','Escape','overlay']){
   await open();await expect(layer).toBeVisible();assert.equal(await page.locator('dialog[open]').count(),1);
   assert.equal(await page.evaluate(()=>document.body.style.overflow),'hidden');
   if(close==='Cancelar')await layer.getByRole('button',{name:'Cancelar',exact:true}).click();
   if(close==='X')await layer.getByRole('button',{name:/^Cerrar/}).click();
   if(close==='Escape')await page.keyboard.press('Escape');
   if(close==='overlay')await page.mouse.click(5,5);
   await expect(layer).toBeHidden();assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
   assert.equal(await page.evaluate(()=>document.activeElement.closest('.sa-products-actions')!==null),true);
  }
 }
 await openDrawer();await page.screenshot({path:out+'/drawer-initial.png'});
 console.log('DRAWER bounds',await drawer.boundingBox());
 await expect(page.locator('#spl-products tr')).toHaveCount(8);
 await page.locator('#spl-search').fill('TAL-750W');await expect(page.locator('#spl-products tr')).toHaveCount(1);
 await page.locator('#spl-search').fill('no existe');await expect(page.locator('#spl-empty')).toBeVisible();
 await page.locator('#spl-search').fill('');
 await page.locator('#spl-category').selectOption('Herramientas eléctricas');await expect(page.locator('#spl-products tr')).toHaveCount(2);
 await page.locator('#spl-category').selectOption('');
 for(const [status,count]of [['Disponible',4],['Bajo stock',3],['Agotado',1]]){
  await page.locator('#spl-stock').selectOption(status);await expect(page.locator('#spl-products tr')).toHaveCount(count);
 }
 await expect(page.locator('#spl-products button')).toBeDisabled();
 await page.locator('#spl-stock').selectOption('');
 const drill=page.locator('#spl-products tr[data-code="TAL-750W"]');
 for(const qty of ['0','-1','23','1.5']){await drill.locator('input').fill(qty);await drill.locator('button').click();await expect(page.locator('#spl-count')).toHaveText('Productos seleccionados: 0');await expect(drill.locator('input')).toHaveAttribute('aria-invalid','true')}
 await drill.locator('input').fill('2');await drill.locator('button').click();await drill.locator('button').click();await expect(page.locator('#spl-count')).toHaveText('Productos seleccionados: 1');
 const tape=page.locator('#spl-products tr[data-code="CIN-5M"]');await tape.locator('input').fill('3');await tape.locator('button').click();
 await expect(page.locator('#spl-count')).toHaveText('Productos seleccionados: 2');
 await page.locator('#spl-count').click();await expect(page.locator('#spl-selected li')).toHaveCount(2);await page.locator('#spl-count').click();
 await page.screenshot({path:out+'/drawer-selected.png'});
 await page.locator('#spl-confirm').click();await expect(drawer).toBeHidden();
 await expect(page.locator('.sa-lines-table tbody tr')).toHaveCount(4);
 await expect(page.locator('.sa-lines-table tbody tr').first().locator('.sa-qty')).toHaveAttribute('aria-valuenow','4');
 await expect(page.locator('.sa-totals dd').last()).toHaveText('$ 834.50');
 await page.screenshot({path:out+'/sale-updated.png'});
 await go();await expect(page.locator('.sa-lines-table tbody tr')).toHaveCount(3);await expect(page.locator('.sa-totals dd').last()).toHaveText('$ 309.50');
 await openScan();await page.screenshot({path:out+'/scanner-initial.png'});console.log('SCANNER bounds',await scanner.boundingBox());
 await expect(page.locator('#spl-simulate')).toBeVisible();await expect(page.locator('#spl-last-product')).toContainText('TAL-750W');
 await page.locator('#spl-code').fill('INVALID');await expect(page.locator('#spl-last-product')).toHaveText('Producto no encontrado');await expect(page.locator('#spl-scan-confirm')).toBeDisabled();
 await page.locator('#spl-code').fill(' tal-750w ');await page.keyboard.press('Enter');await expect(page.locator('#spl-last-product')).toContainText('TAL-750W');
 await page.locator('#spl-scan-add').click();await expect(page.locator('.sa-totals dd').last()).toHaveText('$ 429.50');await expect(scanner).toBeVisible();
 await page.locator('#spl-code').fill('INVALID');await page.locator('#spl-simulate').click();await expect(page.locator('#spl-code')).toHaveValue('TAL-750W');
 await page.locator('#spl-scan-confirm').click();await expect(scanner).toBeHidden();await expect(page.locator('.sa-lines-table tbody tr')).toHaveCount(3);await expect(page.locator('.sa-totals dd').last()).toHaveText('$ 549.50');
 await openDrawer();await page.evaluate(()=>[...document.querySelectorAll('.sa-products-actions button')].find(b=>b.textContent.trim()==='Escanear').click());await expect(scanner).toBeVisible();await expect(drawer).toBeHidden();
 await page.evaluate(()=>[...document.querySelectorAll('.sa-products-actions button')].find(b=>b.textContent.trim()==='Agregar producto').click());await expect(drawer).toBeVisible();await expect(scanner).toBeHidden();assert.equal(await page.locator('dialog[open]').count(),1);
 for(let i=0;i<25;i++){await page.keyboard.press('Tab');assert(await page.evaluate(()=>!!document.activeElement.closest('#spl-drawer')),'Focus trap')}
 await page.keyboard.press('Escape');await go();
 await openDrawer();const full=page.locator('#spl-products tr[data-code="TAL-750W"]');await full.locator('input').fill('22');await full.locator('button').click();await page.locator('#spl-confirm').click();await openScan();await expect(page.locator('#spl-scan-add')).toBeDisabled();await expect(page.locator('#spl-scan-confirm')).toBeDisabled();await page.keyboard.press('Escape');
 await go();assert.equal(await page.evaluate(()=>window.cameraCalls),0);
 const routes=['/sales','/sales/quotes','/sales/orders','/sales/customers','/products','/inventory','/categories','/suppliers','/dashboard'];
 for(const path of routes){const response=await page.goto('http://127.0.0.1:5000'+path+'?ref=1',{waitUntil:'networkidle'});assert.equal(response.status(),200,path);await expect(page.locator('#spl-drawer')).toHaveCount(0);await expect(page.locator('#ix-sidebar')).toContainText('Análisis predictivo');await page.screenshot({path:out+'/regression-'+path.slice(1).replaceAll('/','-')+'.png'})}
 await page.goto('http://127.0.0.1:5000/sales/new',{waitUntil:'networkidle'});await expect(page.locator('#spl-fixtures')).toHaveCount(0);await expect(page.locator('script[src*="ix_sale_product_layers"]')).toHaveCount(0);
 await go();await page.setViewportSize({width:390,height:844});await openDrawer();await expect(drawer).toBeVisible();assert((await drawer.boundingBox()).width<=390);await page.keyboard.press('Escape');await openScan();await expect(scanner).toBeVisible();assert((await scanner.boundingBox()).width<=390);await page.screenshot({path:out+'/scanner-mobile.png'});
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 console.log('PASS: base pixel equality; drawer, filters, quantities, multi-add, duplicate merge, totals, scan, invalid code, simulation, no camera, stock cap, single layer, focus, closures, reload, normal isolation, nine reference routes, mobile, no writes or JS errors.');
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1)});
