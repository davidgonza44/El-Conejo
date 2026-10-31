import { start, stop, login, go, shot, fields, flushErrors } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Conciliación';
await start();
for (const role of ['inventario', 'admin']) {
const p = await login(role, OUT);
await go(p, '/reconciliation');
console.log(`\n### ${role}\n` + (await p.locator('main').innerText()).replace(/\n+/g, ' | ').slice(0, 1800));
console.log('botones', (await p.locator('main button:visible, main a.po-btn:visible').allInnerTexts()).filter(Boolean));
await shot(p, 'inicio');
flushErrors(p, A);
}
await stop(OUT);
