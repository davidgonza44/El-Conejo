// Run after scripts/test_settings_reference.py --serve (synthetic users, no database).
const { chromium } = require('@playwright/test');
const fs = require('fs');
const assert = require('assert/strict');
const path = require('path');
const output = path.join('.tmp', 'settings-validation');
fs.mkdirSync(output, {recursive:true});
const stage = process.env.SETTINGS_STAGE || 'final';
const onlyTab = process.env.SETTINGS_TAB;
const settingsTabs = ['general','predictive','notifications','users','security','backup'];
const base = `http://127.0.0.1:${process.env.SETTINGS_PREVIEW_PORT || '5019'}`;
async function verifyFixtureContent(page, tab) {
    if(tab === 'backup') {
      assert.equal(await page.locator('input[name=backup_type]').count(),3);
      assert.equal(await page.locator('input[name=backup_type]:checked').inputValue(),'full');
      for(const content of ['Todo correcto','01/06/2024 02:00 AM','245.8 MB','3.2 GB de 50 GB (6.4%)','/backups/ferreteria_pro','Google Drive','Consejos y recomendaciones']) assert(await page.getByText(content,{exact:false}).count(),content);
    }
    if(tab === 'users') {
      assert.equal(await page.locator('[data-user-row]').count(),7);
      assert.equal(await page.locator('[data-role-permission]:checked').count(),5);
      for(const content of ['Isabel Vivas','José Rivero','Paola Figueroa','Eduardo Blanco','ID: U-0001','30/05/2024 08:15 AM']) assert(await page.getByText(content,{exact:false}).count(),content);
    }
    if(tab === 'security') {
      assert.equal(await page.locator('.st-security-sessions tbody tr').count(),4);
      assert.equal(await page.locator('.st-security-history tbody tr').count(),5);
      for(const content of ['2FA activado','10 códigos disponibles','No repetir últimas 5','No hay dispositivos confiables','201.234.12.10']) assert(await page.getByText(content,{exact:false}).count(),content);
    }
}

async function verifyPreviewInteractions(page, tab) {
    for(const button of await page.locator('[data-preview-action]').all()) await button.click();
    assert(await page.getByRole('status').isVisible());
    assert.match(await page.getByRole('status').innerText(),/no guarda datos/);
    if(tab === 'backup') {
      await page.getByRole('radio',{name:'Solo base de datos',exact:false}).check();
      assert.equal(await page.locator('input[name=backup_type]:checked').inputValue(),'database');
      await page.reload();
      assert.equal(await page.locator('input[name=backup_type]:checked').inputValue(),'full');
    }
    const toggle = page.locator('[role=switch]').first();
    if(await toggle.count()) { const checked = await toggle.isChecked(); await toggle.click(); assert.equal(await toggle.isChecked(),!checked); }
    if(tab === 'users') {
      await page.getByRole('textbox',{name:'Buscar usuario',exact:true}).fill('Isabel');
      assert.equal(await page.locator('[data-user-row]:visible').count(),1);
      await page.getByRole('textbox',{name:'Buscar usuario',exact:true}).fill('');
      await page.getByRole('combobox',{name:'Filtrar por rol'}).selectOption('Inventario');
      assert.equal(await page.locator('[data-user-row]:visible').count(),2);
      await page.locator('[data-permissions-all]').uncheck();
      assert.equal(await page.locator('[data-role-permission]:checked').count(),0);
      await page.reload();
      assert.equal(await page.locator('[data-role-permission]:checked').count(),5);
    }
}

async function measureGeometry(page) {
  return page.evaluate(()=>{
      const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};
      const box=s=>rect(document.querySelector(s));
      const textRect=s=>{const range=document.createRange();range.selectNodeContents(document.querySelector(s));return rect(range)};
      return {sidebar:box('.ix-sidebar'),content:box('.st-tabs'),title:box('h1'),titleText:textRect('h1'),breadcrumb:box('.st-breadcrumb'),search:box('.st-search'),bell:box('.st-bell'),actions:[...document.querySelectorAll('.st-actions button')].map(rect),tabs:box('.st-tabs'),cards:[...document.querySelectorAll('.st-card')].map(e=>({title:e.querySelector('h2').textContent,...rect(e)})),infoBar:document.querySelector('.st-form>.st-info') ? box('.st-form>.st-info') : null,pageBottom:box('.st-page').bottom,scrollHeight:document.documentElement.scrollHeight,scrollWidth:document.documentElement.scrollWidth,dpr:devicePixelRatio,zoom:visualViewport.scale};
    });
}

