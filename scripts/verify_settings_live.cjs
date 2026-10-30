// Run after python -B scripts/serve_settings_e2e.py (ferreteria_conejo_e2e only). Live tabs + ?ref=1 tabs.
const { chromium } = require('@playwright/test');
const fs = require('fs');
const assert = require('assert/strict');
const path = require('path');
const output = path.join('.tmp', 'settings-validation');
fs.mkdirSync(output, { recursive: true });
const base = `http://127.0.0.1:${process.env.SETTINGS_E2E_PORT || '5026'}`;
const liveTabs = ['general', 'notifications', 'users', 'security', 'backup'];
const allTabs = ['general', 'predictive', 'notifications', 'users', 'security', 'integrations', 'backup'];
const labels = { general: 'General', predictive: 'Análisis predictivo', notifications: 'Notificaciones', users: 'Usuarios y roles', security: 'Seguridad', integrations: 'Integraciones', backup: 'Respaldo' };
const fixtureMarkers = ['isabel.vivas@ferreteriapro.com', '190.12.45.67', '/backups/ferreteria_pro', 'notificaciones@ferreteriapro.com', 'Ferretería Pro C.A.', 'Prophet (Meta)'];
const viewports = [[1600, 900], [1366, 768], [1280, 720]];

const overflow = (page) => page.evaluate(() => ({
  scrollWidth: document.documentElement.scrollWidth, innerWidth: innerWidth,
  clipped: [...document.querySelectorAll('.st-card, .st-users-grid, .st-backup-table, .st-user-row')]
    .filter((e) => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX === 'visible').map((e) => e.className),
}));

