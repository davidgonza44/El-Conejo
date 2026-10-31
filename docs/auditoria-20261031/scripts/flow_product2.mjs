import { start, stop, login, go, shot, flushErrors, TAG } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Productos';
await start();
const p = await login('asistente', OUT);
await go(p, '/products');
let seq = 0;
async function attempt(label, values) {
    await p.getByRole('button', { name: /Nuevo producto/ }).click(); await p.waitForTimeout(600);
    const base = { name: 'Prueba ' + TAG, sku: 'AUD-V-' + TAG + '-' + (++seq), price: '1', cost: '1', stock: '1', minimum: '0', ...values };
    await p.fill('#np-name', base.name); await p.fill('#np-sku', base.sku);
    await p.selectOption('#np-category', { index: 1 }); await p.selectOption('#np-unit', { label: 'Unidad' });
    await p.selectOption('#np-price-currency', 'USD'); await p.selectOption('#np-cost-currency', 'USD');
    for (const k of ['price', 'cost', 'stock', 'minimum']) await p.fill('#np-' + k, base[k]);
    if (values.desc) await p.fill('#np-description', values.desc);
    if (values.service) await p.check('#np-service');
    await p.locator('#np-save').click(); await p.waitForTimeout(1200);
    const fb = await p.locator('#np-feedback').evaluate((e) => ({ text: e.textContent.trim(), visible: !e.hidden && e.getBoundingClientRect().height > 0, top: Math.round(e.getBoundingClientRect().top) })).catch(() => null);
    const open = await p.locator('#np-drawer').isVisible();
    console.log(`[${label}] abierto=${open} feedback=${JSON.stringify(fb)} errores=${JSON.stringify(await p.locator('#np-drawer .np-error:not([hidden])').allTextContents())}`);
    if (open) { await shot(p, 'np_' + label.replace(/\W+/g, '_'), p.locator('#np-drawer')); await p.locator('#np-drawer [data-np-close]').first().click(); await p.waitForTimeout(400); }
    flushErrors(p, A);
}
await attempt('precio negativo', { price: '-5' });
await attempt('stock decimal', { stock: '2.5' });
await attempt('minimo negativo', { minimum: '-1' });
await attempt('precio 3 decimales', { price: '1.999' });
await attempt('precio enorme', { price: '99999999999999' });
await attempt('nombre 300 chars', { name: 'X'.repeat(300) });
await attempt('sku duplicado', { sku: 'E2E-CLAVO-3DFAAF' });
await attempt('servicio con stock', { service: true, stock: '5' });
await attempt('nombre solo espacios', { name: '     ' });
await stop(OUT);
