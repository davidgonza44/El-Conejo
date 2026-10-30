/* Optional existing-Playwright browser check. Reads only .env.demo, never prints credentials. */
const { chromium } = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const values = Object.fromEntries(fs.readFileSync('.env.demo', 'utf8').split(/\r?\n/)
  .filter(line => /^[A-Z_]+=.*/.test(line)).map(line => {
    const split = line.indexOf('=');
    return [line.slice(0, split), line.slice(split + 1)];
  }));
assert.equal(values.DOCKER_ENV_KIND, 'demo');
assert.equal(values.DEMO_MODE, 'true');
assert.equal(values.DOCKER_APP_ENV, 'demo');
assert.equal(values.APP_PORT, '5001');
assert.match(values.DB_NAME, /^conejo_demo_[a-z0-9_]{8,32}$/);
const origin = 'http://localhost:5001';
const output = path.resolve('.tmp/demo-v2-ui');
const targets = [
  ['/dashboard', 'dashboard'], ['/products', 'products'], ['/reports/sales', 'reports'],
  ['/purchases', 'purchases'], ['/purchases/receptions', 'receptions'],
  ['/predictive/demand', 'demand'], ['/predictive/replenishment', 'replenishment'],
  ['/predictive/critical', 'critical'], ['/predictive/trends', 'trends'],
  ['/predictive/accuracy', 'accuracy'], ['/historical-imports', 'history'],
  ['/reconciliation', 'reconciliation'],
];

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'es-VE' });
    const login = await context.request.post(origin + '/api/auth/login', {
      data: { identifier: values.ADMIN_USERNAME, password: values.ADMIN_PASSWORD },
    });
    assert.equal(login.status(), 200, 'DEMO login');
    delete values.ADMIN_PASSWORD;
    fs.mkdirSync(output, { recursive: true });
    const page = await context.newPage();
    const failures = [];
    page.on('pageerror', error => failures.push(error.name + ': ' + error.message));
    for (const [route, name] of targets) {
      const response = await page.goto(origin + route, { waitUntil: 'networkidle', timeout: 180000 });
      assert.equal(response.status(), 200, name);
      assert.ok(!page.url().includes('/login'), 'authenticated ' + name);
      if (name === 'reconciliation') {
        await page.getByRole('button', { name: 'Ver CONC-000001', exact: true }).click();
        await page.getByText('DEMO-v2-conteo.csv', { exact: false }).waitFor();
      }
      await page.screenshot({ path: path.join(output, name + '.png'), fullPage: true });
      const body = await page.locator('body').innerText();
      assert.ok(body.includes('DEMO'), 'synthetic content visible ' + name);
      console.log('PASS browser ' + name);
    }
    assert.deepEqual(failures, [], 'browser JavaScript errors');
    console.log('PASS: 12 authenticated pages, visible DEMO content, no JavaScript exceptions.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.name + ': ' + error.message); process.exitCode = 1; });
