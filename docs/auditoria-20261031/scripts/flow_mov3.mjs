import { start, stop, login, go, shot, flushErrors } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Movimientos';
await start();
for (const role of ['inventario', 'admin']) {
const p = await login(role, OUT);
await go(p, '/inventory');
await p.getByRole('button', { name: /Nuevo movimiento/ }).click(); await p.waitForTimeout(700);
console.log(role, 'responsables:', await p.locator('#mv-drawer #mv-user option').allTextContents(), 'disabled', await p.locator('#mv-drawer #mv-user').isDisabled());
console.log(role, 'tipos:', await p.locator('#mv-drawer #mv-kind option').allTextContents());
await p.locator('#mv-drawer [data-mv-close]').first().click(); await p.waitForTimeout(300);
const row = p.locator('tbody tr', { hasText: 'MOV-000009' }).first();
console.log('acciones fila:', await row.locator('button, a').evaluateAll((b) => b.map((x) => x.getAttribute('aria-label') || x.textContent.trim())));
await row.click(); await p.waitForTimeout(900);
let D = p.locator('#mv-drawer');
if (!(await D.isVisible())) { await row.locator('button, a').first().click(); await p.waitForTimeout(900); }
console.log(role, 'DETALLE MOV-000009:\n' + await D.innerText().catch(() => 'no abre'));
await shot(p, 'detalle_mov9', D);
flushErrors(p, A);
}
await stop(OUT);
