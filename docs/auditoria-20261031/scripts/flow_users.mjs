import { start, stop, login, go, shot, flushErrors, TAG, BASE } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Usuarios';
await start();
const ad = await login('admin', OUT);
await go(ad, '/settings?tab=users');
const saveBtn = () => ad.locator('main button:visible', { hasText: /Crear usuario|Guardar usuario|Guardar cambios del usuario|Guardar$/ }).first();
async function attempt(label, v) {
  await ad.getByRole('button', { name: 'Nuevo usuario' }).click(); await ad.waitForTimeout(700);
  const map = { name: '#st-user-name', email: '#st-user-email', username: '#st-user-username', password: '#st-user-password' };
  for (const [k, sel] of Object.entries(map)) await ad.fill(sel, v[k] ?? '');
  if (v.role) await ad.selectOption('#st-user-role', { label: v.role });
  const btns = await ad.locator('main button:visible').allInnerTexts();
  const b = ad.locator('main button:visible', { hasText: /Crear|Guardar usuario/ }).first();
  if (!(await b.count())) { console.log('botones visibles:', btns.filter(Boolean).join(' | ')); return; }
  await b.click(); await ad.waitForTimeout(1300);
  const msgs = await ad.locator('[role=alert]:visible, [role=status]:visible, .st-error:visible, [class*=feedback]:visible').allInnerTexts();
  console.log(`[${label}] msgs=${msgs.join(' / ')} nativo=${ad.audit.lastNative || ''}`, flushErrors(ad, A).map((x) => x.slice(0, 170)));
  ad.audit.lastNative = '';
  await shot(ad, label);
}
await attempt('vacio', {});
await attempt('correo_malo', { name: 'Pedro Prueba', email: 'pedro@', username: 'pprueba' + TAG.toLowerCase(), password: 'Clave#2026segura', role: 'Vendedor' });
await attempt('clave_debil', { name: 'Pedro Prueba', email: 'pedro' + TAG + '@example.invalid', username: 'pprueba' + TAG.toLowerCase(), password: '123', role: 'Vendedor' });
await attempt('usuario_dup', { name: 'Pedro Prueba', email: 'pedro2' + TAG + '@example.invalid', username: 'aud_admin', password: 'Clave#2026segura', role: 'Vendedor' });
await attempt('valido', { name: 'Pedro Prueba ' + TAG, email: 'pedro' + TAG + '@example.invalid', username: 'pprueba' + TAG.toLowerCase(), password: 'Clave#2026segura', role: 'Vendedor' });
// login con el nuevo usuario
const ctx = await (await import('./lib.mjs')).chromium;
const p2 = await ad.context().browser().newPage();
await p2.goto(BASE + '/login'); await p2.fill('#login-identifier', 'pprueba' + TAG.toLowerCase()); await p2.fill('#login-password', 'Clave#2026segura');
await p2.click('#login-submit'); await p2.waitForTimeout(2500);
console.log('login nuevo usuario ->', p2.url(), (await p2.locator('[role=alert]:visible, .alert:visible').allInnerTexts()).join('/'));
await stop(OUT);