(async () => {
  const ctx = await (await fetch(`${base}/__settings_e2e__/context`)).json();
  assert.equal(ctx.database, 'ferreteria_conejo_e2e');
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
  const login = await context.request.post(`${base}/api/auth/login`, { data: { identifier: ctx.username, password: ctx.password } });
  assert.equal(login.status(), 200);
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push({ url: page.url(), message: e.message }));
  // Missing product thumbnails of pre-existing E2E rows (/media/products/*) are seed data, not settings behaviour.
  const mediaMiss = (url) => /\/media\//.test(url || '');
  page.on('console', (m) => { if (m.type() === 'error' && !mediaMiss(m.location()?.url)) pageErrors.push({ url: page.url(), message: m.text() }); });
  page.on('response', (r) => { if (r.status() >= 400 && !mediaMiss(r.url())) pageErrors.push({ url: page.url(), message: `${r.status()} ${new URL(r.url()).pathname}` }); });
  const checks = [];
  const note = (name, ok, detail = '') => { checks.push({ name, ok, detail }); assert(ok, `${name} ${detail}`); };

  // ---- live tabs at 1600x900 ----
  for (const tab of liveTabs) {
    const response = await page.goto(`${base}/settings?tab=${tab}`, { waitUntil: 'networkidle' });
    note(`live ${tab} 200`, response.status() === 200);
    note(`live ${tab} pro shell`, (await page.locator('.ix-brand-name').innerText()).trim() === 'Ferretería Pro');
    note(`live ${tab} search + bell`, await page.locator('#ix-search-input').isVisible() && await page.locator('#ix-bell').isVisible());
    note(`live ${tab} Configuración active`, /Configuración/.test(await page.locator('.ix-nav [aria-current=page]').first().innerText()));
    note(`live ${tab} tab active`, (await page.locator('.st-tabs .is-active').innerText()).trim() === labels[tab]);
    note(`live ${tab} current user`, (await page.locator('.ix-user-name').innerText()).includes('Operador E2E'));
    const html = await page.content();
    note(`live ${tab} no fixture`, !fixtureMarkers.some((m) => html.includes(m)) && !html.includes('<span class="ix-brand-name">El Conejo C.A.</span>'));
    const geo = await overflow(page);
    note(`live ${tab} no horizontal overflow 1600`, geo.scrollWidth <= geo.innerWidth && geo.clipped.length === 0, JSON.stringify(geo));
    await page.screenshot({ path: path.join(output, `settings-${tab}-live.png`), fullPage: false });
  }

  // ---- real behaviours through the UI ----
  await page.goto(`${base}/settings?tab=general`, { waitUntil: 'networkidle' });
  const nameInput = page.locator('input[name="company.name"]');
  const original = await nameInput.inputValue();
  await nameInput.fill('Ferretería E2E Playwright C.A.');
  await page.locator('#st-save').click();
  await page.locator('.st-toast:not([hidden])').waitFor();
  note('general save toast', /guardad/i.test(await page.locator('.st-toast').innerText()));
  await page.reload({ waitUntil: 'networkidle' });
  note('general company name persisted', (await nameInput.inputValue()) === 'Ferretería E2E Playwright C.A.');
  await nameInput.fill(original); await page.locator('#st-save').click(); await page.locator('.st-toast:not([hidden])').waitFor();

  await page.locator('#ix-bell').click();
  await page.locator('#ix-bell-panel:not([hidden])').waitFor();
  note('bell shows real alerts', (await page.locator('#ix-bell-list li, #ix-bell-list a').count()) > 0);
  await page.screenshot({ path: path.join(output, 'settings-bell-live.png') });

  await page.goto(`${base}/settings?tab=users`, { waitUntil: 'networkidle' });
  const userRows = '#st-user-rows [data-user-id]';
  await page.locator(userRows).first().waitFor();
  const rows = await page.locator(userRows).count();
  note('users list from /api/users (paginated)', rows > 0 && rows <= 7, String(rows));
  note('users pagination enabled', !(await page.locator('#st-user-next').isDisabled()));
  await page.locator('#st-user-search').fill('extremadamente.largo');
  await page.waitForTimeout(300);
  note('users search finds long user', (await page.locator(userRows).count()) === 1);
  await page.locator(userRows).first().click();
  note('user card shows selection', (await page.locator('#st-user-email').inputValue()).includes('extremadamente.largo'));
  const usersGeo = await overflow(page);
  note('users long name no overflow', usersGeo.scrollWidth <= usersGeo.innerWidth && usersGeo.clipped.length === 0, JSON.stringify(usersGeo));
  await page.screenshot({ path: path.join(output, 'settings-users-long-live.png') });
  await page.locator('#st-user-search').fill('');
  await page.locator('#st-user-role-filter').selectOption('admin');
  await page.waitForTimeout(300);
  note('users role filter', (await page.locator(userRows).count()) >= 1);
  note('no delete control for users', (await page.getByRole('button', { name: /eliminar/i }).count()) === 0);

  await page.goto(`${base}/settings?tab=backup`, { waitUntil: 'networkidle' });
  await page.locator('#st-backup-history-toggle').click();
  note('backup history real rows', (await page.locator('#st-backup-history-body tr').count()) >= 1);
  note('backup history names e2e db only', (await page.locator('#st-backup-history-body').innerText()).includes('ferreteria_conejo_e2e_'));
  await page.screenshot({ path: path.join(output, 'settings-backup-history-live.png') });
  // La restauración ahora es real y protegida (RESTAURAR tipeado + respaldo previo): sin selección solo avisa, no restaura.
  await page.locator('#st-restore-button').click();
  await page.locator('.st-toast:not([hidden])', { hasText: /Seleccione un respaldo/ }).waitFor({ timeout: 8000 });
  note('restore requires selecting a backup first', !(await page.locator('#st-restore-dialog').getAttribute('open')));

  // ---- overflow at smaller viewports ----
  for (const [width, height] of viewports.slice(1)) {
    await page.setViewportSize({ width, height });
    for (const tab of liveTabs) {
      await page.goto(`${base}/settings?tab=${tab}`, { waitUntil: 'networkidle' });
      const geo = await overflow(page);
      note(`live ${tab} no horizontal overflow ${width}`, geo.scrollWidth <= geo.innerWidth && geo.clipped.length === 0, JSON.stringify(geo));
    }
  }
  await page.setViewportSize({ width: 1600, height: 900 });

  // ---- reference tabs: deterministic fixture, zero mutations ----
  const mutations = [];
  page.on('request', (r) => { if (!['GET', 'HEAD', 'OPTIONS'].includes(r.method())) mutations.push(r.method() + ' ' + new URL(r.url()).pathname); });
  for (const tab of allTabs) {
    const response = await page.goto(`${base}/settings?tab=${tab}&ref=1`, { waitUntil: 'networkidle' });
    note(`ref ${tab} 200`, response.status() === 200);
    note(`ref ${tab} tab active keeps ref`, (await page.locator('.st-tabs .is-active').getAttribute('href')).includes('ref=1'));
    for (const button of await page.locator('[data-preview-action]').all()) await button.click();
  }
  note('ref users fixture visible', (await page.goto(`${base}/settings?tab=users&ref=1`)) && (await page.content()).includes('isabel.vivas@ferreteriapro.com'));
  note('ref zero mutation requests', mutations.length === 0, JSON.stringify(mutations));
  note('no page errors', pageErrors.length === 0, JSON.stringify(pageErrors));
  fs.writeFileSync(path.join(output, 'live-results.json'), JSON.stringify({ checks, mutations, pageErrors }, null, 2));
  console.log(JSON.stringify({ checks: checks.length, mutations: mutations.length, pageErrors: pageErrors.length, status: 'PASS' }));
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
