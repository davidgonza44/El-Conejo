import { start, stop, login, go, shot, flushErrors } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Reportes';
await start();
const ad = await login('admin', OUT);
for (const href of ['/dashboard', '/reportes', '/reports/sales', '/inventory/summary', '/predictive/demand', '/predictive/critical']) {
  await go(ad, href); await ad.waitForTimeout(1200);
  await ad.screenshot({ path: `out/flows/rep_${href.replace(/\W+/g, '_')}.png` });
  console.log(`\n## ${href}\n` + (await ad.locator('main').innerText()).replace(/\n+/g, ' | ').slice(0, 1400));
}
flushErrors(ad, A);
await stop(OUT);
