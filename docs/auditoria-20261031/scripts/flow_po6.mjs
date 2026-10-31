import { start, stop, login, go, shot, fields, flushErrors } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Productos-proveedor';
await start();
const as = await login('asistente', OUT);
await go(as, '/products');
await as.locator('tr', { hasText: 'AUD-CAB' }).first().locator('button, a').last().click(); await as.waitForTimeout(1200);
const d = as.locator('#pl-dialog');
console.log('botones detalle', await d.locator('button:visible').allInnerTexts());
const sec = d.locator('text=Proveedores del producto').first();
console.log((await d.innerText()).slice(-1500));
console.log(JSON.stringify(await fields(as, '#pl-dialog')));
await shot(as, 'detalle_asistente', d);
// formulario de proveedor dentro del detalle
const form = d.locator('.ps-add').first();
if (await form.count()) {
  const sel = form.locator('select').first(); await sel.selectOption({ index: 1 });
  const nums = form.locator('input[type=number]');
  await nums.nth(0).fill('2.30'); await form.locator('select').nth(1).selectOption('USD');
  await form.locator('input[type=text]').first().fill('CAB-12-PROV');
  await nums.nth(1).fill('5'); await nums.nth(2).fill('100');
  await form.locator('input[type=checkbox]').check();
  await form.locator('button').click(); await as.waitForTimeout(1500);
  console.log('nativo', as.audit.lastNative || '', '| texto tras agregar:', (await d.innerText()).match(/Proveedores del producto[\s\S]{0,600}/)?.[0]);
  await shot(as, 'tras_agregar_prov', d);
}
for (const n of flushErrors(as, A)) console.log(n.slice(0, 200));
await stop(OUT);
