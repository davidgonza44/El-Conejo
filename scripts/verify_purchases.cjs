/* Browser proof against the loopback purchase harness with DATABASE() write guards. */
const {chromium, expect} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const origin = 'http://127.0.0.1:51432';
const output = '.tmp/purchases-validation';

(async () => {
  const browser = await chromium.launch({channel:'msedge'});
  const results = {controls:[], overflow:{}, geometry:{}};
  try {
    const context = await browser.newContext({viewport:{width:1600,height:900}, deviceScaleFactor:1, locale:'es-VE'});
    // Reuse only public vendor assets already cached by the existing browser harness.
    await context.route(/https:\/\/(cdn.jsdelivr.net|fonts.googleapis.com|fonts.gstatic.com)\//, route => {
      const url = new URL(route.request().url());
      const name = url.hostname === 'fonts.googleapis.com' ? 'inter.css' : path.basename(url.pathname);
      const local = path.join('.tmp/sales-reports-validation', name);
      if (!/\.(css|js|woff2?|ttf)$/.test(name) || !fs.existsSync(local)) return route.abort();
      return route.fulfill({path:local, contentType:name.endsWith('.css') ? 'text/css' : name.endsWith('.js') ? 'application/javascript' : name.endsWith('.woff2') ? 'font/woff2' : 'font/ttf'});
    });
    const api = async url => {
      const response = await context.request.get(origin + url);
      assert.equal(response.status(), 200, url);
      return response.json();
    };
    const ids = await api('/__purchases_e2e__/context');
    assert.equal(ids.database, 'ferreteria_conejo_e2e');
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
      await page.screenshot({path:path.join(output, name + '.png')});
    };
    const close = async () => page.locator('#po-dialog > header [data-close]').click();
    const action = async (id, name) => {
      await page.locator(`.po-table [data-action=menu][data-id="${id}"]`).click();
      await expect(page.locator('#po-menu')).toBeVisible();
      await page.locator(`#po-menu [data-action="${name}"]`).click();
      if (name !== 'print') await expect(page.locator('#po-dialog')).toBeVisible();
    };
    const saved = async label => {
      await page.locator('#po-dialog [type=submit]').click();
      await expect(page.locator('#po-dialog-title')).toContainText('Orden de compra OC-');
      results.controls.push(label);
    };
    const changeFilter = async (name, value) => {
      const input = page.locator(`#po-filters [name="${name}"]`);
      await Promise.all([page.waitForURL(url => url.searchParams.get(name) === value), input.selectOption(value)]);
      await page.waitForLoadState('networkidle');
    };
    await go('/__purchases_e2e__/login');

    // Every reference modal submits locally without real mutation APIs or SQL writes.
    const writeBefore = await api('/__purchases_e2e__/write-count');
    const mutationBefore = mutations.length;
    await go('/purchases?ref=1');
    await shot('purchases-orders-reference');
    const firstFixture = await api('/api/purchases/42?ref=1');
    for (const name of ['detail','edit','receive','duplicate','cancel']) {
      await action(42, name);
      if (name === 'detail') await close();
      else {
        if (name === 'receive') await page.locator('#po-reception-form input[type=number]').first().fill('1');
        await page.locator('#po-dialog [type=submit]').click();
        await expect(page.locator('#po-dialog')).not.toBeVisible();
      }
    }
    await page.locator('#po-new').click();
    await page.locator('#po-order-form [name=supplier_id]').selectOption(String(firstFixture.supplier.id));
    await page.locator('#po-product-results button').first().click();
    await page.locator('#po-order-form [type=submit]').click();
    await expect(page.locator('#po-dialog')).not.toBeVisible();
    const referenceDownload = page.waitForEvent('download');
    await page.locator('#po-export').click();
    assert.equal((await referenceDownload).suggestedFilename(), 'ordenes-de-compra.csv');
    assert.deepEqual(await api('/api/purchases/42?ref=1'), firstFixture);
    assert.equal((await api('/__purchases_e2e__/write-count')).count, writeBefore.count);
    assert.equal(mutations.length, mutationBefore);
    results.referenceWrites = 0;
    results.referenceMutationAPIs = 0;

    // Live controls and end-to-end business actions use only synthetic order/product IDs.
    await go('/purchases');
    await shot('purchases-orders-live');
    await expect(page.locator('[aria-controls=ix-nav-compras]')).toHaveAttribute('aria-expanded','true');
    await expect(page.locator('#ix-nav-compras [aria-current=page]')).toHaveText('Órdenes de compra');
    await page.locator('[aria-controls=ix-nav-pred]').click();
    for (const label of ['Pronóstico de demanda','Reabastecimiento','Productos críticos','Tendencias','Precisión del modelo']) {
      await expect(page.locator('#ix-nav-pred').getByText(label, {exact:true})).toBeVisible();
    }
    await page.locator('[aria-controls=ix-nav-pred]').click();
    results.sidebar = 'PASS';
    await page.locator('#po-toggle-filters').click();
    await expect(page.locator('#po-filters')).not.toBeVisible();
    await page.locator('#po-toggle-filters').click();
    await expect(page.locator('#po-filters')).toBeVisible();
    results.controls.push('Filtros');
    await changeFilter('status','pending');
    assert.ok((await page.locator('.po-table tbody tr').evaluateAll(rows => rows.map(row => row.dataset.status))).every(status => status === 'pending'));
    results.controls.push('status filter');
    await changeFilter('supplier_id', String(ids.supplier_ids[0]));
    results.controls.push('supplier filter');
    await page.locator('#po-clear').click();
    await expect(page.locator('#po-filters [name=status]')).toHaveValue('');
    await expect(page.locator('#po-filters [name=supplier_id]')).toHaveValue('');
    results.controls.push('clear filters');
    const today = await page.locator('#po-context').evaluate(node => JSON.parse(node.textContent).today);
    for (const name of ['start','end']) {
      await Promise.all([
        page.waitForURL(url => url.searchParams.get(name) === today),
        page.locator(`#po-filters [name=${name}]`).fill(today)
      ]);
      await page.waitForLoadState('networkidle');
      results.controls.push(name + ' date filter');
    }
    await page.locator('#po-filters [name=search]').fill(ids.run);
    await page.locator('#po-filters [name=search]').press('Enter');
    await page.waitForURL(url => url.searchParams.get('search') === ids.run);
    await page.waitForLoadState('networkidle');
    results.controls.push('search');
    await page.locator('#po-per-page').selectOption('20');
    await page.waitForURL(url => url.searchParams.get('per_page') === '20');
    await page.waitForLoadState('networkidle');
    assert.ok(await page.locator('.po-table tbody tr').count() > 8);
    await page.locator('#po-per-page').selectOption('8');
    await page.waitForURL(url => url.searchParams.get('per_page') === '8');
    await page.waitForLoadState('networkidle');
    await page.getByRole('link',{name:'Página siguiente'}).click();
    await page.waitForURL(url => url.searchParams.get('page') === '2');
    await page.getByRole('link',{name:'Página anterior'}).click();
    await page.waitForURL(url => url.searchParams.get('page') === '1');
    results.controls.push('pagination','per-page selector');
    const csvDownload = page.waitForEvent('download');
    await page.locator('#po-export').click();
    const download = await csvDownload;
    const csv = await fs.promises.readFile(await download.path(), 'utf8');
    assert.ok(csv.includes('N° Orden') && csv.includes('E2E'));
    results.controls.push('Exportar CSV');
    await page.locator('#po-view-all').click();
    await expect(page.locator('#po-filters [name=search]')).toHaveValue('');
    results.controls.push('view all orders');
    await page.locator('.po-upcoming .po-number').first().click();
    await expect(page.locator('#po-dialog')).toBeVisible();
    await close();
    for (const link of ['.po-upcoming .po-small-link','.po-panel-footer','.po-top .po-small-link']) {
      await page.locator(link).click();
      assert.ok(['/purchases/receptions','/suppliers'].includes(new URL(page.url()).pathname));
      await go('/purchases');
    }
    results.controls.push('upcoming reception links','top suppliers link');

    await page.locator('#po-new').click();
    await expect(page.locator('#po-order-form')).toBeVisible();
    await page.locator('#po-order-form [name=supplier_id]').selectOption(String(ids.supplier_ids[0]));
    await Promise.all([
      page.waitForResponse(response => response.url().includes('/api/purchases/options?search=PO-E2E-' + ids.run)),
      page.locator('#po-product-search').fill('PO-E2E-' + ids.run)
    ]);
    await expect(page.locator(`#po-product-results [data-product="${ids.product_ids[0]}"]`)).toBeVisible();
    await page.locator(`#po-product-results [data-product="${ids.product_ids[0]}"]`).click();
    await page.locator('[data-field=quantity]').fill('10');
    await page.locator('[name=estimated_delivery_date]').fill(today);
    await page.locator('[name=notes]').fill('Validación Playwright: orden sintética.');
    await shot('purchase-order-new');
    const beforeStock = (await api('/api/products/' + ids.product_ids[0])).current_stock;
    await saved('Nueva orden de compra');
    const orderId = Number(new URL(page.url()).searchParams.get('detail'));
    const createdOrder = await api('/api/purchases/' + orderId);
    assert.match(createdOrder.ordered_date, /^\d{4}-\d{2}-\d{2}$/);
    assert.equal(createdOrder.ordered_date, today);
    assert.equal((await api('/api/products/' + ids.product_ids[0])).current_stock, beforeStock);
    await close();
    await page.locator(`.po-table [data-action=detail][data-id="${orderId}"]`).last().click();
    await expect(page.locator('#po-dialog')).toBeVisible();
    await expect(page.locator('.po-detail-grid')).toContainText(createdOrder.ordered_date);
    await shot('purchase-order-detail');
    results.controls.push('eye detail');
    await close();
    await page.locator(`.po-table [data-action=menu][data-id="${orderId}"]`).click();
    await expect(page.locator('#po-menu')).toBeVisible();
    await shot('purchase-order-actions-menu');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Escape');
    await expect(page.locator('#po-menu')).not.toBeVisible();
    results.controls.push('three-dot menu');
    await action(orderId, 'edit');
    await page.locator('[name=notes]').fill('Orden editada mediante Playwright.');
    await shot('purchase-order-edit');
    await saved('edit order');
    assert.equal((await api('/api/purchases/' + orderId)).notes, 'Orden editada mediante Playwright.');
    await close();
    await action(orderId, 'receive');
    const dateInput = page.locator('[name=received_at]');
    await expect(dateInput).toHaveAttribute('min', (await api('/api/purchases/' + orderId)).ordered_date);
    await expect(dateInput).toHaveAttribute('max', today);
    await page.locator('#po-reception-form input[type=number]').fill('6');
    await shot('purchase-order-reception');
    await saved('register reception');
    assert.equal((await api('/api/products/' + ids.product_ids[0])).current_stock, beforeStock + 6);
    assert.equal((await api('/api/purchases/' + orderId)).status, 'partial');
    await close();
    const popupEvent = context.waitForEvent('page');
    await action(orderId, 'print');
    const printPage = await popupEvent;
    await printPage.waitForLoadState();
    await expect(printPage.locator('h1')).toHaveText('Ferretería Pro');
    await expect(printPage.locator('body')).toContainText('Fecha: ' + createdOrder.ordered_date);
    await printPage.screenshot({path:path.join(output,'purchase-order-print.png')});
    await printPage.evaluate(() => { window.print = () => { window.printCalled = true; }; });
    await printPage.getByRole('button',{name:'Imprimir',exact:true}).click();
    assert.equal(await printPage.evaluate(() => window.printCalled), true);
    await printPage.close();
    results.controls.push('print');
    await action(orderId, 'duplicate');
    await saved('duplicate order');
    const duplicateId = Number(new URL(page.url()).searchParams.get('detail'));
    assert.notEqual(duplicateId,orderId);
    assert.equal((await api('/api/purchases/' + duplicateId)).received_units,0);
    await close();
    await action(duplicateId,'cancel');
    await saved('cancel order');
    assert.equal((await api('/api/purchases/' + duplicateId)).status,'cancelled');
    await close();

    for (const [width,height] of [[1600,900],[1366,768],[1280,720]]) {
      await page.setViewportSize({width,height});
      for (const ref of [false,true]) {
        await go('/purchases' + (ref ? '?ref=1' : ''));
        const geometry = await page.evaluate(() => {
          const boxes = {};
          for (const selector of ['.po-kpis','.po-filters','.po-orders','.po-status','.po-upcoming','.po-top']) {
            const r = document.querySelector(selector).getBoundingClientRect();
            boxes[selector] = {x:r.x,y:r.y,width:r.width,height:r.height};
          }
          return {overflow:document.documentElement.scrollWidth > innerWidth, boxes};
        });
        assert.equal(geometry.overflow,false,`overflow ${width} reference=${ref}`);
        results.overflow[`${width}-${ref ? 'reference' : 'live'}`] = 'PASS';
        results.geometry[`${width}-${ref ? 'reference' : 'live'}`] = geometry.boxes;
        if (width === 1600) {
          // Approved screenshot macro rectangles, allowing only the existing ~3 px tolerance.
          const approved = {'.po-kpis':[275,122,1109,109], '.po-filters':[272,245,1300,88],
            '.po-orders':[272,343,951,524], '.po-status':[1233,343,339,195],
            '.po-upcoming':[1233,545,339,170], '.po-top':[1233,723,339,138]};
          for (const [selector, expected] of Object.entries(approved)) {
            const actual = geometry.boxes[selector];
            ['x','y','width','height'].forEach((key,i) => assert.ok(Math.abs(actual[key] - expected[i]) <= 3,
              `${selector} ${key}: ${actual[key]} versus approved ${expected[i]}`));
          }
        }
        await shot(width === 1600 ? `purchases-orders-${ref ? 'reference' : 'live'}` : `purchases-orders-${ref ? 'reference' : 'live'}-${width}`);
      }
    }
    assert.deepEqual(errors,[]);
    results.playwright = 'PASS';
    results.noOpControls = 0;
    fs.writeFileSync(path.join(output,'verification.json'),JSON.stringify(results,null,2));
    console.log(JSON.stringify(results,null,2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
