/* Run only with test_profile_core.py --serve (temporary SQLite and storage). */
const { chromium, expect } = require('@playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const origin = 'http://127.0.0.1:' + (process.env.PROFILE_TEST_PORT || '5041');
const output = '.tmp/profile-validation';

(async () => {
    const browser = await chromium.launch({ channel: 'msedge' });
    const results = { screens: [], errors: [], checks: [] };
    try {
        const context = await browser.newContext({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1, locale: 'es-VE' });
        // Optional reviewed public CDN cache for networks that block external fonts/CSS.
        if (process.env.PROFILE_ASSET_CACHE) {
            await context.route(/https:\/\/(cdn.jsdelivr.net|fonts.googleapis.com|fonts.gstatic.com)\//, route => {
                const url = new URL(route.request().url());
                const name = url.hostname === 'fonts.googleapis.com' ? 'inter.css' : path.basename(url.pathname);
                const local = path.join(process.env.PROFILE_ASSET_CACHE, name);
                if (!/\.(css|js|woff2?|ttf)$/.test(name) || !fs.existsSync(local)) return route.abort();
                return route.fulfill({ path: local, contentType: name.endsWith('.css') ? 'text/css' :
                    name.endsWith('.js') ? 'application/javascript' : name.endsWith('.ttf') ? 'font/ttf' : 'font/woff2' });
            });
        }
        const marker = await context.request.get(origin + '/__profile_test__');
        assert.deepEqual(await marker.json(), { isolated: true, database: 'temporary-sqlite' });
        const login = await context.request.post(origin + '/api/auth/login', {
            data: { identifier: 'core_admin', password: 'SecretCorePass999' }
        });
        assert.equal(login.status(), 200);
        const page = await context.newPage();
        page.on('pageerror', error => results.errors.push(error.message));
        fs.mkdirSync(output, { recursive: true });
        const go = async url => {
            await page.goto(origin + url, { waitUntil: 'networkidle' });
            await page.evaluate(() => document.fonts.ready);
            assert.equal(await page.evaluate(() => getComputedStyle(document.body).boxSizing), 'border-box', 'Tabler CSS must load');
            assert.equal(await page.evaluate(() => document.fonts.check('14px Inter')), true, 'Inter must load');
        };
        const shot = name => page.screenshot({ path: path.join(output, name + '.png') });
        for (const [width, height] of [[1600, 900], [1366, 768], [1280, 720]]) {
            await page.setViewportSize({ width, height });
            for (const tab of ['profile', 'preferences', 'activity', 'sessions']) {
                await go('/profile?tab=' + tab);
                const geometry = await page.evaluate(() => {
                    const style = selector => {
                        const element = document.querySelector(selector), css = getComputedStyle(element);
                        return { size: css.fontSize, font: css.fontFamily, weight: css.fontWeight, color: css.color };
                    };
                    return { width: innerWidth, scroll: document.documentElement.scrollWidth, dpr: devicePixelRatio,
                        zoom: visualViewport.scale, brand: style('.ix-brand-name'), subtitle: style('.ix-brand-sub'),
                        logo: document.querySelector('.ix-brand-logo').getBoundingClientRect().width };
                });
                assert.ok(geometry.scroll <= width, JSON.stringify(geometry));
                assert.equal(geometry.dpr, 1); assert.equal(geometry.zoom, 1);
                assert.equal(geometry.brand.size, '22px'); assert.equal(geometry.brand.weight, '700');
                assert.ok(geometry.brand.font.startsWith('Arial')); assert.equal(geometry.brand.color, 'rgb(12, 18, 48)');
                assert.equal(geometry.subtitle.size, '16px'); assert.equal(geometry.logo, 48);
                await expect(page.locator('.pr-tabs [aria-current=page]')).toHaveCount(1);
                const previous = page.url();
                await page.locator('#ix-account-toggle').click();
                await expect(page.locator('#ix-account-menu')).toBeVisible();
                assert.equal(page.url(), previous);
                await expect(page.locator('#ix-account-menu a').first()).toHaveAttribute('href', '/profile');
                await shot(`${tab}-${width}`);
                results.screens.push({ tab, width, height, ...geometry });
                await page.keyboard.press('Escape');
                await expect(page.locator('#ix-account-menu')).toBeHidden();
                await expect(page.locator('#ix-account-toggle')).toBeFocused();
                if (tab === 'activity') {
                    await page.locator('.pr-pagination').scrollIntoViewIfNeeded();
                    await expect(page.locator('.pr-pagination')).toBeInViewport();
                    await shot(`activity-pagination-${width}`);
                }
            }
            await go('/profile#change-password');
            await expect(page.locator('#profile-password-dialog')).toBeVisible();
            await expect(page.locator('#profile-password-cancel')).toBeInViewport();
            await shot(`password-${width}`);
            await page.keyboard.press('Escape');
            await expect(page.locator('#profile-password-dialog')).toBeHidden();
        }
        await go('/profile?tab=activity');
        await page.locator('[name=search]').fill('Navegador de prueba');
        await page.locator('[name=status]').selectOption('failed');
        await page.getByRole('button', { name: 'Filtrar', exact: true }).click();
        await page.locator('#profile-per-page').selectOption('5');
        await expect(page.locator('.pr-pagination')).toContainText('Mostrando 1 a 5 de 12 registros');
        await page.getByRole('link', { name: 'Siguiente', exact: true }).click();
        await expect(page.locator('.pr-pagination')).toContainText('Mostrando 6 a 10 de 12 registros');
        await expect(page.locator('[name=status]')).toHaveValue('failed');
        await page.locator('.pr-pagination').scrollIntoViewIfNeeded();
        await shot('activity-filtered-page-2');
        await page.locator('[name=search]').fill('sin coincidencias');
        await page.getByRole('button', { name: 'Filtrar', exact: true }).click();
        await expect(page.locator('.pr-pagination')).toContainText('Mostrando 0 a 0 de 0 registros');
        await shot('activity-empty');
        results.checks.push('filters, pagination, empty state, account toggle, keyboard, password dialogs');
        await page.setViewportSize({ width: 1600, height: 900 });
        await go('/profile?ref=1');
        await page.locator('#ix-account-toggle').click();
        await shot('profile-reference-1600');
        await page.locator('#profile-save').click();
        await expect(page.locator('#page-alert')).toContainText('no permite cambios');
        await go('/profile');
        const png = await page.evaluate(() => {
            const canvas = document.createElement('canvas'); canvas.width = 180; canvas.height = 180;
            const paint = canvas.getContext('2d'); paint.fillStyle = '#0066ff'; paint.fillRect(0, 0, 180, 180);
            return canvas.toDataURL('image/png').split(',')[1];
        });
        await page.locator('#profile-photo').setInputFiles({ name: 'avatar.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64') });
        await expect(page.locator('#profile-avatar-img')).toHaveAttribute('src', /^blob:/);
        await shot('avatar-preview');
        await page.locator('#profile-name').fill('Administrador de prueba visual');
        await page.locator('#profile-save').click();
        await expect(page.locator('#page-alert')).toContainText('Perfil actualizado');
        await expect(page.locator('#profile-name')).toHaveValue('Administrador de prueba visual');
        await expect(page.locator('#profile-avatar-img')).toHaveAttribute('src', /^\/media\/users\/u\d+_[a-f0-9]{16}\.webp$/);
        await expect.poll(() => page.locator('#profile-avatar-img').evaluate(img => img.naturalWidth)).toBeGreaterThan(0);
        await shot('avatar-saved');
        const other = await browser.newContext();
        await other.request.post(origin + '/api/auth/login', { data: { identifier: 'core_admin', password: 'SecretCorePass999' } });
        for (const [width, height] of [[1600, 900], [1366, 768], [1280, 720]]) {
            await page.setViewportSize({ width, height });
            await go('/profile?tab=sessions');
            await page.locator('[data-revoke-session]').first().click();
            await expect(page.locator('#profile-confirm-accept')).toBeInViewport();
            await shot(`revoke-${width}`);
            await page.locator('#profile-confirm-cancel').click();
        }
        await page.locator('[data-revoke-session]').first().click();
        await page.locator('#profile-confirm-accept').click();
        await expect(page.locator('[data-revoke-session]')).toHaveCount(0);
        assert.equal((await other.request.get(origin + '/api/auth/me')).status(), 401);
        await other.close();
        results.checks.push('avatar preview and persistence, session dialogs at three sizes, real own-session revocation');
        await page.locator('#ix-account-toggle').click();
        await page.locator('#ix-account-logout').click();
        await expect(page).toHaveURL(origin + '/login');
        results.checks.push('LIVE profile persistence, reference mutation blocked, canonical logout');
        assert.deepEqual(results.errors, []);
        fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
        console.log(`PASS: ${results.screens.length} screens, three password dialogs, interaction checks; zero page errors.`);
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
