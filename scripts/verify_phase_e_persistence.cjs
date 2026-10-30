/* Read-only business verification after restarting ONLY the Phase E server. */
const {chromium,expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const BASE='http://127.0.0.1:5066', OUTPUT='.tmp/phase-e-20261009';
(async()=>{
  const before=JSON.parse(fs.readFileSync(OUTPUT+'/browser-result.json','utf8'));
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:900}});
    await expect.poll(async()=>{
      try{return (await page.request.get(BASE+'/fase-e/identity')).status();}catch{return 0;}
    },{timeout:60000}).toBe(200);
    const identity=await (await page.request.get(BASE+'/fase-e/identity')).json();
    assert.equal(identity.simulation,true);
    assert.equal(identity.database,before.identity.database);
    await page.goto(BASE+'/fase-e');
    await page.getByRole('button',{name:'Entrar como administrador sintético'}).click();
    const after=await (await page.request.get(BASE+'/fase-e/state')).json();
    assert.deepEqual(after,before.final);
    const notifications=await (await page.request.get(BASE+'/api/shell/notifications')).json();
    const summary=notifications.items.find(item=>item.entity_type==='reconciliation_cut' && item.entity_id===after.cuts[0].id);
    assert.ok(summary);
    assert.match(summary.message,/2 discrepancias/);
    assert.equal(summary.href,'/reconciliation?detail=1');
    await page.goto(BASE+'/reconciliation?detail=1');
    await expect(page.locator('#phase-e-banner')).toContainText('SIMULACIÓN AISLADA');
    await expect(page.locator('#rc-lines tr')).toHaveCount(4);
    await expect(page.locator('#rc-lines tr').filter({hasText:'SIM-SOBRA'})).toContainText('Revisó: SIMULADO admin');
    await expect(page.locator('#rc-lines tr').filter({hasText:'SIM-FALTA'})).toContainText('Pendiente');
    await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:OUTPUT+'/13-persistencia-tras-reinicio-simulacion.png',fullPage:true});
    await page.goto(BASE+'/settings?tab=notifications');
    await expect(page.locator('#phase-e-banner')).toContainText('SIMULACIÓN AISLADA');
    await expect(page.locator('#st-recent-list')).toContainText('Corte de inventario 08:00 completado');
    await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:OUTPUT+'/14-notificacion-interna-simulacion.png',fullPage:true});
    fs.writeFileSync(OUTPUT+'/persistence-browser.json',JSON.stringify({simulation:true,persisted_unchanged:true,identity,summary},null,2));
    console.log('PASS: real server restart preserved cut, snapshot, comparison, review, pending difference and stock; internal notification available to authorized user.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
