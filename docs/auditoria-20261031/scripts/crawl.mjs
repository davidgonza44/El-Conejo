// Fase A: recorrido por rol de cada pantalla del menú y de cada botón que abre algo (modal, panel, pestaña).
// Solo laboratorio 5099. Uso: node crawl.mjs <rol> [filtroRuta]
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
const require = createRequire(new URL('../../../package.json', import.meta.url));
const { chromium } = require('playwright');

const BASE = process.env.AUDIT_BASE_URL || 'http://127.0.0.1:5099';
const PASSWORD = process.env.AUDIT_PASSWORD;
const role = process.argv[2] || 'admin';
const only = process.argv[3] || '';
const OUT = path.join(import.meta.dirname, 'out', role);
fs.mkdirSync(OUT, { recursive: true });

// Botones que el barrido genérico no pulsa: cambian datos o cierran la sesión. Se prueban en los flujos (fase B).
const RISKY = /cerrar sesi|salir|eliminar|borrar|desactivar|activar|anular|aprobar|rechazar|confirmar|guardar|enviar|registrar|crear|emitir|recibir|cancelar orden|descartar|restablecer|restaurar|importar|subir|publicar|aplicar|resolver|solicitar|proponer|marcar|iniciar|preparar|cerrar orden|generar respaldo|ejecutar|reintentar|revocar|bloquear/i;
const BAD = /\b(undefined|null|NaN|None|Invalid Date|\[object Object\])\b/;

const report = { role, pages: [] };
const slug = (s) => s.replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, '').slice(0, 60) || 'root';

async function scan(page, scope) {
    return page.evaluate((sel) => {
        const root = sel ? document.querySelector(sel) : document.body;
        if (!root) return { bad: [], dashes: [], empties: [] };
        const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
        const bad = []; const dashes = []; const empties = [];
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const badRe = /\b(undefined|null|NaN|None|Invalid Date|\[object Object\])\b/;
        while (walker.nextNode()) {
            const node = walker.currentNode; const t = node.textContent.trim();
            const el = node.parentElement;
            if (!t || !el || !visible(el) || el.closest('script,style,template')) continue;
            if (badRe.test(t)) bad.push(t.slice(0, 120));
            if (/^[—–-]$/.test(t) || /^(—|–|-)\s*(USD|VES|Bs\.?|\$|u\.?)?$/.test(t)) {
                // Etiqueta del campo: <dt>, th de la columna, label previa o texto del contenedor.
                let label = '';
                const dd = el.closest('dd'); if (dd && dd.previousElementSibling) label = dd.previousElementSibling.textContent.trim();
                const td = el.closest('td'); if (!label && td) { const table = td.closest('table');
                    const th = table && table.querySelectorAll('thead th')[td.cellIndex]; label = th ? th.textContent.trim() : (td.dataset.label || ''); }
                if (!label) { const prev = el.previousElementSibling || el.parentElement?.previousElementSibling; label = prev ? prev.textContent.trim() : ''; }
                if (!label) label = (el.parentElement?.textContent || '').trim();
                dashes.push(label.slice(0, 60));
            }
        }
        for (const f of root.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]),select,textarea')) {
            if (!visible(f)) continue;
            const lab = (f.labels && f.labels[0] ? f.labels[0].textContent : f.getAttribute('aria-label') || f.name || f.id || '').trim();
            if (f.tagName === 'SELECT' && f.options.length === 0) empties.push(`select vacío: ${lab}`);
        }
        return { bad: [...new Set(bad)], dashes, empties };
    }, scope);
}

function summarizeDashes(list) {
    const counts = {};
    for (const l of list) counts[l] = (counts[l] || 0) + 1;
    return counts;
}

const DIALOG = '[role=dialog]:not([hidden]), dialog[open], .modal.show, .is-open[class*=drawer], [class*=drawer].is-open, [class*=modal].is-open, [class*=modal]:not([hidden])[aria-modal=true]';

async function openDialog(page) {
    return page.evaluate((sel) => {
        const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
            return r.width > 40 && r.height > 40 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
        const found = [...document.querySelectorAll(sel)].filter(visible);
        if (!found.length) return null;
        const el = found[found.length - 1];
        if (!el.id) el.id = 'aud-dialog-' + Math.random().toString(36).slice(2, 8);
        return { id: el.id, title: (el.querySelector('h1,h2,h3,[class*=title]')?.textContent || '').trim().slice(0, 80),
            text: el.innerText.slice(0, 3000) };
    }, DIALOG);
}

async function closeDialog(page) {
    await page.keyboard.press('Escape');
    await page.waitForTimeout(250);
    if (await openDialog(page)) {
        const close = page.locator(`${DIALOG} >> button:has-text("Cerrar"), ${DIALOG} >> [aria-label*="errar"], ${DIALOG} >> button:has-text("Cancelar")`).first();
        if (await close.count()) await close.click({ timeout: 1500 }).catch(() => {});
        await page.waitForTimeout(250);
    }
}

const browser = await chromium.launch({ channel: 'chrome' });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'es-VE', timezoneId: 'America/Caracas' });
const page = await context.newPage();
const net = []; const jsErrors = [];
page.on('pageerror', (e) => jsErrors.push(e.message));
page.on('console', (m) => { if (m.type() === 'error') jsErrors.push('console: ' + m.text().slice(0, 200)); });
page.on('response', (r) => { if (r.url().startsWith(BASE) && r.status() >= 400) net.push(`${r.status()} ${r.request().method()} ${r.url().replace(BASE, '')}`); });

