import { start, stop, login, go, shot, flushErrors } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Configuración';
await start();
const ad = await login('admin', OUT);
await go(ad, '/settings');
const tabs = await ad.evaluate(() => [...document.querySelectorAll('a[href*="tab="]')].map((a) => a.getAttribute('href')));
console.log('pestañas', [...new Set(tabs)]);
for (const t of [...new Set(tabs)]) {
  await go(ad, t.startsWith('/') ? t : '/settings' + t);
  const dead = await ad.evaluate(() => [...document.querySelectorAll('main button, main a, main input, main select')].filter((b) => /no conectad|no disponible/i.test(b.title || '') || b.getAttribute('aria-disabled') === 'true' || b.disabled).map((b) => (b.textContent.trim() || b.name || b.id || b.placeholder).slice(0, 40) + ' [' + (b.title || (b.disabled ? 'disabled' : 'aria-disabled')) + ']'));
  console.log(`\n## ${t}\n  botones: ${(await ad.locator('main button:visible').allInnerTexts()).filter(Boolean).join(' | ')}\n  inertes: ${dead.join(' | ')}`);
  await shot(ad, t.replace(/\W+/g, '_'));
}
flushErrors(ad, A);
await stop(OUT);
