import { start, stop, login, go, shot, flushErrors } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Chatbot';
await start();
const p = await login('admin', OUT);
await go(p, '/chatbot');
const input = p.locator('main textarea:visible, main input[type=text]:visible').last();
for (const q of ['¿Cuánto stock hay del Cable THW 12 AWG?', '¿Qué productos están en bajo stock o agotados?', '¿Cuál es el precio del E2E CLAVO?', '¿Cuántas órdenes de compra están pendientes de aprobación?']) {
  const before = await p.locator('main').innerText();
  await input.fill(q); await p.keyboard.press('Enter');
  await p.waitForTimeout(9000);
  const after = await p.locator('main').innerText();
  console.log(`\nP: ${q}\nR: ${after.slice(before.length - 50).replace(/\n+/g, ' | ').slice(0, 600)}`);
}
await shot(p, 'conversacion');
console.log(flushErrors(p, A).map((x) => x.slice(0, 200)));
await stop(OUT);
