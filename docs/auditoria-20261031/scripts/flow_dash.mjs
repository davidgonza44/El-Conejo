import { start, stop, login, go, flushErrors, BASE } from './lib.mjs';
const OUT = 'out/flows';
await start();
for (const role of ['admin', 'vendedor', 'inventario']) {
  const p = await login(role, OUT);
  await go(p, '/dashboard');
  const links = await p.evaluate(() => [...document.querySelectorAll('main a[href], main button')].filter((a) => /Nuevo producto|Registrar venta|Registrar compra|Ver todos/.test(a.textContent)).map((a) => a.textContent.trim().replace(/\s+/g, ' ').slice(0, 30) + ' → ' + (a.getAttribute('href') || a.dataset.href || '(botón)')));
  console.log(`\n## ${role} ${p.url()}`); console.log(links.join('\n'));
  for (const l of links) { const href = l.split(' → ')[1]; if (href && href.startsWith('/')) { const r = await p.goto(BASE + href); console.log('   ', href, '→', r.status(), p.url().replace(BASE, ''), (await p.title())); } }
}
await stop(OUT);
