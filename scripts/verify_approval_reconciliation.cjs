/* Browser acceptance against serve_approval_reconciliation.py's disposable fixture only. */
const {chromium} = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  const context = await browser.newContext({viewport:{width:1600,height:900}, deviceScaleFactor:1});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const output = path.resolve('tmp/approval-reconciliation-ui');
  fs.mkdirSync(output, {recursive:true});
  const base = 'http://127.0.0.1:5049';
  try {
    await page.goto(base + '/__isolated_login/inventario');
    const options = await (await page.request.get(base + '/api/purchases/options')).json();
    const created = await page.request.post(base + '/api/purchases', {data:{supplier_id:options.suppliers[0].id,
      items:[{product_id:options.products[0].id, quantity:4, unit_cost:'2.00'}]}});
    assert.equal(created.status(), 201);
    const order = await created.json();
    await page.reload();
    await page.locator('#po-new').waitFor();
    await page.getByRole('button', {name:'Ver detalle ' + order.order_number}).click();
    await page.locator('#po-dialog[open]').waitFor();
    assert.equal(await page.locator('#po-dialog [data-action=approve]').count(), 0);
    await page.keyboard.press('Escape');
    await page.goto(base + '/__isolated_login/admin');
    await page.goto(base + '/purchases?status=pending_approval&start=&end=');
    await page.screenshot({path:path.join(output,'purchase-queue-1600.png'), fullPage:true});
    await page.getByRole('button', {name:'Ver detalle ' + order.order_number}).click();
    await page.locator('#po-dialog [data-action=approve]').click();
    await page.getByRole('button', {name:'Confirmar aprobación'}).click();
    await page.waitForURL(url => url.searchParams.get('detail') === String(order.id));
    await page.locator('#po-dialog[open]').waitFor();
    assert.match(await page.locator('#po-dialog').innerText(), /Aprobada por/i);
    await page.screenshot({path:path.join(output,'purchase-approved-detail-1600.png'), fullPage:true});
    await page.keyboard.press('Escape');
    await page.goto(base + '/reconciliation');
    await page.locator('[name=file]').setInputFiles({name:'synthetic.csv', mimeType:'text/csv', buffer:Buffer.from('product_code;quantity;product_name\nTEST-01;14;Synthetic item\nUNKNOWN;5;Unknown item\n')});
    await page.locator('[name=cutoff_at]').fill('2026-09-28T12:00');
    await page.getByRole('button', {name:'Previsualizar', exact:true}).click();
    await page.getByRole('button', {name:'Confirmar comparación actual'}).waitFor();
    await page.screenshot({path:path.join(output,'reconciliation-preview-1600.png'), fullPage:true});
    await page.getByRole('button', {name:'Confirmar comparación actual'}).click();
    await page.getByRole('button', {name:'Resolver', exact:true}).click();
    await page.locator('#rc-resolution [name=action]').selectOption('adjustment');
    assert.match(await page.locator('#rc-stock-label').innerText(), /ajuste de \+4 unidades/);
    await page.locator('#rc-resolution [name=note]').fill('Conteo sintético de verificación');
    await page.locator('#rc-resolution [type=submit]').click();
    await page.locator('#rc-dialog').waitFor({state:'hidden'});
    assert.match(await page.locator('#rc-lines').innerText(), /Solicitud de ajuste #\d+: pendiente de aprobación/);
    await page.screenshot({path:path.join(output,'reconciliation-resolved-1600.png'), fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(output,'reconciliation-mobile.png'), fullPage:true});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'mobile page overflow');
    await page.setViewportSize({width:1600,height:900});
    await page.goto(base + '/purchases?ref=1');
    await page.screenshot({path:path.join(output,'purchases-reference-1600.png'), fullPage:true});
    assert.deepEqual(errors, []);
    console.log('PASS: inventory creation access without approval; admin approval; CSV preview/confirm/adjustment request pending approval; mobile overflow; browser errors=0. Screenshots: tmp/approval-reconciliation-ui');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode=1;});
