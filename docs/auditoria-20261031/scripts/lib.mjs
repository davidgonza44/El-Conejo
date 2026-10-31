// Utilidades comunes de la auditoría en el navegador (solo laboratorio 5099).
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
const require = createRequire(new URL('../../../package.json', import.meta.url));
export const { chromium } = require('playwright');

export const BASE = process.env.AUDIT_BASE_URL || 'http://127.0.0.1:5099';
export const PASSWORD = process.env.AUDIT_PASSWORD;
export const TAG = Date.now().toString(36).slice(-4).toUpperCase();
export const findings = [];

let browser;
export async function start() { browser = await chromium.launch({ channel: 'chrome' }); return browser; }
export async function stop(outDir) {
    fs.writeFileSync(path.join(outDir, 'findings.json'), JSON.stringify(findings, null, 2));
    await browser.close();
}

/** Sesión real por la pantalla de login; devuelve la página con registro de errores. */
export async function login(role, outDir) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'es-VE', timezoneId: 'America/Caracas' });
    const page = await context.newPage();
    page.audit = { role, outDir, js: [], net: [], n: 0 };
    page.on('pageerror', (e) => page.audit.js.push(e.message));
    page.on('response', async (r) => {
        if (r.url().startsWith(BASE) && r.status() >= 400) {
            let body = ''; try { body = (await r.text()).slice(0, 200); } catch { /* sin cuerpo */ }
            page.audit.net.push(`${r.status()} ${r.request().method()} ${r.url().replace(BASE, '')} ${body}`);
        }
    });
    page.on('dialog', (d) => { page.audit.lastNative = d.message(); d.accept(); });
    await page.goto(BASE + '/login');
    await page.locator('#login-identifier').fill('aud_' + role);
    await page.locator('#login-password').fill(PASSWORD);
    await Promise.all([page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }), page.locator('#login-submit').click()]);
    return page;
}

export async function go(page, href) {
    await page.goto(BASE + href, { waitUntil: 'networkidle' }).catch(async () => { await page.waitForTimeout(1500); await page.goto(BASE + href, { waitUntil: 'networkidle' }); });
    await page.waitForTimeout(600);
}

export async function shot(page, name, locator) {
    page.audit.n += 1;
    const file = path.join(page.audit.outDir, path.basename(process.argv[1], '.mjs') + '_' + String(page.audit.n).padStart(2, '0') + '_' + page.audit.role + '_' + name + '.png');
    if (locator) await locator.screenshot({ path: file }).catch(() => page.screenshot({ path: file }));
    else await page.screenshot({ path: file, fullPage: true });
    return file;
}

export function finding(area, severity, what, evidence = '') {
    findings.push({ area, severity, what, evidence });
    console.log(`[${severity}] ${area}: ${what}${evidence ? ' | ' + evidence : ''}`);
}

export function check(area, cond, what, evidence = '', severity = 'media') {
    if (!cond) finding(area, severity, what, evidence);
    else console.log(`  ok  ${area}: ${what}`);
    return cond;
}

/** Inventario de los campos visibles de un contenedor: etiqueta, tipo, obligatorio, límites. */
export async function fields(page, scope) {
    return page.locator(scope).first().evaluate((root) => {
        const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
        return [...root.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(vis).map((f) => ({
            id: f.id, name: f.name, tag: f.tagName.toLowerCase(), type: f.type,
            label: ((f.labels && f.labels[0]?.textContent) || f.getAttribute('aria-label') || f.placeholder || '').trim().replace(/\s+/g, ' ').slice(0, 60),
            required: f.required, min: f.min, max: f.max, step: f.step, maxlength: f.maxLength > 0 ? f.maxLength : '',
            value: f.type === 'password' ? '***' : (f.tagName === 'SELECT' ? f.options[f.selectedIndex]?.text : f.value),
            options: f.tagName === 'SELECT' ? [...f.options].map((o) => o.text).slice(0, 12) : undefined,
        }));
    });
}

/** Texto visible de un contenedor y las etiquetas cuyo valor es un guion. */
export async function readPanel(page, scope) {
    return page.locator(scope).first().evaluate((root) => {
        const pairs = [];
        for (const dt of root.querySelectorAll('dt')) pairs.push([dt.textContent.trim(), dt.nextElementSibling?.textContent.trim() ?? '']);
        return { text: root.innerText, pairs, dashes: pairs.filter(([, v]) => /^[—–-]?$/.test(v)).map(([k]) => k) };
    });
}

export function flushErrors(page, area) {
    const { js, net } = page.audit;
    for (const e of js) finding(area, 'alta', 'error JavaScript en la página', e);
    for (const n of net) console.log(`  http ${area}: ${n}`);
    const out = [...net]; js.length = 0; net.length = 0; return out;
}
