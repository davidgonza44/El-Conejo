import { start, stop, login, go, shot, fields, flushErrors } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Compras';
await start();
const ad = await login('admin', OUT);
await go(ad, '/purchases?detail=4');
let d = ad.locator('dialog[open], [role=dialog]:visible').last();
await d.getByRole('button', { name: 'Aprobar' }).click(); await ad.waitForTimeout(1500);
const conf = ad.locator('dialog[open], [role=dialog]:visible').last();
console.log('tras pulsar Aprobar:', (await conf.innerText()).replace(/\n+/g, ' | ').slice(0, 500), '| nativo', ad.audit.lastNative || '');
const again = conf.getByRole('button', { name: /Aprobar|Confirmar/ });
if (await again.count()) { await again.last().click(); await ad.waitForTimeout(1500); }
console.log('estado:', (await ad.locator('tbody tr', { hasText: 'OC-000004' }).innerText()).replace(/\s+/g, ' '), flushErrors(ad, A));
const inv = await login('inventario', OUT);
await go(inv, '/purchases/receptions');
await shot(inv, 'recepciones');
console.log('botones', await inv.locator('main button:visible').allInnerTexts());
console.log((await inv.locator('main').innerText()).slice(0, 1200));
await stop(OUT);
