// Run only against the disposable, no-database test_settings_reference.py harness.
const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const out = '.tmp/quote-layers-validation-20260909';
const baseUrl = process.env.QUOTE_PREVIEW_URL || 'http://127.0.0.1:5001';
(async () => {
  fs.mkdirSync(out,{recursive:true});
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const context = await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1});
  const writes=[],errors=[],results=[];
  await context.route('**/*',r => {
    if (!['GET','HEAD'].includes(r.request().method())) {writes.push(r.request().method());return r.abort();}
    return r.continue();
  });
  const p=await context.newPage();p.on('pageerror',e=>errors.push(e.message));
  const go=async(path='sales/quotes?ref=1')=>{const r=await p.goto(baseUrl+'/'+path,{waitUntil:'networkidle'});assert.equal(r.status(),200,path);await p.evaluate(()=>document.fonts.ready);};
  const shot=async name=>p.screenshot({path:out+'/'+name+'.png'});
  const triggers=['.sa-toolbar .is-primary','.sa-chip:nth-child(1)','.sa-chip:nth-child(2)','.sa-chip:nth-child(3)'];
  const ids=['drawer','dates','status','more'];
  const open=async i=>{await p.locator(triggers[i]).click();await expect(p.locator('#ql-'+ids[i])).toBeVisible();};
  const reset=async()=>{await open(3);await p.locator('#ql-reset').click();};
  const day=async (field,value)=>{await p.locator('#ql-'+field).click();await expect(p.locator('#ql-single-calendar')).toBeVisible();await p.locator(`#ql-single-calendar [data-ql-day="${value}"]`).click();await expect(p.locator('#ql-'+field)).toHaveAttribute('data-ql-date',value);await expect(p.locator('#ql-single-calendar')).toBeHidden();};
  await go();
  assert.deepEqual(await p.evaluate(()=>[innerWidth,innerHeight,devicePixelRatio,visualViewport.scale]),[1600,900,1,1]);
  const base=await shot('base');
  const originalRows=await p.locator('.sa-table tbody').innerHTML();
  await p.route('**/static/**/ix_quote_layers.*',r=>r.abort());await go();
  assert(base.equals(await shot('base-without-layer-assets')),'Layer assets do not change base pixels');
  await p.unroute('**/static/**/ix_quote_layers.*');await go();results.push('Base pixels unchanged by quote assets');
  for(let i=0;i<4;i++){await open(i);await shot(ids[i]);await p.keyboard.press('Escape');await expect(p.locator('#ql-'+ids[i])).toBeHidden();await expect(p.locator(triggers[i])).toBeFocused();}
  for(const i of [0,3]) for(const method of ['Cancelar','X','outside']){
    await open(i);const layer=p.locator('#ql-'+ids[i]);assert.equal(await p.evaluate(()=>document.body.style.overflow),'hidden');
    if(method==='Cancelar')await layer.getByRole('button',{name:'Cancelar',exact:true}).click();
    if(method==='X')await layer.getByRole('button',{name:/^Cerrar/}).click();
    if(method==='outside')await p.mouse.click(3,3);
    await expect(layer).toBeHidden();await expect(p.locator(triggers[i])).toBeFocused();assert.equal(await p.evaluate(()=>document.body.style.overflow),'');
  }
  for(const i of [1,2]){await open(i);await p.mouse.click(900,100);await expect(p.locator('#ql-'+ids[i])).toBeHidden();}
  await open(0);await expect(p.locator('#ql-total')).toHaveText('$ 800.00');await expect(p.locator('#ql-products tr')).toHaveCount(2);
  await p.locator('#ql-quote-date').click();await shot('quote-date-calendar');await p.locator('#ql-single-calendar [data-ql-day="2024-06-12"]').click();await expect(p.locator('#ql-quote-date')).toContainText('12/06/2024');
  await p.locator('#ql-customer').selectOption({index:1});await p.getByRole('button',{name:'Guardar cotización',exact:true}).click();await expect(p.locator('#ql-drawer')).toBeHidden();await expect(p.locator('#ql-notice')).toContainText('No se creó ninguna cotización real');
  await p.reload({waitUntil:'networkidle'});await open(0);await expect(p.locator('#ql-quote-date')).toContainText('03/06/2024');await expect(p.locator('#ql-total')).toHaveText('$ 800.00');
  for(let i=0;i<32;i++){await p.keyboard.press('Tab');assert(await p.evaluate(()=>!!document.activeElement.closest('#ql-drawer')),'Drawer focus stays inside');}await p.keyboard.press('Escape');results.push('Drawer/calendar selection/simulated save/F5/focus');
  await open(1);
  for(const [i,start,end] of [[0,'2024-05-31','2024-05-31'],[1,'2024-05-25','2024-05-31'],[2,'2024-05-02','2024-05-31'],[3,'2024-05-01','2024-05-31'],[4,'2024-04-01','2024-04-30']]){
    await p.locator(`[data-ql-quick="${i}"]`).click();await expect(p.locator(`#ql-calendars [data-ql-day="${start}"]`).first()).toHaveClass(/is-end/);await expect(p.locator(`#ql-calendars [data-ql-day="${end}"]`).first()).toHaveClass(/is-end/);
  }
  await p.locator('#ql-dates').getByRole('button',{name:'Cancelar',exact:true}).click();await expect(p.locator(triggers[1])).toContainText('Todas las fechas');
  await open(1);await p.locator('[data-ql-quick="0"]').click();await p.locator('#ql-date-apply').click();await expect(p.locator('.sa-pagination-summary')).toHaveText('Mostrando 1 a 1 de 1 cotizaciones');
  await open(1);await p.locator('[data-ql-quick="5"]').click();await p.locator('#ql-calendars [data-ql-day="2024-05-29"]').first().click();await expect(p.locator('#ql-date-apply')).toBeDisabled();await p.locator('#ql-calendars [data-ql-day="2024-05-31"]').first().click();await shot('custom-range');await p.locator('#ql-date-apply').click();
  await expect(p.locator('.sa-table tbody tr')).toHaveCount(5);results.push('All quick ranges/custom range/cancel/apply filter');
  await open(2);await expect(p.locator('#ql-status [role="option"]')).toHaveCount(6);await p.locator('[data-ql-status="2"]').click();await expect(p.locator('.sa-table tbody tr')).toHaveCount(2);
  await open(3);await p.locator('#ql-filter-client').selectOption('Ferretería San José');await p.locator('#ql-filter-seller').selectOption('María Pérez');await p.getByRole('button',{name:'Aplicar filtros',exact:true}).click();await expect(p.locator('.sa-table tbody tr')).toHaveCount(1);await expect(p.locator('.sa-table tbody')).toContainText('COT-000088');await expect(p.locator(triggers[1])).toContainText('29/05/2024');await expect(p.locator(triggers[2])).toContainText('Pendiente');await shot('combined-filters');results.push('Date/status/client/seller intersection');
  await open(3);await p.locator('#ql-filter-client').selectOption('Público en general');await p.locator('#ql-more').getByRole('button',{name:'Cancelar',exact:true}).click();await open(3);await expect(p.locator('#ql-filter-client')).toHaveValue('Ferretería San José');await p.locator('#ql-reset').click();
  await expect(p.locator('.sa-pagination-summary')).toHaveText('Mostrando 1 a 10 de 89 cotizaciones');assert.equal(await p.locator('.sa-table tbody').innerHTML(),originalRows);await expect(p.locator(triggers[1])).toContainText('Todas las fechas');await expect(p.locator(triggers[2])).toContainText('Todos los estados');
  await p.evaluate(()=>document.activeElement.blur());await p.mouse.move(1590,890);await shot('reset-base');
  await open(3);
  for(const field of ['filter-from','filter-to']){await p.locator('#ql-'+field).click();await shot(field+'-calendar');await p.locator('#ql-single-calendar [data-ql-month="-1"]').click();await p.locator('#ql-single-calendar [data-ql-day="2024-05-31"]').click();await expect(p.locator('#ql-'+field)).toContainText('31/05/2024');}
  for(const [key,value] of [['client','Constructora del Norte S.A.'],['product','Cemento Gris 42.5 kg'],['seller','Administrador'],['validity','30 días'],['payment','Pendiente']])await p.locator('#ql-filter-'+key).selectOption(value);
  await p.locator('#ql-filter-min').fill('52300');await p.locator('#ql-filter-max').fill('52300');await p.getByRole('button',{name:'Aplicar filtros',exact:true}).click();await expect(p.locator('.sa-table tbody tr')).toHaveCount(1);await expect(p.locator('.sa-table tbody')).toContainText('COT-000089');await reset();
  await open(3);await day('filter-from','2024-06-15');await day('filter-to','2024-06-01');await p.getByRole('button',{name:'Aplicar filtros',exact:true}).click();await expect(p.locator('#ql-filter-error')).not.toBeEmpty();await p.locator('#ql-reset').click();results.push('More dates day selection/all criteria intersection/cancel/reset/reversed range validation');
  for(const [i,name] of [[1,'Enviada'],[2,'Pendiente'],[3,'Aceptada'],[4,'Vencida'],[5,'Rechazada']]){await open(2);await p.locator(`[data-ql-status="${i}"]`).click();const statuses=await p.locator('.sa-table tbody .sa-badge').allTextContents();assert(statuses.length&&statuses.every(x=>x===name));await open(2);await expect(p.locator(`[data-ql-status="${i}"]`)).toHaveAttribute('aria-selected','true');await p.keyboard.press('Escape');}await reset();
  await p.locator('.sa-pages button').filter({hasText:/^9$/}).click();await expect(p.locator('.sa-table tbody tr')).toHaveCount(9);await expect(p.locator('.sa-pagination-summary')).toHaveText('Mostrando 81 a 89 de 89 cotizaciones');await reset();
  await open(1);await p.locator(triggers[2]).click();await expect(p.locator('#ql-dates')).toBeHidden();await p.locator(triggers[3]).click();await expect(p.locator('#ql-status')).toBeHidden();await p.evaluate(()=>document.querySelector('.sa-toolbar .is-primary').click());await expect(p.locator('#ql-more')).toBeHidden();await expect(p.locator('#ql-drawer')).toBeVisible();assert.equal(await p.locator('dialog[open]').count(),1);await p.keyboard.press('Escape');results.push('All states/active check/pagination/single layer/closures/focus/scroll');
  for(const route of ['sales/quotes','sales/new','sales/orders','sales/customers','sales','products','inventory','categories','suppliers']){await go(route+'?ref=1');await expect(p.locator('#ix-sidebar')).toContainText('Análisis predictivo');if(route!=='sales/quotes')await expect(p.locator('#ql-fixture')).toHaveCount(0);await shot('route-'+route.replaceAll('/','-'));}
  await go('sales/quotes');await expect(p.locator('#ql-fixture')).toHaveCount(0);await expect(p.locator('script[src*="ix_quote_layers"]')).toHaveCount(0);results.push('Nine reference routes 200/sidebar visible; normal quote layer exclusion; movements route /inventory');
  assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);results.push('No non-GET/HEAD requests; no page errors');
  fs.writeFileSync(out+'/results.json',JSON.stringify({viewport:[1600,900,1,1],results,writes,errors},null,2));console.log('PASS',JSON.stringify(results));await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
