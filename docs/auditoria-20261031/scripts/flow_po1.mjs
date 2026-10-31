import { start, stop, login, go, shot, fields, flushErrors } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Compras';
await start();
const p = await login('inventario', OUT);
await go(p, '/purchases/requests');
await shot(p, 'solicitudes');
console.log((await p.locator('main').innerText()).slice(0, 1500));
const btns = await p.locator('main button:visible').allInnerTexts(); console.log('botones', btns);
const nb = p.locator('main button:visible', { hasText: /Nueva|Solicitar|Crear/ }).first();
if (await nb.count()) { await nb.click(); await p.waitForTimeout(800);
  console.log(JSON.stringify(await fields(p, 'dialog[open], [role=dialog]:visible')));
  await shot(p, 'nueva_solicitud', p.locator('dialog[open], [role=dialog]:visible').last()); }
flushErrors(p, A);
await stop(OUT);
