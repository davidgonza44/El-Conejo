/* Actual browser + real Flask services. Hard-locked to the synthetic port 5066. */
const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const BASE = 'http://127.0.0.1:5066';
const OUTPUT = path.resolve('.tmp/phase-e-20261009');

async function main() {
  fs.mkdirSync(OUTPUT, {recursive:true});
  const browser = await chromium.launch({channel:'msedge', headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:900}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const capture = async name => {
    await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:path.join(OUTPUT,name+'.png'),fullPage:true});
  };
  const state = async () => {
    const response = await page.request.get(BASE+'/fase-e/state');
    assert.equal(response.status(),200);
    return response.json();
  };
  const action = async label => {
    await page.goto(BASE+'/fase-e');
    await page.getByRole('button',{name:label,exact:true}).click();
    await expect(page).toHaveURL(/\/fase-e(?:\?|$)/);
  };
  try {
    await expect.poll(async () => {
      try { return (await page.request.get(BASE+'/fase-e/identity')).status(); }
      catch { return 0; }
    }, {timeout:60000}).toBe(200);
    const identity = await (await page.request.get(BASE+'/fase-e/identity')).json();
    assert.equal(identity.simulation,true);
    assert.match(identity.database,/^reconciliation_test_[a-f0-9]{12}$/);
    await page.goto(BASE+'/fase-e');
    if (await page.getByRole('button',{name:'Entrar como administrador sintético'}).count()) {
      await page.getByRole('button',{name:'Entrar como administrador sintético'}).click();
    }
    await expect(page.locator('#fase-e-schedule')).toContainText('08:00 · 12:00 · 16:00 · 18:30');
    await capture('01-horarios-simulacion');
    const initial = await state();
    assert.equal(initial.cuts.length,0,'For pristine screenshots prepare a fresh scenario; never silently reset data.');
    await action('Ejecutar corte 08:00');
    const waiting = await state();
    const cutId = waiting.cuts[0].id;
    assert.equal(waiting.cuts[0].status,'waiting_source');
    assert.equal(waiting.cuts[0].snapshot_status,'captured');
    assert.deepEqual(waiting.cuts[0].snapshot.items.map(x=>x.quantity),[12,20,8]);
    await capture('02-corte-ejecutado-simulacion');
    await page.getByRole('link',{name:'Ver corte y fotografía',exact:true}).click();
    await expect(page.locator('#rc-detail-title')).toContainText('Pendiente de archivo');
    await expect(page.locator('#rc-snapshot-lines tr')).toHaveCount(3);
    await capture('03-snapshot-original-simulacion');
    await page.locator('#rc-detail').scrollIntoViewIfNeeded();
    await capture('04-espera-de-archivo-simulacion');
    await action('Registrar entrada posterior +3');
    const moved = await state();
    assert.equal(moved.stock['SIM-IGUAL'],15);
    assert.deepEqual(moved.cuts[0].snapshot,waiting.cuts[0].snapshot);
    await action('Publicar CSV sintético');
    await capture('05-csv-externo-sintetico');
    await action('Ejecutar conciliación');
    const compared = await state();
    const run = compared.runs[0];
    assert.equal(compared.cuts[0].status,'completed');
    assert.deepEqual([run.match_count,run.discrepancy_count,run.unmatched_count],[1,2,1]);
    assert.deepEqual(compared.stock,moved.stock);
    assert.deepEqual(compared.movements,moved.movements);
    await page.getByRole('link',{name:'Ver resultado y revisión',exact:true}).click();
    await expect(page.locator('#rc-lines tr')).toHaveCount(4);
    await expect(page.locator('#rc-summary')).toContainText('Coincidencias: 1 · Discrepancias: 2 · Sin identificar: 1');
    await capture('06-resultado-conciliacion-simulacion');
    await page.locator('#rc-line-result').selectOption('discrepancy');
    await expect(page.locator('#rc-lines tr')).toHaveCount(2);
    await capture('07-diferencias-pendientes-simulacion');
    const extra = page.locator('#rc-lines tr').filter({hasText:'SIM-SOBRA'});
    await extra.getByRole('button',{name:'Revisar',exact:true}).click();
    await page.locator('#rc-resolution [name=note]').fill('SIMULADO: revisar exportación externa con el responsable; sin ajuste de stock.');
    await page.locator('#rc-dialog').getByRole('button',{name:'Guardar',exact:true}).click();
    await expect(page.locator('#rc-dialog')).not.toBeVisible();
    await expect(extra).toContainText('Revisó: SIMULADO admin');
    await expect(page.locator('#rc-lines tr').filter({hasText:'SIM-FALTA'})).toContainText('Pendiente');
    await capture('08-revision-responsable-y-pendiente-simulacion');
    await page.locator('#rc-line-result').selectOption('all');
    await expect(page.locator('#rc-lines tr')).toHaveCount(4);
    await page.getByText('Identificador técnico',{exact:true}).click();
    await capture('09-historial-trazabilidad-simulacion');
    await page.getByRole('button',{name:'Ver fotografía original',exact:true}).click();
    await expect(page.locator('#rc-detail-title')).toContainText('Completado');
    await expect(page.locator('#rc-snapshot-lines tr').first()).toContainText('12');
    await capture('10-snapshot-conservado-tras-conciliar');
    await action('Verificar integridad');
    await expect(page.getByRole('status')).toContainText('Integridad verificada');
    await capture('11-stock-y-movimientos-intactos-simulacion');
    const final = await state();
    for (const label of ['Ejecutar corte 08:00','Registrar entrada posterior +3','Publicar CSV sintético','Ejecutar conciliación']) await action(label);
    assert.deepEqual(await state(),final,'Repetition must preserve cut, snapshot, movement and run IDs.');
    fs.writeFileSync(path.join(OUTPUT,'browser-result.json'),JSON.stringify({simulation:true,identity,
      initial,waiting,moved,compared,final,replay_unchanged:true,page_errors:errors},null,2));
    await page.goto(BASE+'/reconciliation?detail='+run.id);
    await expect(page.locator('#rc-lines tr')).toHaveCount(4);
    for (const viewport of [{width:768,height:1024},{width:390,height:844}]) {
      await page.setViewportSize(viewport);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Page overflow');
      await capture('12-responsive-'+viewport.width+'-simulacion');
    }
    assert.deepEqual(errors,[]);
    console.log('PASS: real browser, complete synthetic MariaDB flow, review and pending difference, immutable snapshot, replay, desktop/tablet/mobile; page errors=0.');
  } finally { await browser.close(); }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
