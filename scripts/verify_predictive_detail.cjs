/* Run only against the disposable SQLite server: test_predictive_module.py --serve. */
const {chromium} = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const origin = 'http://127.0.0.1:' + (process.env.PREDICTIVE_DETAIL_TEST_PORT || '5038');
(async () => {
    const browser = await chromium.launch({channel:'msedge'});
    let passed = 0;
    const output = '.tmp/predictive-detail-validation';
    fs.mkdirSync(output, {recursive:true});
    try {
        const context = await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1,locale:'es-VE'});
        const login = await context.request.post(origin+'/api/auth/login',{data:{identifier:'core_admin',password:'SecretCorePass999'}});
        assert.equal(login.status(),200);
        const page = await context.newPage();
        const errors=[];
        page.on('pageerror',e=>errors.push(e.message));
        for (const [route, expected] of [['critical','summary'],['demand','forecast'],['replenishment','replenishment'],['trends','demand']]) {
            await page.goto(origin+'/predictive/'+route,{waitUntil:'networkidle'});
            const button=page.locator('[data-product-detail]').first();
            const id=await button.getAttribute('data-product-detail');
            await button.click();
            await page.waitForSelector('#pd-product h3');
            assert.equal(await page.locator('[aria-selected="true"][data-pd-tab]').getAttribute('data-pd-tab'),expected);
            const dto=await (await context.request.get(origin+`/api/predictions/products/${id}/detail`)).json();
            assert.equal(await page.locator('#pd-product h3').innerText(),dto.product.name);
            await page.keyboard.press('Escape');
            assert.equal(await button.evaluate(e=>document.activeElement===e),true);
            passed+=3;
        }
        for (const [width,height] of [[1600,900],[1366,768],[1280,720]]) {
            await page.setViewportSize({width,height});
            for (const mode of ['live','reference']) {
                await page.goto(origin+'/predictive/critical'+(mode==='reference'?'?ref=1':''),{waitUntil:'networkidle'});
                await page.locator('[data-product-detail]').first().focus();
                await page.keyboard.press('Enter');
                await page.waitForSelector('#pd-product h3');
                for (const tab of ['summary','demand','forecast','inventory','replenishment']) {
                    await page.locator(`[data-pd-tab="${tab}"]`).click();
                    const g=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,dpr:devicePixelRatio,zoom:visualViewport.scale,drawer:document.querySelector('#pd-drawer').getBoundingClientRect().toJSON()}));
                    assert.ok(g.scroll<=g.width+1,JSON.stringify(g));
                    assert.equal(g.dpr,1);assert.equal(g.zoom,1);
                    assert.ok(g.drawer.right<=width+1 && g.drawer.left>=0,JSON.stringify(g));
                    assert.equal(await page.locator(`[role="tabpanel"][aria-labelledby="pd-tab-${tab}"]`).count(),1);
                    assert.equal(await page.evaluate(()=>Array.from(document.querySelectorAll('#pd-body canvas')).every(c=>typeof Chart!=='undefined' && Chart.getChart(c)?.width>0)),true,'Charts must render');
                    assert.equal(await page.evaluate(()=>Array.from(document.querySelectorAll('#pd-body,.pd-table-wrap')).every(e=>e.scrollWidth<=e.clientWidth+1)),true,'No internal horizontal overflow');
                    await page.screenshot({path:`${output}/${mode==='reference'?'detail':'live'}-${tab}-${width}.png`});
                    if(mode==='reference') {
                        await page.locator('#pd-body').evaluate(e=>e.scrollTop=e.scrollHeight);
                        await page.screenshot({path:`${output}/detail-${tab}-${width}-bottom.png`});
                    }
                    passed++;
                }
                await page.locator('[data-pd-tab="summary"]').focus();
                await page.keyboard.press('ArrowRight');
                assert.equal(await page.locator('[data-pd-tab="demand"]').getAttribute('aria-selected'),'true');
                if(mode==='reference')assert.equal(await page.locator('#pd-buy').isDisabled(),true);
                await page.locator('[data-pd-close]').click();
            }
        }
        await page.setViewportSize({width:1374,height:1145});
        await page.goto(origin+'/predictive/critical?ref=1',{waitUntil:'networkidle'});
        await page.locator('[data-product-detail]').first().focus();
        await page.keyboard.press('Enter');
        await page.waitForSelector('#pd-product h3');
        await page.locator('[data-pd-tab="forecast"]').click();
        await page.screenshot({path:`${output}/detail-forecast-reference-size.png`});
        await page.goto(origin+'/predictive/critical',{waitUntil:'networkidle'});
        const candidate=page.locator('[data-product-detail]').filter({hasText:'Ver detalle'}).first();
        await candidate.focus();await page.keyboard.press('Enter');await page.waitForSelector('#pd-product h3');
        if(await page.locator('#pd-buy').isEnabled()){
            await page.locator('#pd-buy').click();
            await page.waitForSelector('#rl-order[open]');
            assert.equal(await page.locator('#rl-order-lines tr').count(),1);
            assert.ok(await page.locator('#rl-order-supplier option').count()>1);
            await page.locator('#rl-order [data-order-close]').first().click();passed++;
        }
        assert.deepEqual(errors,[]);
        console.log(`${passed} PASS / 0 FAIL; screenshots ${output}`);
    } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
