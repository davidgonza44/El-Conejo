/* Real browser, only serve_reconciliation_scalability.py's disposable SQLite fixture. */
const {chromium} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  const page = await browser.newPage({viewport:{width:1600,height:900}});
  const base = 'http://127.0.0.1:5059', errors = [], payloads = [];
  const output = 'tmp/reconciliation-pagination-ui';
  fs.mkdirSync(output, {recursive:true});
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', async response => {
    if (/\/api\/reconciliation\/\d+(\?|$)/.test(response.url()) && response.ok()) {
      payloads.push(await response.json());
    }
  });
  try {
    const marker = await (await page.request.get(base + '/__reconciliation_fixture')).json();
    assert.equal(marker.synthetic_sqlite, true);
    await page.goto(base + '/__isolated_login/admin');
    await page.getByText('Conciliación manual independiente',{exact:true}).click();
    const csv = 'product_code;quantity\nTEST-01;14\n' + Array.from({length:120},(_,i)=>`SYN-${i};10\n`).join('');
    await page.locator('[name=file]').setInputFiles({name:'synthetic.csv',mimeType:'text/csv',buffer:Buffer.from(csv)});
    await page.locator('[name=cutoff_at]').fill('2026-09-30T12:00');
    await page.getByRole('button',{name:'Previsualizar',exact:true}).click();
    await page.locator('#rc-line-pagination').filter({hasText:'Página 1 de 3'}).waitFor();
    assert.equal(await page.locator('#rc-lines tr').count(),50);
    await page.locator('#rc-line-next').click();
    await page.locator('#rc-line-pagination').filter({hasText:'Página 2 de 3'}).waitFor();
    assert.match(await page.locator('#rc-lines tr').first().innerText(),/^52 ·/);
    await page.locator('#rc-line-next').click();
    await page.locator('#rc-line-pagination').filter({hasText:'Página 3 de 3'}).waitFor();
    assert.equal(await page.locator('#rc-lines tr').count(),21);
    assert.equal(await page.locator('#rc-line-next').isDisabled(),true);
    await page.locator('#rc-line-result').selectOption('discrepancy');
    await page.locator('#rc-line-pagination').filter({hasText:/^Página 1 de 1$/}).waitFor();
    assert.equal(await page.locator('#rc-line-range').innerText(),'Mostrando 1–1 de 1 líneas');
    assert.equal(await page.locator('#rc-lines tr').count(),1);
    assert.match(await page.locator('#rc-summary').innerText(),/Filas: 121/);
    await page.getByRole('button',{name:'Confirmar comparación actual'}).click();
    await page.getByRole('button',{name:'Revisar',exact:true}).click();
    await page.locator('[name=note]').fill('Revisión sintética');
    await page.locator('#rc-resolution [type=submit]').click();
    await page.locator('#rc-dialog').waitFor({state:'hidden'});
    await Promise.all([page.waitForResponse(r=>r.url().includes('review_status=reviewed')),
      page.locator('#rc-line-review').selectOption('reviewed')]);
    await page.getByRole('button',{name:'Resolver',exact:true}).click();
    await page.locator('[name=note]').fill('Reconocer sin ajuste sintético');
    await page.locator('#rc-resolution [type=submit]').click();
    await page.locator('#rc-dialog').waitFor({state:'hidden'});
    await page.getByText('No hay líneas que coincidan con estos filtros.').waitFor();
    await page.locator('#rc-line-review').selectOption('resolved');
    await page.getByText('Sin ajuste',{exact:false}).first().waitFor();
    await page.screenshot({path:output+'/detail-1600.png',fullPage:true});
    await page.locator('#rc-line-result').selectOption('all');
    await page.locator('#rc-line-review').selectOption('all');
    await page.locator('#rc-line-size').selectOption('25');
    await page.locator('#rc-line-pagination').filter({hasText:'Página 1 de 5'}).waitFor();
    assert.equal(await page.locator('#rc-lines tr').count(),25);
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:output+'/detail-390.png',fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    const failedPage = '**/api/reconciliation/*?page=2&per_page=25&**';
    await page.route(failedPage,route=>route.fulfill({status:503,contentType:'application/json',
      body:JSON.stringify({error:'Fallo de página sintético'})}));
    await page.locator('#rc-line-next').click();
    await page.locator('#rc-message').filter({hasText:'Fallo de página sintético'}).waitFor();
    assert.match(await page.locator('#rc-line-pagination').innerText(),/Página 1 de 5/);
    assert.equal(await page.locator('#rc-line-next').isDisabled(),false);
    await page.unroute(failedPage);
    await page.locator('#rc-line-next').click();
    await page.locator('#rc-line-pagination').filter({hasText:'Página 2 de 5'}).waitFor();
    assert.ok(payloads.length>=7 && payloads.every(x=>x.items.length<=100));
    for (const actor of ['inventario','vendedor']) {
      await page.goto(base+'/__isolated_login/'+actor);
      assert.equal((await page.request.get(base+'/api/reconciliation')).status(),403);
    }
    assert.deepEqual(errors,[]);
    console.log('PASS: paging, filters, counters, confirm/review/acknowledge, empty state, page error/retry, permissions, mobile overflow; bounded responses; browser errors=0.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