(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.SETTINGS_BROWSER_CHANNEL ? {channel:process.env.SETTINGS_BROWSER_CHANNEL} : {})});
  const context = await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1});
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror',error=>pageErrors.push({url:page.url(),message:error.message}));
  page.on('console',message=>{if(message.type()==='error') pageErrors.push({url:page.url(),message:message.text()});});
  const checks = [];
  const mutations = [];
  page.on('request',r=>{if(!['GET','HEAD','OPTIONS'].includes(r.method())) mutations.push(r.method()+' '+new URL(r.url()).pathname)});
  for (const tab of (onlyTab ? [onlyTab] : settingsTabs)) {
    const response = await page.goto(`${base}/settings?tab=${tab}&ref=1`,{waitUntil:'networkidle'});
    assert.equal(response.status(),200);
    await page.evaluate(()=>document.fonts.ready);
    assert.equal(await page.locator('.st-tabs [aria-current="page"]').count(),1);
    assert.match(await page.locator('.st-tabs [aria-current="page"]').getAttribute('href'),new RegExp('tab='+tab+'(?:&|$)'));
    await verifyFixtureContent(page, tab);
    assert.equal(await page.locator('.ix-nav > ul > li > a[aria-current="page"]').innerText(),'Configuración');
    assert(await page.locator('button[aria-controls=ix-nav-pred]').isVisible());
    assert.equal(await page.locator('.st-card').count(),['notifications','users'].includes(tab) ? 4 : 6);
    await page.screenshot({path:path.join(output,`${tab}-${stage}.png`)});
    const geometry=await measureGeometry(page);
    assert.equal(geometry.scrollHeight,900); assert.equal(geometry.scrollWidth,1600);
    assert.equal(geometry.dpr,1); assert.equal(geometry.zoom,1);
    assert.equal(geometry.cards[0].y,tab === 'backup' ? 205 : tab === 'users' ? 201 : tab === 'security' ? 202 : tab === 'notifications' ? 198 : 197);
    fs.writeFileSync(path.join(output,`${tab}-${stage}-geometry.json`),JSON.stringify(geometry,null,2));
    await verifyPreviewInteractions(page, tab);
    checks.push({screen:tab,status:'PASS',cards:['notifications','users'].includes(tab) ? 4 : 6,scrollHeight:geometry.scrollHeight});
  }
  if (onlyTab) { assert.deepEqual(mutations,[]); assert.deepEqual(pageErrors,[]); console.log(JSON.stringify({stage,checks,mutations,pageErrors})); await browser.close(); return; }
  await page.goto(`${base}/settings?tab=notifications&ref=1`);
  assert.equal(await page.locator('.st-channel-row').count(),4);
  assert.equal(await page.locator('.st-notification-matrix tbody tr').count(),6);
  assert.equal(await page.locator('.st-recent-row').count(),4);
  assert.equal(await page.locator('.st-notification-switch:checked').count(),18);
  assert.equal(await page.locator('input[type=email]').inputValue(),'notificaciones@ferreteriapro.com');
  assert.deepEqual(mutations,[]);
  await page.goto(`${base}/settings?tab=general&ref=1`);
  await page.locator('.st-tabs').getByRole('link',{name:'Análisis predictivo',exact:true}).click();
  assert.match(page.url(),/tab=predictive/); assert.match(page.url(),/ref=1/);
  // Live tabs (no ?ref=1) need a database: covered by scripts/verify_settings_live.cjs against the E2E harness.
  for (const tab of ['integrations']) {
    await page.goto(`${base}/settings?tab=${tab}&ref=1`);
    assert(await page.locator('.st-unavailable').isVisible());
  }
  for (const width of [390,768,1024]) {
    await page.setViewportSize({width,height:900});
    for(const tab of settingsTabs) {
      await page.goto(`${base}/settings?tab=${tab}&ref=1`,{waitUntil:'networkidle'});
      const dimensions=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth}));
      assert.equal(dimensions.scrollWidth,width,`${tab} horizontal overflow at ${width}`);
      const menu=page.getByRole('button',{name:'Menú',exact:true});assert(await menu.isVisible());
      await menu.click();assert.equal(await menu.getAttribute('aria-expanded'),'true');
      await menu.click();
      await page.screenshot({path:path.join(output,`${tab}-${width}.png`)});
      checks.push({screen:tab,width,status:'PASS'});
    }
  }
  await page.setViewportSize({width:1600,height:900});
  const routes=['/dashboard','/inventory/summary','/products','/categories','/suppliers','/inventory',
    '/sales','/sales/list','/sales/new','/sales/quotes','/sales/orders','/sales/customers','/sales/reports',
    '/sales/VTA-000156','/sales/VTA-000156/print','/reports','/predictive/demand',
    '/predictive/replenishment','/predictive/critical','/predictive/trends','/predictive/accuracy'];
  for(const route of routes) {
    const response=await page.goto(base+route+'?ref=1',{waitUntil:'networkidle'});
    assert.equal(response.status(),200,route);
    assert(await page.locator('h1').count(),route+' heading');
    await page.evaluate(()=>document.fonts.ready);
    await page.screenshot({path:path.join(output,'regression-'+route.slice(1).replaceAll('/','-')+'.png')});
    checks.push({route,status:response.status(),title:await page.locator('h1').first().innerText()});
  }
  for(const [tab,reference] of [['general','01_general.png'],['predictive','02_analisis_predictivo.png'],['notifications','03_notificaciones.png'],['users','04_usuarios_y_roles.png'],['security','05_seguridad.png'],['backup','06_respaldo.png']]) {
    const data=file=>'data:image/png;base64,'+fs.readFileSync(file).toString('base64');
    await page.setViewportSize({width:3200,height:900});
    await page.setContent(`<html><body style="margin:0;display:flex"><img alt="Approved reference" src="${data('references/05_configuracion/'+reference)}"><img alt="Final render" src="${data(path.join(output,tab+'-final.png'))}"></body></html>`);
    await page.locator('img').evaluateAll(images=>Promise.all(images.map(i=>i.decode())));
    await page.screenshot({path:path.join(output,tab+'-side-by-side.png')});
  }
  assert.deepEqual(pageErrors,[]);
  assert.deepEqual(mutations,[]);
  fs.writeFileSync(path.join(output,'checks.json'),JSON.stringify({checks,mutations,pageErrors},null,2));
  console.log(JSON.stringify({checks:checks.length,mutationRequests:mutations.length,pageErrors:pageErrors.length,status:'PASS'}));
  await browser.close();
})().catch(error=>{console.error(error);process.exit(1)});

