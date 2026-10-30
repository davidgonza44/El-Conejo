// Run only against the existing test_settings_reference no-database harness.
const {chromium} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const output = fs.mkdtempSync('.tmp/movement-inline-continuation-');
const base = 'http://127.0.0.1:5000';

async function verify(page) {
    const errors = [], writes = [], failures = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('requestfailed', request => failures.push(request.url()));
    page.on('request', request => {
        if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) writes.push(request.method());
    });
    await page.goto(`${base}/inventory?ref=1`, {waitUntil: 'networkidle'});
    await page.evaluate(() => document.fonts.ready);
    const environment = await page.evaluate(() => ({width: innerWidth, height: innerHeight,
        dpr: devicePixelRatio, scale: visualViewport.scale, zoom: getComputedStyle(document.documentElement).zoom}));
    assert.deepEqual(environment, {width:1600, height:900, dpr:1, scale:1, zoom:'1'});
    const fixture = await page.locator('#mv-fixture').textContent();
    const rows = JSON.parse(fixture).history.rows;
    const popover = page.locator('#mv-inline-popover');
    const control = key => page.locator(`[data-mv-inline="${key}"]`);
    const visibleRefs = () => page.locator('[data-movement-ref]:visible').evaluateAll(nodes => nodes.map(n => n.dataset.movementRef));
    const expectRows = async expected => assert.deepEqual(await visibleRefs(), expected.map(row => row.ref));
    const clear = async () => page.getByRole('button', {name:'Limpiar filtros', exact:true}).click();
    const select = async (key, text) => {
        await control(key).click();
        await popover.getByRole('option', {name:text, exact:true}).click();
        assert(await popover.isHidden());
        assert.match(await control(key).innerText(), new RegExp(text));
    };
    const capture = name => page.screenshot({path:`${output}/${name}.png`});
    const originalMarkup = await page.locator('.iv-mov-body').innerHTML();
    const frozenGeometry = () => page.locator('.ix-sidebar, .iv-toolbar, .iv-kpis, .iv-mov-body').evaluateAll(nodes => nodes.map(node => {
        const {x,y,width,height} = node.getBoundingClientRect(); return {x,y,width,height};
    }));
    const geometry = await frozenGeometry();
    await expectRows(rows);
    await capture('base');
    for (const [key, options, property] of [
        ['kind', ['Todos','Entrada','Salida','Ajuste'], row => row.kind[0]],
        ['user', ['Todos', ...new Set(rows.map(row => row.user))], row => row.user],
    ]) {
        await control(key).click();
        assert.deepEqual(await popover.getByRole('option').allTextContents(), options);
        await capture(key);
        await page.keyboard.press('Escape');
        assert(await popover.isHidden());
        assert(await control(key).evaluate(node => node === document.activeElement));
        for (const value of options.slice(1)) {
            await select(key, value);
            await expectRows(rows.filter(row => property(row) === value));
        }
        await clear();
    }
    // Switching controls, outside click, keyboard opening/navigation and layer mutual exclusion.
    await control('kind').click();
    await control('user').click();
    assert.equal(await page.locator('[data-mv-inline][aria-expanded="true"]').count(), 1);
    assert.equal(await control('kind').getAttribute('aria-expanded'), 'false');
    await page.locator('h1').click();
    assert(await popover.isHidden());
    await control('kind').focus();
    await page.keyboard.press('Enter');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    await expectRows(rows.filter(row => row.kind[0] === 'Entrada'));
    await clear();

    const quickRanges = [
        ['Hoy','31 may. 2024 - 31 may. 2024',3],
        ['Últimos 7 días','25 may. 2024 - 31 may. 2024',6],
        ['Últimos 30 días','02 may. 2024 - 31 may. 2024',6],
        ['Este mes','01 may. 2024 - 31 may. 2024',6],
        ['Mes pasado','01 abr. 2024 - 30 abr. 2024',0],
        ['Últimos 3 meses','01 mar. 2024 - 31 may. 2024',6],
        ['Últimos 6 meses','01 dic. 2023 - 31 may. 2024',6],
        ['Este año','01 ene. 2024 - 31 dic. 2024',6],
        ['Año pasado','01 ene. 2023 - 31 dic. 2023',0],
    ];
    await control('date').click();
    assert.equal(await popover.locator('.mv-inline-month').count(), 2);
    assert.equal(await popover.locator('.mv-inline-quick').count(), 10);
    assert.equal(await popover.locator('input[type="date"]').count(), 0);
    await capture('date');
    await popover.getByRole('button', {name:'Hoy', exact:true}).click();
    await popover.getByRole('button', {name:'Cancelar', exact:true}).click();
    await expectRows(rows);
    assert.match(await control('date').innerText(), /01 may. 2024 - 31 may. 2024/);
    for (const [name, range, count] of quickRanges) {
        await control('date').click();
        await popover.getByRole('button', {name, exact:true}).click();
        assert.equal(await popover.locator('.mv-inline-date-footer > span').innerText(), range);
        await popover.getByRole('button', {name:'Aplicar', exact:true}).click();
        assert.equal((await visibleRefs()).length, count);
        assert.equal(await control('date').innerText(), range);
        assert.equal(await page.locator('.mv-inline-empty').isVisible(), count === 0);
    }
    await clear();
    await control('date').click();
    await popover.getByRole('button', {name:'Mes siguiente', exact:true}).click();
    assert.match(await popover.locator('.mv-inline-month header').first().innerText(), /junio/);
    await popover.getByRole('button', {name:'Mes anterior', exact:true}).click();
    await popover.getByRole('button', {name:'Rango personalizado', exact:true}).click();
    await popover.locator('[data-day="2024-05-31"]').first().click();
    await popover.locator('[data-day="2024-05-29"]').first().click();
    assert(await popover.locator('[data-day="2024-05-30"]').first().evaluate(node => node.classList.contains('is-between')));
    await capture('custom');
    await popover.getByRole('button', {name:'Aplicar', exact:true}).click();
    assert.equal(await control('date').innerText(), '29 may. 2024 - 31 may. 2024');
    await select('kind', 'Salida');
    await select('user', 'Pedro Gómez');
    await expectRows(rows.filter(row => row.ref === 'SAL-0186'));
    await select('user', 'Pedro Gómez');
    await control('date').click();
    await popover.locator('[data-day="2024-05-30"]').first().click();
    await popover.locator('[data-day="2024-05-30"]').first().click();
    await popover.getByRole('button', {name:'Aplicar', exact:true}).click();
    await expectRows(rows.filter(row => row.ref === 'SAL-0186'));
    await capture('combined');
    await control('date').click();
    await popover.getByRole('button', {name:'Hoy', exact:true}).click();
    await popover.getByRole('button', {name:'Aplicar', exact:true}).click();
    await expectRows([]);
    await clear();
    await expectRows(rows);
    assert.equal(await page.locator('.iv-mov-body').innerHTML(), originalMarkup);
    assert.deepEqual(await frozenGeometry(), geometry);
    await page.locator('h1').click();
    await capture('cleared');

    for (const name of ['Nuevo movimiento','Filtros','Exportar']) {
        await control('kind').click();
        await page.locator('.iv-toolbar-actions button').filter({hasText:name}).click();
        assert(await popover.isHidden());
        assert(await page.locator(name === 'Exportar' ? '#mv-export' : '#mv-drawer').isVisible());
        await page.keyboard.press('Escape');
    }
    for (const trigger of [page.locator('[data-movement-ref="ENT-0098"] button'), page.locator('[data-mv-open="activities"]')]) {
        await control('kind').click(); await trigger.click();
        assert(await popover.isHidden());
        assert(await page.locator('#mv-drawer').isVisible());
        await page.keyboard.press('Escape');
    }
    const regression = [];
    for (const route of ['inventory','products','categories','suppliers','dashboard']) {
        const response = await page.goto(`${base}/${route}?ref=1`, {waitUntil:'networkidle'});
        assert.equal(response.status(), 200);
        assert(await page.locator('[aria-controls="ix-nav-pred"]').isVisible());
        if (await page.locator('[aria-controls="ix-nav-pred"]').getAttribute('aria-expanded') === 'false') {
            await page.locator('[aria-controls="ix-nav-pred"]').click();
        }
        assert.equal(await page.locator('#ix-nav-pred a:visible').count(), 5);
        await capture(route + '-regression');
        regression.push(route);
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, []);
    assert.deepEqual(failures, []);
    const result = {result:'PASS',environment,regression,errors,writes,failures,output,
        note:'Quick ranges anchored to latest fixture day (2024-05-31). CDN resources replayed from existing local cache.'};
    fs.writeFileSync(`${output}/result.json`, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
}

(async () => {
    const browser = await chromium.launch({executablePath: process.env.MOVEMENT_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless:true});
    try {
        const context = await browser.newContext({viewport:{width:1600,height:900}, deviceScaleFactor:1});
        await context.route(/https:\/\/(cdn.jsdelivr.net|fonts.googleapis.com|fonts.gstatic.com)\//, route => {
            const url = new URL(route.request().url());
            const file = url.hostname === 'fonts.googleapis.com' ? 'inter.css' : path.basename(url.pathname);
            const local = path.join('.tmp/sales-reports-validation', file);
            if (!fs.existsSync(local)) return route.continue();
            return route.fulfill({path:local, contentType:file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'application/javascript' : file.endsWith('.woff2') ? 'font/woff2' : 'font/ttf'});
        });
        await verify(await context.newPage());
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
