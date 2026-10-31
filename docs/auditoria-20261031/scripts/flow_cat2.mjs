import { start, stop, login, go, shot, flushErrors, TAG } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Categorías';
await start();
const as = await login('asistente', OUT);
await go(as, '/categories');
await as.locator('main button:visible', { hasText: 'Nueva categoría' }).click(); await as.waitForTimeout(900);
const d = as.locator('dialog[open]').last();
await as.fill('#cl-name', 'Plomería ' + TAG);
await as.fill('#cl-description', 'Tuberías, conexiones y grifería.');
const sw = d.locator('[data-cl-color]').nth(1); console.log('color', await sw.getAttribute('aria-label'), await sw.getAttribute('data-cl-tone')); await sw.click();
const icons = d.locator('[data-cl-icon], .cl-icons button, [aria-label^="Icono"]'); console.log('iconos', await icons.count());
if (await icons.count()) { const ic = icons.nth(6); console.log('icono', await ic.getAttribute('aria-label'), await ic.getAttribute('data-cl-icon')); await ic.click(); }
await shot(as, 'llena', d);
await d.getByRole('button', { name: 'Proponer categoría' }).click(); await as.waitForTimeout(1500);
console.log(flushErrors(as, A));
await stop(OUT);
