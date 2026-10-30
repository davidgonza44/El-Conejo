// Fixtures comunes: cada prueba falla si la página lanza errores JS o si una petición
// del propio servidor responde 5xx. Los 4xx esperados (403 por rol) se validan en cada spec.
import { test as base, expect } from '@playwright/test';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { authFile } from './users.mjs';

// Tabler, Chart.js y las fuentes vienen de CDN; se guardan en disco tras la primera descarga
// para que la suite no dependa de la red en cada página (mismas URLs versionadas).
const CDN = /^https:\/\/(cdn\.jsdelivr\.net|fonts\.googleapis\.com|fonts\.gstatic\.com)\//;
const CDN_CACHE = path.resolve(import.meta.dirname, '..', '..', '.tmp', 'e2e-cdn-cache');

async function cachedCdn(route) {
    const key = crypto.createHash('sha1').update(route.request().url()).digest('hex');
    const file = path.join(CDN_CACHE, key);
    if (fs.existsSync(file)) {
        const meta = JSON.parse(fs.readFileSync(`${file}.json`, 'utf8'));
        return route.fulfill({ status: 200, contentType: meta.contentType, body: fs.readFileSync(file) });
    }
    const response = await route.fetch();
    if (response.ok()) {
        fs.mkdirSync(CDN_CACHE, { recursive: true });
        fs.writeFileSync(file, await response.body());
        fs.writeFileSync(`${file}.json`, JSON.stringify({ contentType: response.headers()['content-type'] }));
    }
    return route.fulfill({ response });
}

export const test = base.extend({
    context: async ({ context }, use) => {
        await context.route(CDN, cachedCdn);
        await use(context);
    },
    pageIssues: [async ({ page, baseURL }, use) => {
        const issues = [];
        page.on('pageerror', (error) => issues.push(`Error JS: ${error.message}`));
        page.on('response', (response) => {
            if (response.status() >= 500 && response.url().startsWith(baseURL)) {
                issues.push(`HTTP ${response.status()} ${response.request().method()} ${response.url()}`);
            }
        });
        await use(issues);
        expect(issues, 'La página no debe lanzar errores JS ni respuestas 5xx').toEqual([]);
    }, { auto: true }],
});

/** Sesión guardada por global-setup para un rol: ``test.use(as('admin'))``. */
export const as = (role) => ({ storageState: authFile(role) });

/**
 * Cliente de la API con la sesión del ``page`` y su token CSRF (Flask-WTF lo liga a la sesión).
 * ``tokenPage`` es una pantalla del rol que publica el token en su contexto JSON o en data-csrf.
 */
export async function api(page, tokenPage) {
    await page.goto(tokenPage);
    const csrf = await page.evaluate(() => {
        for (const script of document.querySelectorAll('script[type="application/json"]')) {
            try { const data = JSON.parse(script.textContent); if (data.csrf) return data.csrf; } catch { /* otro JSON */ }
        }
        return document.querySelector('[data-csrf]')?.dataset.csrf
            || document.querySelector('meta[name="csrf-token"]')?.content || '';
    });
    expect(csrf, `token CSRF en ${tokenPage}`).not.toBe('');
    const call = async (method, path, data) => {
        const response = await page.request.fetch(path, { method, data, headers: { 'X-CSRFToken': csrf } });
        const body = await response.json().catch(() => ({}));
        return { status: response.status(), body };
    };
    return { get: (path) => call('GET', path), post: (path, data = {}) => call('POST', path, data),
        patch: (path, data = {}) => call('PATCH', path, data) };
}

export { expect };