// Inicio de sesión por la pantalla real.
await page.goto(BASE + '/login');
await page.locator('#login-identifier').fill('aud_' + role);
await page.locator('#login-password').fill(PASSWORD);
await Promise.all([page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }), page.locator('#login-submit').click()]);
const home = new URL(page.url()).pathname;

const links = await page.evaluate(() => [...new Set([...document.querySelectorAll('#ix-sidebar a[href], .ix-sidebar a[href], nav a[href]')]
    .map((a) => a.getAttribute('href')).filter((h) => h && h.startsWith('/') && !h.startsWith('/logout') && h !== '#'))]);
const targets = [...new Set([home, ...links])].filter((h) => !only || h.includes(only));
console.log(role, 'inicio', home, 'enlaces', targets.length);

for (const href of targets) {
    net.length = 0; jsErrors.length = 0;
    const entry = { href, status: null, title: '', issues: [], dialogs: [], buttons: 0 };
    report.pages.push(entry);
    const resp = await page.goto(BASE + href, { waitUntil: 'networkidle', timeout: 30000 }).catch((e) => ({ status: () => 'ERR ' + e.message.slice(0, 80) }));
    entry.status = resp?.status?.();
    await page.waitForTimeout(800);
    entry.title = await page.title();
    entry.finalUrl = page.url().replace(BASE, '');
    const s = await scan(page, null);
    entry.bad = s.bad; entry.dashes = summarizeDashes(s.dashes); entry.empties = s.empties;
    await page.screenshot({ path: path.join(OUT, slug(href) + '.png'), fullPage: true });

    // Botones visibles en el contenido (no el menú lateral).
    const buttons = await page.evaluate((risky) => {
        const re = new RegExp(risky, 'i');
        const visible = (el) => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
        const main = document.querySelector('main') || document.body;
        return [...main.querySelectorAll('button, [role=button], a.po-btn[href^="#"], [data-action], [role=tab]')]
            .filter((b) => visible(b) && !b.closest('#ix-sidebar') && !b.disabled)
            .map((b, i) => { b.dataset.audIdx = i; return { i, text: (b.innerText || b.getAttribute('aria-label') || b.title || '').trim().replace(/\s+/g, ' ').slice(0, 60),
                type: b.getAttribute('type') || '', risky: re.test((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')) }; });
    }, RISKY.source);
    entry.buttons = buttons.length;
    entry.skipped = buttons.filter((b) => b.risky).map((b) => b.text);
    const seen = new Set();
    for (const b of buttons.filter((x) => !x.risky)) {
        const key = b.text || `#${b.i}`;
        if (seen.has(key) && /^(ver|detalle|revisar|editar|abrir|)$/i.test(key.split(' ')[0]) === false) continue;
        if (seen.has(key) && seen.size > 25) continue;
        seen.add(key);
        if (new URL(page.url()).pathname !== new URL(BASE + href).pathname) {
            await page.goto(BASE + href, { waitUntil: 'networkidle' }).catch(() => {}); await page.waitForTimeout(500);
            await page.evaluate((risky) => { const re = new RegExp(risky, 'i'); const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
                const main = document.querySelector('main') || document.body;
                [...main.querySelectorAll('button, [role=button], a.po-btn[href^="#"], [data-action], [role=tab]')].filter((x) => visible(x) && !x.closest('#ix-sidebar') && !x.disabled).forEach((x, i) => { x.dataset.audIdx = i; }); }, RISKY.source);
        }
        const loc = page.locator(`[data-aud-idx="${b.i}"]`).first();
        if (!(await loc.count()) || !(await loc.isVisible().catch(() => false))) continue;
        const errBefore = jsErrors.length; const netBefore = net.length;
        await loc.click({ timeout: 2500 }).catch((e) => entry.issues.push(`no se pudo pulsar "${key}": ${e.message.split('\n')[0].slice(0, 80)}`));
        await page.waitForTimeout(900);
        const dlg = await openDialog(page);
        const after = { button: key, newJsErrors: jsErrors.slice(errBefore), newNet: net.slice(netBefore) };
        if (dlg) {
            const d = await scan(page, '#' + dlg.id);
            const file = `${slug(href)}__${slug(key)}.png`;
            await page.locator('#' + dlg.id).screenshot({ path: path.join(OUT, file) }).catch(() => page.screenshot({ path: path.join(OUT, file) }));
            entry.dialogs.push({ ...after, title: dlg.title, dashes: summarizeDashes(d.dashes), bad: d.bad, empties: d.empties, shot: file, text: dlg.text.slice(0, 800) });
            await closeDialog(page);
        } else if (after.newJsErrors.length || after.newNet.length) {
            entry.issues.push(after);
        }
    }
    entry.jsErrors = [...jsErrors]; entry.net = [...net];
    console.log(`${entry.status} ${href} botones=${entry.buttons} dialogos=${entry.dialogs.length} guiones=${Object.values(entry.dashes).reduce((a, b) => a + b, 0)} malos=${entry.bad.length} js=${entry.jsErrors.length} http=${entry.net.length}`);
}
fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 2));
await browser.close();
