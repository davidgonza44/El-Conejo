/* Run against serve_dashboard_purchase_e2e.py: existing disposable schema, no migrations. */
const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const origin = 'http://127.0.0.1:51439';
const output = '.tmp/dashboard-purchase-validation';

(async () => {
  const browser = await chromium.launch({channel:'msedge'});
  const context = await browser.newContext({viewport:{width:1600,height:900}, deviceScaleFactor:1, locale:'es-VE'});
  const results = {overflow:{}, screenshots:[]};
  const state = async () => {
    const response = await context.request.get(origin + '/__dashboard_purchase_e2e__/state');
    assert.equal(response.status(), 200);
    const data = await response.json();
    assert.equal(data.database, 'ferreteria_conejo_e2e');
    return data;
  };
  try {
    await context.route(/https:\/\/(cdn.jsdelivr.net|fonts.googleapis.com|fonts.gstatic.com)\//, route => {
      const url = new URL(route.request().url());
      const name = url.hostname === 'fonts.googleapis.com' ? 'inter.css' : path.basename(url.pathname);
      const local = path.join('.tmp/sales-reports-validation', name);
      if (!/\.(css|js|woff2?|ttf)$/.test(name) || !fs.existsSync(local)) return route.abort();
      return route.fulfill({path:local, contentType:name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : 'font/woff2'});
    });
    const page = await context.newPage();
    const errors = [], mutations = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (new URL(request.url()).pathname.startsWith('/api/') && !['GET','HEAD','OPTIONS'].includes(request.method())) mutations.push(request.url());
    });
    fs.mkdirSync(output, {recursive:true});
    const go = async url => {
      await page.goto(origin + url, {waitUntil:'networkidle'});
      await page.evaluate(() => document.fonts.ready);
    };
    const shot = async name => {
      const file = path.join(output, name + '.png');
      await page.screenshot({path:file});
      results.screenshots.push(file);
    };
    const shortcut = page.locator('[data-action=new_purchase]');
    const openFromDashboard = async reference => {
      await go('/dashboard' + (reference ? '?ref=1' : ''));
      await expect(shortcut).not.toHaveClass(/is-disabled/);
      await expect(shortcut).toContainText('Crear una nueva orden de compra');
      await expect(page.locator('body')).not.toContainText('Módulo de compras no disponible');
      await shortcut.click();
      await expect(page.locator('#po-order-form')).toBeVisible();
      await expect(page.locator('#po-dialog-title')).toHaveText('Nueva orden de compra');
      assert.equal(new URL(page.url()).pathname, '/purchases');
      assert.equal(new URL(page.url()).searchParams.get('new'), '1');
      assert.equal(new URL(page.url()).searchParams.get('ref'), reference ? '1' : null);
    };
    await go('/__dashboard_purchase_e2e__/login/admin');
    const initial = await state();
    assert.equal(initial.dynamic_action.enabled, true);
    assert.equal(initial.dynamic_action.href, '/purchases?new=1');
    assert.equal(initial.denied_action.enabled, false);

    // Reference: opening, selecting and submitting all stay in preview, including server defense.
    const referenceBefore = await state(), mutationBefore = mutations.length;
    await openFromDashboard(true);
    const options = await (await context.request.get(origin + '/api/purchases/options?ref=1')).json();
    await page.locator('[name=supplier_id]').last().selectOption(String(options.suppliers[0].id));
    await page.locator('#po-product-results button').first().click();
    await page.locator('#po-order-form [type=submit]').click();
    await expect(page.locator('#po-dialog')).not.toBeVisible();
    for (const [url, headers] of [
      ['/api/purchases?ref=1', {}],
      ['/api/purchases', {Referer:origin + '/purchases?new=1&ref=1'}],
    ]) {
      assert.equal((await context.request.post(origin + url, {data:{}, headers})).status(), 409);
    }
    const referenceAfter = await state();
    assert.equal(referenceAfter.writes, referenceBefore.writes);
    assert.equal(referenceAfter.orders, referenceBefore.orders);
    assert.equal(mutations.length, mutationBefore);
    results.referenceWrites = 0;

    // Live selectors and cancel cannot create an order; explicit save uses the existing API.
    await openFromDashboard(false);
    await page.locator('#po-order-form [name=supplier_id]').selectOption(String(initial.supplier_ids[0]));
    await page.locator('#po-product-search').fill('DPO-' + initial.run);
    await page.locator(`#po-product-results [data-product="${initial.product_ids[0]}"]`).click();
    assert.equal((await state()).orders, initial.orders);
    await page.locator('#po-order-form [data-close]').click();
    await expect(page.locator('#po-dialog')).not.toBeVisible();
    assert.equal((await state()).orders, initial.orders);
    results.cancelWithoutCreation = 'PASS';
    await openFromDashboard(false);
    await page.locator('#po-order-form [name=supplier_id]').selectOption(String(initial.supplier_ids[0]));
    await page.locator('#po-product-search').fill('DPO-' + initial.run);
    await page.locator(`#po-product-results [data-product="${initial.product_ids[0]}"]`).click();
    await shot('existing-new-purchase-flow');
    await page.locator('#po-order-form [type=submit]').click();
    await expect(page.locator('#po-dialog-title')).toContainText('Orden de compra OC-');
    const saved = await state();
    assert.equal(saved.orders, initial.orders + 1);
    assert.equal(saved.stock, initial.stock);
    assert.equal(saved.movements, initial.movements);
    results.orderChangesStock = false;
    results.liveFlow = 'PASS';

    // Persisted custom role, unrelated to admin: write succeeds, then revocation blocks UI/API.
    await go('/__dashboard_purchase_e2e__/login/reader');
    await expect(page.locator('#po-order-form')).toBeVisible();
    const payload = {supplier_id:initial.supplier_ids[0], items:[{product_id:initial.product_ids[0], quantity:1, unit_cost:'25.00'}]};
    assert.equal((await context.request.post(origin + '/api/purchases', {data:payload})).status(), 201);
    assert.equal((await context.request.post(origin + '/__dashboard_purchase_e2e__/reader-only')).status(), 200);
    await go('/purchases?new=1');
    await expect(page.locator('#po-dialog')).not.toBeVisible();
    assert.equal((await state()).dynamic_action.enabled, false);
    assert.equal((await context.request.post(origin + '/api/purchases', {data:payload})).status(), 403);
    results.dynamicRBAC = 'PASS';
    results.unauthorized = 'PASS';

    await go('/__dashboard_purchase_e2e__/login/denied');
    for (const url of ['/dashboard', '/dashboard?ref=1']) {
      await go(url);
      await expect(shortcut).toHaveAttribute('aria-disabled', 'true');
      await expect(shortcut).toHaveClass(/is-disabled/);
      assert.equal(await shortcut.getAttribute('href'), null);
    }
    assert.equal((await context.request.post(origin + '/api/purchases', {data:payload})).status(), 403);
    results.unauthorizedDashboard = 'PASS';

    await go('/__dashboard_purchase_e2e__/login/admin');
    for (const [width,height] of [[1600,900],[1366,768],[1280,720]]) {
      await page.setViewportSize({width,height});
      for (const reference of [false,true]) {
        await go('/dashboard' + (reference ? '?ref=1' : ''));
        const geometry = await page.evaluate(() => {
          const action = document.querySelector('[data-action=new_purchase]');
          const card = document.querySelector('#card-actions');
          const boxes = () => [...document.querySelectorAll('.ix-actions .ix-action')].map(node => {
            const r = node.getBoundingClientRect();
            return {x:r.x,y:r.y,width:r.width,height:r.height};
          });
          const enabled = boxes(), opacity = getComputedStyle(action).opacity;
          const oldDescription = action.querySelector('.ix-row-desc').textContent;
          action.classList.add('is-disabled');
          action.querySelector('.ix-row-desc').textContent = 'Módulo de compras no disponible';
          const disabled = boxes();
          action.classList.remove('is-disabled');
          action.querySelector('.ix-row-desc').textContent = oldDescription;
          const r = card.getBoundingClientRect();
          return {horizontal:document.documentElement.scrollWidth > innerWidth,
            vertical:card.scrollHeight > card.clientHeight + 1 || enabled.some(b => b.y + b.height > r.bottom),
            enabled, disabled, opacity};
        });
        assert.equal(geometry.horizontal, false);
        assert.equal(geometry.vertical, false);
        assert.equal(geometry.opacity, '1');
        assert.deepEqual(geometry.enabled, geometry.disabled, 'No geometry shift from enabling existing row');
        results.overflow[`${width}-${reference ? 'reference' : 'live'}`] = 'PASS';
        await shot(`dashboard-${reference ? 'reference' : 'live'}-${width}`);
      }
    }
    assert.deepEqual(errors, []);
    results.pageErrors = 0;
    fs.writeFileSync(path.join(output, 'verification.json'), JSON.stringify(results,null,2));
    console.log(JSON.stringify(results,null,2));
  } finally {
    const cleanup = await context.request.post(origin + '/__dashboard_purchase_e2e__/cleanup');
    assert.equal(cleanup.status(), 200, 'Synthetic rows cleaned');
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
