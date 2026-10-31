import { start, stop, login, go, shot } from './lib.mjs';
const OUT = 'out/flows';
await start();
const p = await login('admin', OUT);
for (const name of ['Registrar venta', 'Registrar compra', 'Nuevo producto']) {
  await go(p, '/dashboard');
  const el = p.locator('main').getByText(name, { exact: true }).first();
  const info = await el.evaluate((e) => { const c = e.closest('a,button,[role=button]'); return c ? `${c.tagName} href=${c.getAttribute('href')} disabled=${c.disabled || c.getAttribute('aria-disabled')} title=${c.title}` : 'sin control'; });
  await el.click().catch(() => {}); await p.waitForTimeout(1200);
  console.log(`[${name}] ${info} → ${p.url()} | diálogo: ${(await p.locator('dialog[open]').count()) ? (await p.locator('dialog[open]').last().innerText()).slice(0, 80) : 'no'} | nuevo producto visible: ${await p.getByRole('button', { name: 'Nuevo producto' }).isVisible().catch(() => false)}`);
}
await stop(OUT);
