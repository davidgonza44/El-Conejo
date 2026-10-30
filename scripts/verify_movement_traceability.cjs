/* Only the dedicated SQLite fixture at :5046; never an operational server. */
const {chromium, expect} = require('@playwright/test');
const fs = require('node:fs');
const origin = 'http://127.0.0.1:5046';
const output = 'tmp/movement-traceability';

(async () => {
    fs.mkdirSync(output, {recursive:true});
    const browser = await chromium.launch({headless:true,channel:'msedge'});
    const page = await browser.newPage({viewport:{width:1600,height:900},deviceScaleFactor:1});
    const errors=[];
    page.on('pageerror', error=>errors.push(error.message));
    async function inventory() {
        await page.goto(origin+'/inventory');
        await expect(page.locator('.iv-history-table tbody')).toHaveAttribute('aria-busy','false');
    }
    try {
        await page.goto(origin+'/_test/session/admin');
        await inventory();
        await expect(page.getByRole('link',{name:/VTA-000001/})).toHaveAttribute('href','/sales/VTA-000001');
        await expect(page.getByRole('link',{name:/OC-000001.*REC-00001/})).toHaveAttribute('href','/purchases/receptions?detail=1');
        await expect(page.getByRole('link',{name:/CONC-000001/})).toHaveAttribute('href','/reconciliation?detail=1');
        await expect(page.locator('.iv-history-table').getByText('Origen no identificado')).toBeVisible();
        await page.screenshot({path:output+'/history-1600.png',fullPage:true});

        await page.getByRole('button',{name:'Nuevo movimiento',exact:true}).click();
        await expect(page.locator('#mv-reference')).toHaveValue('Se asignará al guardar');
        await expect(page.locator('#mv-reference')).toBeDisabled();
        await expect(page.locator('#mv-external-reference')).toHaveAttribute('maxlength','120');
        await page.locator('#mv-kind').selectOption('entrada');
        await page.locator('#mv-product').selectOption('1');
        await page.locator('#mv-quantity').fill('1');
        await page.locator('#mv-external-reference').fill('  VTA-001572  ');
        await page.screenshot({path:output+'/new-1600.png',fullPage:true});
        const response=page.waitForResponse(r=>r.url()===origin+'/api/inventory/entry' && r.request().method()==='POST');
        await page.getByRole('button',{name:'Guardar movimiento',exact:true}).click();
        const saved=await (await response).json();
        expect(saved.movement.source_type).toBe('manual');
        expect(saved.movement.external_reference).toBe('VTA-001572');
        expect(saved.movement.display_reference).toBe(saved.movement.movement_number);
        await expect(page.locator('#mv-status')).toContainText(saved.movement.movement_number);
        await page.getByRole('link',{name:new RegExp(saved.movement.movement_number+' .*Movimiento manual')}).click();
        await expect(page.locator('#mv-content')).toContainText('Referencia externa');
        await expect(page.locator('#mv-content')).toContainText('VTA-001572');
        await page.screenshot({path:output+'/manual-detail-1600.png',fullPage:true});
        await page.getByRole('button',{name:'Cerrar panel',exact:true}).click();

        await page.getByRole('link',{name:/VTA-000001/}).click();
        await expect(page).toHaveURL(origin+'/sales/VTA-000001');
        await inventory();
        await page.getByRole('link',{name:/OC-000001.*REC-00001/}).click();
        await expect(page).toHaveURL(origin+'/purchases/receptions?detail=1');
        await expect(page.getByText('Detalle de recepción',{exact:true})).toBeVisible();
        await inventory();
        await page.getByRole('link',{name:/CONC-000001/}).click();
        await expect(page.locator('#rc-detail-title')).toContainText('CONC-000001');
        await page.screenshot({path:output+'/reconciliation-1600.png',fullPage:true});
        await page.goto(origin+'/reconciliation?detail=1%2Fconfirm');
        await expect(page.locator('#rc-detail')).toBeHidden();

        await page.goto(origin+'/_test/session/inventario');
        await inventory();
        await expect(page.getByRole('link',{name:/CONC-000001/})).toHaveCount(0);
        await expect(page.locator('.iv-history-table').getByText('CONC-000001',{exact:true})).toBeVisible();
        await page.goto(origin+saved.movement.source_url);
        await expect(page.locator('#mv-content')).toContainText(saved.movement.movement_number);
        await page.setViewportSize({width:390,height:844});
        await page.screenshot({path:output+'/manual-detail-mobile.png'});
        expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
        await page.getByRole('button',{name:'Cerrar panel',exact:true}).press('Enter');
        await expect(page.locator('#mv-drawer')).toBeHidden();
        expect(errors).toEqual([]);
        console.log('PASS: real UI creation, server MOV, external text, exact links, permissions, safe detail parameter, manual deep link, keyboard and mobile; 5 synthetic screenshots.');
    } finally {
        await browser.close();
    }
})().catch(error=>{console.error(error);process.exitCode=1;});
