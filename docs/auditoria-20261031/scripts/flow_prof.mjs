import { start, stop, login, go, shot, fields, flushErrors } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Perfil';
await start();
const p = await login('vendedor', OUT);
await go(p, '/profile');
console.log(JSON.stringify((await fields(p, 'main')).filter((f) => f.id !== 'ix-search-input').map((f) => `${f.id}|${f.label}|${f.value}|max=${f.maxlength}`)));
const phone = p.locator('main input[type=tel], main #profile-phone, main [name=phone]').first();
await phone.fill('abc'); await p.getByRole('button', { name: 'Guardar cambios' }).first().click(); await p.waitForTimeout(1200);
console.log('tel inválido ->', (await p.locator('[role=alert]:visible, [role=status]:visible').allInnerTexts()).join(' / '), flushErrors(p, A).map((x) => x.slice(0, 160)));
await phone.fill('0412-5551234'); await p.getByRole('button', { name: 'Guardar cambios' }).first().click(); await p.waitForTimeout(1200);
console.log('tel válido ->', (await p.locator('[role=alert]:visible, [role=status]:visible').allInnerTexts()).join(' / '), flushErrors(p, A));
await p.reload(); await p.waitForTimeout(800);
console.log('tras recargar tel =', await phone.inputValue());
// notificaciones
await p.locator('button[aria-label*="otificac"], .ix-bell, [data-notifications]').first().click().catch(() => {}); await p.waitForTimeout(1000);
console.log('NOTIFICACIONES:', (await p.locator('[role=dialog]:visible, dialog[open], [class*=notif]:visible').last().innerText().catch(() => 'n/a')).replace(/\n+/g, ' | ').slice(0, 800));
await shot(p, 'notif');
await stop(OUT);
