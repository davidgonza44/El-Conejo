import { start, stop, login, go, shot, fields, flushErrors, TAG } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Configuración';
await start();
const ad = await login('admin', OUT);
await go(ad, '/settings?tab=general');
console.log(JSON.stringify((await fields(ad, 'main')).filter((f) => f.id !== 'ix-search-input').map((f) => `${f.id || f.name}|${f.label}|${f.value}|disabled?`)));
const name = ad.locator('main input').filter({ has: ad.locator('xpath=.') }).first();
const editable = await ad.evaluate(() => [...document.querySelectorAll('main input, main select')].map((e) => `${e.id}:${e.disabled || e.readOnly ? 'RO' : 'RW'}`));
console.log('editables', editable.join(' '));
// usuarios
await go(ad, '/settings?tab=users');
await ad.getByRole('button', { name: 'Nuevo usuario' }).click(); await ad.waitForTimeout(900);
await shot(ad, 'tras_nuevo_usuario'); const d = ad.locator('dialog[open], [role=dialog]:visible, .modal.show, [class*=drawer]:visible, form:visible').last();
console.log('NUEVO USUARIO:', (await d.innerText()).replace(/\n+/g, ' | ').slice(0, 900));
console.log(JSON.stringify((await fields(ad, 'main')).map((f) => `${f.id || f.name}|${f.label}|${f.type}|req=${f.required}|${(f.options || []).join(';')}`)));
await shot(ad, 'nuevo_usuario', d);
await stop(OUT);
