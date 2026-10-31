import { start, stop, login, go, shot, fields, check, finding, flushErrors, TAG } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Productos';
await start();
const p = await login('asistente', OUT);
await go(p, '/products');
await p.getByRole('button', { name: /Nuevo producto/ }).click();
await p.waitForTimeout(800);
const D = p.locator('#np-drawer');
console.log(JSON.stringify(await fields(p, '#np-drawer')));
await shot(p, 'np_vacio', D);
// 1. Guardar vacío
await p.locator('#np-save').click(); await p.waitForTimeout(500);
await shot(p, 'np_guardar_vacio', D);
const errs = await D.locator('.np-error:not([hidden])').allTextContents();
console.log('errores visibles al guardar vacío:', errs);
// 2. Valores inválidos
await p.fill('#np-name', 'Cable THW 12 AWG ' + TAG);
await p.fill('#np-sku', 'AUD-CAB-' + TAG);
const cats = await p.locator('#np-category option').allTextContents(); console.log('categorías', cats);
await p.selectOption('#np-category', { index: 1 });
await p.fill('#np-brand', 'Cabel');
console.log('unidades', await p.locator('#np-unit option').allTextContents());
await p.selectOption('#np-unit', { label: 'Metro' });
await p.fill('#np-price', '-5'); await p.fill('#np-stock', '2.5'); await p.fill('#np-minimum', '-1');
await p.locator('#np-save').click(); await p.waitForTimeout(1200);
await shot(p, 'np_invalidos', D);
const fb = await p.locator('#np-feedback').textContent().catch(() => '');
console.log('feedback inválidos:', fb, D.locator('.np-error:not([hidden])') && await D.locator('.np-error:not([hidden])').allTextContents());
console.log('net', flushErrors(p, A));
// 3. Valores válidos completos
await p.fill('#np-price', '3.75'); await p.selectOption('#np-price-currency', 'USD');
if (await p.locator('#np-cost').count()) { await p.fill('#np-cost', '2.10'); await p.selectOption('#np-cost-currency', 'VES').catch(() => {}); }
await p.fill('#np-stock', '150'); await p.fill('#np-minimum', '20');
await p.fill('#np-description', 'Cable de cobre calibre 12 para instalaciones residenciales. Rollo de 100 m.');
await p.setInputFiles('#np-image', 'prod.png');
// proveedor
const sup = p.locator('#np-supplier-form select').first();
console.log('proveedores ofrecidos', await sup.locator('option').allTextContents());
await sup.selectOption({ index: 1 });
const inputs = p.locator('#np-supplier-form input');
console.log('inputs proveedor', await inputs.count());
await shot(p, 'np_completo_antes', D);
await p.locator('#np-supplier-form button').click(); await p.waitForTimeout(500);
console.log('lista proveedores staged:', await p.locator('#np-supplier-list').innerText());
console.log('feedback tras agregar proveedor:', await p.locator('#np-feedback').textContent());
await p.locator('#np-save').click(); await p.waitForTimeout(2000);
await shot(p, 'np_tras_guardar');
console.log('feedback guardar:', await p.locator('#np-feedback').textContent().catch(() => ''), 'drawer abierto:', await D.isVisible());
console.log('net', flushErrors(p, A));
await stop(OUT);
