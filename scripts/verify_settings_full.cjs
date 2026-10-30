// Configuración completa: cada control real de Usuarios/Seguridad/Notificaciones/Respaldo a través de la UI,
// cero pageerrors, ?ref=1 sin escrituras y sin desborde horizontal en 1600x900 / 1366x768 / 1280x720.
// Ejecutar tras: python scripts/migrate_settings_full.py --database ferreteria_conejo_e2e
//                python -B scripts/serve_settings_e2e.py   (solo ferreteria_conejo_e2e)
const { chromium } = require('@playwright/test');
const crypto = require('crypto');
const fs = require('fs');
const assert = require('assert/strict');
const path = require('path');

const output = path.join('.tmp', 'settings-full-validation');
fs.mkdirSync(output, { recursive: true });
const base = `http://127.0.0.1:${process.env.SETTINGS_E2E_PORT || '5026'}`;
const liveTabs = ['general', 'notifications', 'users', 'security', 'backup'];
const allTabs = ['general', 'predictive', 'notifications', 'users', 'security', 'integrations', 'backup'];
const viewports = [[1366, 768], [1280, 720]];

/* TOTP RFC 6238 (SHA-1, 30 s, 6 dígitos) sin dependencias: la suite actúa como la app de autenticación. */
function base32Decode(text) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = ''; for (const ch of text.replace(/=+$/, '').toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, '0');
  const bytes = []; for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}
function totp(secret, offsetSteps = 0) {
  const counter = Buffer.alloc(8); counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000) + offsetSteps));
  const hmac = crypto.createHmac('sha1', base32Decode(secret)).update(counter).digest();
  const o = hmac[19] & 0xf;
  return String(((hmac.readUInt32BE(o) & 0x7fffffff) % 1e6)).padStart(6, '0');
}

const overflow = (page) => page.evaluate(() => ({
  scrollWidth: document.documentElement.scrollWidth, innerWidth,
  clipped: [...document.querySelectorAll('.st-card, .st-users-grid, .st-backup-table, .st-user-row, .st-notification-matrix')]
    .filter((e) => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX === 'visible').map((e) => e.className),
}));

(async () => {
  const ctx = await (await fetch(`${base}/__settings_e2e__/context`)).json();
  assert.equal(ctx.database, 'ferreteria_conejo_e2e');
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
  const login = await context.request.post(`${base}/api/auth/login`, { data: { identifier: ctx.username, password: ctx.password } });
  assert.equal(login.status(), 200);
  const page = await context.newPage();
  const pageErrors = [];
  const expected = new Set(); // rutas cuyo 4xx provoca la propia suite (validación honesta), nunca 5xx
  page.on('pageerror', (e) => pageErrors.push({ url: page.url(), message: e.message }));
  const mediaMiss = (url) => /\/media\//.test(url || '');
  page.on('console', (m) => {
    if (m.type() !== 'error' || mediaMiss(m.location()?.url)) return;
    // El navegador registra "Failed to load resource … 4xx" también para las respuestas 4xx que la suite provoca a propósito
    // (validación honesta); las 5xx y los 4xx no esperados ya quedan registrados por el manejador de 'response'.
    if (/Failed to load resource: the server responded with a status of 4\d\d/.test(m.text())) return;
    pageErrors.push({ url: page.url(), message: m.text() });
  });
  page.on('response', (r) => {
    const p = new URL(r.url()).pathname;
    if (r.status() >= 500 || (r.status() >= 400 && !mediaMiss(r.url()) && !expected.has(p))) pageErrors.push({ url: page.url(), message: `${r.status()} ${p}` });
  });
  const checks = [];
  const note = (name, ok, detail = '') => { checks.push({ name, ok, detail }); console.log(`${ok ? '[OK]' : '[FALLO]'} ${name}${detail ? ' — ' + detail : ''}`); assert(ok, `${name} ${detail}`); };
  const toast = async (pattern) => {
    const el = page.locator('.st-toast:not([hidden])', { hasText: pattern });
    let text = '';
    try { await el.waitFor({ timeout: 8000 }); text = await el.innerText(); } catch { text = await page.locator('.st-toast').innerText().catch(() => '(sin toast)'); }
    note(`toast ${pattern}`, pattern.test(text), text);
    return text;
  };
  const shot = (name) => page.screenshot({ path: path.join(output, `${name}.png`), fullPage: false });
  const goto = (tab, extra = '') => page.goto(`${base}/settings?tab=${tab}${extra}`, { waitUntil: 'networkidle' });
  const menuClick = async (label) => { const item = page.locator('#st-menu:not([hidden]) .st-menu-item', { hasText: label }); await item.waitFor(); await item.click(); };
  const confirmAccept = async () => { await page.locator('#st-confirm[open] #st-confirm-accept').click(); };
  // espera (hasta 8 s) a que el conteo del locator cumpla el predicado; evita carreras entre toast y re-render
  const expectText = async (name, selector, pattern) => { await page.locator(selector, { hasText: pattern }).first().waitFor({ timeout: 8000 }).catch(() => {}); const text = (await page.locator(selector).first().innerText()).trim(); note(name, pattern.test(text), text); };
  const countIs = async (locator, predicate) => { let n = 0; for (let i = 0; i < 40; i++) { n = await locator.count(); if (predicate(n)) return n; await page.waitForTimeout(200); } return n; };

  // ---- pestañas en vivo 1600x900 ----
  for (const tab of liveTabs) {
    const response = await goto(tab);
    note(`live ${tab} 200`, response.status() === 200);
    note(`live ${tab} pro shell`, (await page.locator('.ix-brand-name').innerText()).trim() === 'Ferretería Pro');
    if (tab !== 'general') { // la pestaña General conserva sus campos fijos preexistentes (fuera del alcance de esta tarea)
      note(`live ${tab} sin "No disponible en esta versión"`, !(await page.content()).includes('No disponible en esta versión'));
      note(`live ${tab} sin botones deshabilitados por versión`, (await page.locator('button[title="No disponible en esta versión."]').count()) === 0);
    }
    const geo = await overflow(page);
    note(`live ${tab} sin desborde 1600`, geo.scrollWidth <= geo.innerWidth && geo.clipped.length === 0, JSON.stringify(geo));
    await shot(`settings-${tab}-live`);
  }

  /* ================= USUARIOS Y ROLES ================= */
  await goto('users');
  await page.locator('#st-user-rows [data-user-id]').first().waitFor();
  await page.locator('#st-user-rows [data-user-id]').first().click();
  note('usuarios: ID formato U-0001', /^ID: U-\d{4}$/.test(await page.locator('#st-user-id').innerText()), await page.locator('#st-user-id').innerText());
  await page.locator('#st-user-search').fill('Operador E2E');
  await page.waitForTimeout(250);
  await page.locator('#st-user-rows [data-user-id]').first().click();
  note('usuarios: último acceso real del operador', /\d{2}\/\d{2}\/\d{4}/.test(await page.locator('#st-user-last-login').innerText()), await page.locator('#st-user-last-login').innerText());
  await page.locator('#st-user-search').fill('Usuario E2E 01');
  await page.waitForTimeout(250);
  await page.locator('#st-user-rows [data-user-id]').first().click();
  if ((await page.locator('#st-user-active').inputValue()) === '0') { // ejecución previa interrumpida: reactivar primero
    await page.locator('#st-user-menu').click(); await menuClick('Activar usuario'); await confirmAccept(); await toast(/activado/);
  }
  await page.locator('#st-user-menu').click();
  await menuClick('Desactivar usuario');
  await confirmAccept();
  await toast(/desactivado/);
  await page.waitForFunction(() => document.getElementById('st-user-active')?.value === '0', null, { timeout: 8000 }).catch(() => {});
  note('usuarios: estado inactivo persistido', (await page.locator('#st-user-active').inputValue()) === '0');
  await page.locator('#st-user-menu').click();
  await menuClick('Activar usuario');
  await confirmAccept();
  await toast(/activado/);
  await page.locator('#st-user-search').fill('');
  // Nuevo rol
  const roleName = `E2E Rol ${ctx.run}-${Date.now().toString(36).slice(-4)}`;
  await page.locator('#st-role-new').click();
  await page.locator('#st-role-dialog[open]').waitFor();
  await page.locator('#st-role-form [name=name]').fill(roleName);
  await page.locator('#st-role-form [name=description]').fill('Rol creado por la suite Playwright');
  await page.locator('#st-role-form [name=permissions][value="products:read"]').check();
  await page.locator('#st-role-form [name=permissions][value="inventory:read"]').check();
  await page.locator('#st-role-submit').click();
  await toast(/Rol creado/);
  const roleRow = page.locator('.st-role-row', { hasText: roleName });
  await roleRow.waitFor({ timeout: 8000 }).catch(() => {});
  note('roles: nuevo rol listado', await roleRow.count() === 1, String(await roleRow.count()));
  note('roles: nuevo rol en selector de usuario', (await page.locator('#st-user-role option', { hasText: roleName }).count()) === 1);
  // Permisos: matriz editable, seleccionar todo, guardar, recarga
  await roleRow.locator('[data-role-menu]').click();
  await menuClick('Ver permisos');
  note('permisos: título del rol seleccionado', (await page.locator('#st-permissions-title').innerText()).includes(roleName));
  note('permisos: 2 marcados', (await page.locator('#st-permission-list [data-permission]:checked').count()) === 2);
  await page.locator('[data-module-toggle="sales"]').check();
  await page.locator('[data-expand="sales"]').click();
  note('permisos: detalle del módulo visible', await page.locator('#st-permission-detail-sales').isVisible());
  const salesCount = await page.locator('#st-permission-detail-sales [data-permission]:checked').count();
  note('permisos: módulo completo marca individuales', salesCount === await page.locator('#st-permission-detail-sales [data-permission]').count() && salesCount > 0);
  await page.locator('#st-save').click();
  await toast(/Permisos del rol .* guardados/);
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('.st-role-row', { hasText: roleName }).locator('[data-role-menu]').click();
  await menuClick('Ver permisos');
  note('permisos: persisten tras recargar', (await page.locator('#st-permission-list [data-permission]:checked').count()) === 2 + salesCount);
  await page.locator('#st-permissions-toggle-all').click();
  note('permisos: seleccionar todo', (await page.locator('#st-permission-list [data-permission]:not(:checked)').count()) === 0);
  await page.locator('#st-permissions-toggle-all').click();
  note('permisos: deseleccionar todo', (await page.locator('#st-permission-list [data-permission]:checked').count()) === 0);
  await page.locator('#st-reset').click();
  await confirmAccept();
  await toast(/restablecidos/);
  note('permisos: restablecer descarta cambios', (await page.locator('#st-permission-list [data-permission]:checked').count()) === 2 + salesCount);
  // Admin no puede perder permisos críticos ni desactivarse
  await page.locator('.st-role-row', { hasText: 'Administrador' }).locator('[data-role-menu]').click();
  await menuClick('Ver permisos');
  await page.locator('[data-module-toggle="settings"]').uncheck();
  expected.add('/api/settings/roles/1');
  const adminId = await page.locator('#st-permissions').getAttribute('data-role-id');
  expected.add(`/api/settings/roles/${adminId}`);
  await page.locator('#st-save').click();
  await toast(/Administrador|crítico|administrador/i);
  await page.locator('.st-role-row', { hasText: 'Administrador' }).locator('[data-role-menu]').click();
  note('roles: Administrador no se puede desactivar', await page.locator('#st-menu .st-menu-item', { hasText: 'Desactivar' }).isDisabled());
  note('roles: Administrador no se puede eliminar', await page.locator('#st-menu .st-menu-item', { hasText: 'Eliminar' }).isDisabled());
  await page.keyboard.press('Escape');
  // Ver detalles, duplicar, desactivar/activar, eliminar
  await page.locator('.st-role-row', { hasText: roleName }).locator('[data-role-menu]').click();
  await menuClick('Ver detalles');
  await page.locator('#st-role-dialog[open]').waitFor();
  note('roles: detalles en solo lectura', await page.locator('#st-role-form [name=name]').isDisabled() && (await page.locator('#st-role-meta').innerText()).includes('Rol personalizado'));
  await shot('settings-users-role-details');
  await page.locator('#st-role-dialog [data-dialog-close]').click();
  await page.locator('.st-role-row', { hasText: roleName }).locator('[data-role-menu]').click();
  await menuClick('Duplicar');
  await toast(/duplicado/);
  const copyRow = page.locator('.st-role-row', { hasText: `Copia de ${roleName}` });
  note('roles: copia creada', await countIs(copyRow, (n) => n === 1) === 1);
  await copyRow.locator('[data-role-menu]').click();
  await menuClick('Desactivar');
  await confirmAccept();
  await toast(/actualizado/);
  await page.locator('.st-role-row.is-inactive', { hasText: `Copia de ${roleName}` }).waitFor({ timeout: 8000 }).catch(() => {});
  note('roles: copia marcada inactiva', (await copyRow.innerText()).includes('Inactivo'));
  await copyRow.locator('[data-role-menu]').click();
  await menuClick('Eliminar');
  await confirmAccept();
  await toast(/eliminado/);
  note('roles: copia eliminada', await countIs(copyRow, (n) => n === 0) === 0);
  await page.locator('.st-role-row', { hasText: roleName }).locator('[data-role-menu]').click();
  await menuClick('Editar');
  await page.locator('#st-role-form [name=description]').fill('Descripción editada');
  await page.locator('#st-role-submit').click();
  await toast(/actualizado/);
  await page.locator('.st-role-row', { hasText: 'Descripción editada' }).waitFor({ timeout: 8000 }).catch(() => {});
  note('roles: descripción editada', (await page.locator('.st-role-row', { hasText: roleName }).innerText()).includes('Descripción editada'));
  await shot('settings-users-roles-live');

  /* ================= SEGURIDAD ================= */
  await goto('security');
  // Política
  await page.locator('#st-edit-policy').click();
  await page.locator('#st-policy-dialog[open]').waitFor();
  await page.locator('#st-policy-form [name="security.password_min_length"]').fill('10');
  await page.locator('#st-policy-form [name="security.password_require_uppercase"]').check();
  await page.locator('#st-policy-form [name="security.password_require_number"]').check();
  await page.locator('#st-policy-form [name="security.password_history_count"]').fill('3');
  await shot('settings-security-policy-dialog');
  await page.locator('#st-policy-form [type=submit]').click();
  await toast(/guardad/i);
  await page.reload({ waitUntil: 'networkidle' });
  note('política: longitud persistida', (await page.locator('[data-policy="min_length"]').innerText()).startsWith('10'));
  note('política: mayúsculas requeridas', (await page.locator('[data-policy="require_uppercase"]').innerText()) === 'Requeridas');
  note('política: historial 3', (await page.locator('[data-policy="history_count"]').innerText()).includes('3'));
  // Cambio de contraseña (respeta política; historial impide reutilizar)
  await page.locator('#st-change-password').click();
  await page.locator('#st-password-dialog[open]').waitFor();
  await page.locator('#st-password-form [name=current_password]').fill(ctx.password);
  note('contraseña: el diálogo refleja la política (minlength 10)', (await page.locator('#st-password-form [name=new_password]').getAttribute('minlength')) === '10');
  expected.add('/api/auth/change-password');
  const short = await context.request.post(`${base}/api/auth/change-password`, { data: { current_password: ctx.password, new_password: 'corta1', confirm_password: 'corta1' } });
  note('contraseña: el servidor rechaza corta', short.status() === 400 && /10 caracteres/.test((await short.json()).error || ''), String(short.status()));
  const newPassword = 'NuevaClaveE2E2026';
  await page.locator('#st-password-form [name=new_password]').fill(newPassword);
  await page.locator('#st-password-form [name=confirm_password]').fill(newPassword);
  await page.locator('#st-password-form [type=submit]').click();
  await toast(/actualizada/i);
  const reuse = await context.request.post(`${base}/api/auth/change-password`, { data: { current_password: newPassword, new_password: ctx.password, confirm_password: ctx.password } });
  note('contraseña: historial impide reutilizar la anterior', reuse.status() === 400, String(reuse.status()));
  // Segunda sesión (otro "dispositivo") para probar el registro de sesiones
  const other = await browser.newContext({ userAgent: 'Mozilla/5.0 (Linux; Android 14) Chrome/128 Mobile' });
  const otherLogin = await other.request.post(`${base}/api/auth/login`, { data: { identifier: ctx.username, password: newPassword } });
  note('sesiones: segundo inicio de sesión 200', otherLogin.status() === 200);
  await page.reload({ waitUntil: 'networkidle' });
  note('sesiones: tabla muestra la actual', (await page.locator('#st-sessions-body').innerText()).includes('Actual'));
  note('sesiones: ubicación honesta', (await page.locator('#st-sessions-body').innerText()).includes('No disponible'));
  note('historial: intentos registrados', (await page.locator('#st-history-body tr:not(.st-security-empty)').count()) > 0);
  await page.locator('#st-sessions-all').click();
  await page.locator('#st-list-dialog[open]').waitFor();
  await page.locator('#st-list-body tr:not(.st-security-empty)').first().waitFor();
  const sessionRows = await page.locator('#st-list-body tr:not(.st-security-empty)').count();
  note('sesiones: "Ver todas" lista ambas', sessionRows >= 2, String(sessionRows));
  await shot('settings-security-sessions-dialog');
  await page.locator('#st-list-body [data-revoke-session]').first().click();
  await toast(/Sesión cerrada/);
  note('sesiones: revocación inmediata rechaza al otro dispositivo', (await other.request.get(`${base}/api/auth/me`)).status() === 401);
  await page.locator('#st-list-dialog [type=submit]').click();
  await page.locator('#st-history-all').click();
  await page.locator('#st-list-dialog[open]').waitFor();
  await page.locator('#st-list-body .st-security-status', { hasText: /Exitoso|Fallido/ }).first().waitFor({ timeout: 8000 }).catch(() => {});
  note('historial: diálogo completo con resultado', /Exitoso|Fallido/.test(await page.locator('#st-list-body').innerText()));
  await page.locator('#st-list-dialog [type=submit]').click();
  await page.locator('#st-sessions-close-inactive').click();
  await confirmAccept();
  await toast(/Sesiones cerradas/);
  // 2FA: enrolar con TOTP calculado aquí
  await page.locator('#st-mfa-toggle').click();
  await page.locator('#st-mfa-dialog[open]').waitFor();
  note('2FA: QR servido por el servidor', (await page.locator('#st-mfa-qr svg').count()) === 1);
  const secret = (await page.locator('#st-mfa-setup-key').innerText()).trim();
  note('2FA: clave manual base32', /^[A-Z2-7]{16,}$/.test(secret));
  await shot('settings-security-mfa-enroll');
  expected.add('/api/auth/mfa/confirm');
  await page.locator('#st-mfa-form [name=code]').fill('000000');
  await page.locator('#st-mfa-submit').click();
  await expectText('2FA: código inválido rechazado', '#st-mfa-dialog .st-dialog-error', /no es válido/);
  await page.locator('#st-mfa-form [name=code]').fill(totp(secret));
  await page.locator('#st-mfa-submit').click();
  await page.locator('#st-mfa-codes-step:not([hidden])').waitFor();
  const recovery = await page.locator('#st-mfa-code-list li').allInnerTexts();
  note('2FA: 10 códigos de recuperación mostrados una vez', recovery.length === 10 && recovery.every((c) => /^[A-F0-9]{4}-[A-F0-9]{4}$/.test(c)));
  await shot('settings-security-mfa-codes');
  await page.locator('#st-mfa-dialog [data-dialog-close]').click();
  await expectText('2FA: badge Activo', '#st-mfa-badge', /^Activo$/);
  await expectText('2FA: 10 códigos disponibles', '#st-mfa-codes-count', /^10/);
  // Login exige desafío 2FA; código de recuperación válido una sola vez
  const mfaCtx = await browser.newContext();
  const mfaLogin = await mfaCtx.request.post(`${base}/api/auth/login`, { data: { identifier: ctx.username, password: newPassword } });
  note('2FA: login responde mfa_required sin sesión', mfaLogin.status() === 200 && (await mfaLogin.json()).mfa_required === true && (await mfaCtx.request.get(`${base}/api/auth/me`)).status() === 401);
  const bad = await mfaCtx.request.post(`${base}/api/auth/mfa/verify`, { data: { code: '123456' } });
  note('2FA: verify con código incorrecto 400', bad.status() === 400);
  const rec = await mfaCtx.request.post(`${base}/api/auth/mfa/verify`, { data: { code: recovery[0], trust_device: true } });
  note('2FA: verify con código de recuperación abre sesión', rec.status() === 200 && (await mfaCtx.request.get(`${base}/api/auth/me`)).status() === 200);
  note('2FA: cookie de dispositivo confiable emitida', (await mfaCtx.cookies()).some((c) => c.name === 'fc_trusted_device' && c.httpOnly));
  const again = await browser.newContext();
  await again.request.post(`${base}/api/auth/login`, { data: { identifier: ctx.username, password: newPassword } });
  note('2FA: código de recuperación no se reutiliza', (await again.request.post(`${base}/api/auth/mfa/verify`, { data: { code: recovery[0] } })).status() === 400);
  await again.close();
  await mfaCtx.close();
  // Login UI con paso 2FA
  const ui = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const loginPage = await ui.newPage();
  await loginPage.goto(`${base}/login`);
  await loginPage.locator('#login-identifier').fill(ctx.username);
  await loginPage.locator('#login-password').fill(newPassword);
  await loginPage.locator('#login-submit').click();
  await loginPage.locator('#mfa-form:not(.d-none)').waitFor();
  await loginPage.screenshot({ path: path.join(output, 'login-mfa-step.png') });
  await loginPage.locator('#mfa-code').fill(totp(secret, 1));
  await loginPage.locator('#mfa-submit').click();
  await loginPage.waitForURL(/dashboard/);
  note('2FA: login UI completa el desafío', loginPage.url().includes('/dashboard'));
  await ui.close();
  await page.reload({ waitUntil: 'networkidle' });
  note('dispositivos: confiable listado', (await countIs(page.locator('#st-trusted-list li'), (n) => n >= 1)) >= 1);
  await page.locator('#st-trusted-add').click();
  await page.locator('#st-reauth-dialog[open]').waitFor();
  await page.locator('#st-reauth-form [name=code]').fill(recovery[3]); // re-autenticación con código de recuperación (un solo uso)
  await page.locator('#st-reauth-form [name=label]').fill('Equipo de pruebas Playwright');
  await page.locator('#st-reauth-submit').click();
  await toast(/confiable/);
  await expectText('dispositivos: este navegador agregado', '#st-trusted-list', /Equipo de pruebas Playwright/);
  await shot('settings-security-live-2fa');
  await page.locator('#st-trusted-list [data-revoke-device]').first().click();
  await toast(/revocado/);
  // Regenerar códigos y desactivar 2FA con re-autenticación fuerte
  await page.locator('#st-mfa-codes-button').click();
  await page.locator('#st-reauth-dialog[open]').waitFor();
  expected.add('/api/auth/mfa/recovery-codes');
  await page.locator('#st-reauth-form [name=code]').fill(recovery[1]);
  await page.locator('#st-reauth-form [name=current_password]').fill('incorrecta');
  await page.locator('#st-reauth-submit').click();
  await expectText('2FA: regenerar exige contraseña correcta', '#st-reauth-dialog .st-dialog-error', /contraseña/i);
  await page.locator('#st-reauth-form [name=code]').fill(recovery[2]);
  await page.locator('#st-reauth-form [name=current_password]').fill(newPassword);
  await page.locator('#st-reauth-submit').click();
  await page.locator('#st-mfa-codes-step:not([hidden])').waitFor();
  note('2FA: códigos regenerados (10 nuevos)', (await page.locator('#st-mfa-code-list li').count()) === 10);
  await page.locator('#st-mfa-dialog [data-dialog-close]').click();
  await page.locator('#st-mfa-toggle').click();
  await page.locator('#st-reauth-dialog[open]').waitFor();
  // TOTP del intervalo siguiente al último aceptado (+1 en el login UI): anti-replay exacto por paso
  await page.locator('#st-reauth-form [name=code]').fill(totp(secret, 1));
  await page.locator('#st-reauth-form [name=current_password]').fill(newPassword);
  expected.add('/api/auth/mfa/disable');
  await page.locator('#st-reauth-submit').click();
  await page.locator('.st-toast:not([hidden]):has-text("desactivada"), #st-reauth-dialog[open] .st-dialog-error:not([hidden])').first().waitFor({ timeout: 8000 });
  if (await page.locator('#st-reauth-dialog[open] .st-dialog-error:not([hidden])').count()) { // mismo paso que el login UI: esperar al siguiente intervalo
    await page.waitForTimeout(31000);
    await page.locator('#st-reauth-form [name=code]').fill(totp(secret, 1));
    await page.locator('#st-reauth-submit').click();
  }
  await toast(/desactivada/);
  await expectText('2FA: badge Inactivo tras desactivar', '#st-mfa-badge', /^Inactivo$/);
  // Restablecer política (por pestaña) y comprobar que el usuario y su contraseña siguen intactos
  await page.locator('#st-reset').click();
  await confirmAccept();
  await toast(/restablecidos/);
  await expectText('política: restablecida a 6', '[data-policy="min_length"]', /^6/);
  note('reset seguridad no toca credenciales', (await other.request.post(`${base}/api/auth/login`, { data: { identifier: ctx.username, password: newPassword } })).status() === 200);
  await other.close();
  await shot('settings-security-live');

  /* ================= NOTIFICACIONES ================= */
  await goto('notifications');
  note('canales: correo marcado como proveedor no configurado', (await page.locator('[data-channel-badge="email"]').innerText()) === 'Proveedor no configurado');
  await page.locator('[data-channel-config="whatsapp"]').click();
  await page.locator('#st-channel-dialog[open]').waitFor();
  note('canal WhatsApp: diálogo indica variables faltantes', /WHATSAPP_/.test(await page.locator('#st-channel-status').innerText()) && await page.locator('#st-channel-form [name=enabled]').isDisabled());
  await page.locator('#st-channel-form [name=destination]').fill('+584121234567');
  await shot('settings-notifications-channel-dialog');
  await page.locator('#st-channel-form [type=submit]').click();
  await toast(/WhatsApp guardado/);
  await page.locator('[name="notifications.whatsapp_enabled"]').click({ force: true });
  await toast(/proveedor no configurado/i);
  note('canal WhatsApp: no se activa sin proveedor', !(await page.locator('[name="notifications.whatsapp_enabled"]').isChecked()));
  const salesApp = page.locator('[name="notify.sales_app"]');
  const before = await salesApp.isChecked();
  await salesApp.click({ force: true });
  await page.locator('[name="notifications.email_to"]').fill('alertas@example.invalid');
  await page.locator('[name="notifications.digest_frequency"]').selectOption('daily');
  await page.locator('[name="notifications.digest_time"]').fill('07:30');
  await page.locator('#st-save').click();
  await toast(/guardad/i);
  await page.reload({ waitUntil: 'networkidle' });
  note('matriz: celda persistida', (await salesApp.isChecked()) === !before);
  note('correo: destino y frecuencia persistidos', (await page.locator('[name="notifications.email_to"]').inputValue()) === 'alertas@example.invalid' && (await page.locator('[name="notifications.digest_frequency"]').inputValue()) === 'daily' && (await page.locator('[name="notifications.digest_time"]').inputValue()) === '07:30');
  await page.locator('#st-test-notifications').click();
  await page.locator('#st-test-dialog[open]').waitFor();
  note('probar: solo canales activos y configurados habilitados', await page.locator('#st-test-channels [value=email]').isDisabled() && !(await page.locator('#st-test-channels [value=app]').isDisabled()));
  await page.locator('#st-test-submit').click();
  await page.locator('#st-test-results:not([hidden]) li').first().waitFor();
  note('probar: resultado real del canal app', (await page.locator('#st-test-results li.is-ok').count()) === 1);
  await shot('settings-notifications-test-dialog');
  await page.locator('#st-test-dialog [data-dialog-close]').click();
  await page.locator('#st-recent-list [data-notification-detail]').first().waitFor();
  note('bandeja: notificación de prueba listada', (await page.locator('#st-recent-list').innerText()).includes('prueba'));
  await page.locator('#st-recent-list [data-notification-detail]').first().click();
  await page.locator('#st-notification-dialog[open]').waitFor();
  note('bandeja: detalle con entregas', (await page.locator('#st-notification-deliveries li').count()) >= 1);
  await shot('settings-notifications-detail-dialog');
  await page.locator('#st-notification-dialog [type=submit]').click();
  await page.locator('#st-recent-read-all').click();
  await toast(/Marcadas como leídas/);
  await page.locator('#st-recent-read-all[disabled]').waitFor({ timeout: 8000 }).catch(() => {});
  note('bandeja: sin pendientes tras marcar todas', await page.locator('#st-recent-read-all').isDisabled());
  await page.locator('#ix-bell').click();
  await page.locator('#ix-bell-panel:not([hidden])').waitFor();
  note('campana: bandeja real', (await page.locator('#ix-bell-panel .ix-bell-title').innerText()) === 'Notificaciones' && (await page.locator('#ix-bell-list li').count()) > 0);
  await shot('settings-notifications-bell');
  await page.keyboard.press('Escape');
  // Evento real: salida de inventario deja el producto agotado => notificación de stock
  const countsBefore = await (await context.request.get(`${base}/api/settings/notifications?per_page=1`)).json();
  const exit = await context.request.post(`${base}/api/inventory/exit`, { data: { product_id: ctx.low_stock_product_id, quantity: 2, reason: 'Prueba E2E notificaciones' } });
  note('evento stock: salida registrada', exit.status() === 201 || exit.status() === 200, String(exit.status()));
  const countsAfter = await (await context.request.get(`${base}/api/settings/notifications?per_page=1`)).json();
  note('evento stock: notificación generada por el servicio', countsAfter.total > countsBefore.total, `${countsBefore.total} -> ${countsAfter.total}`);
  await page.locator('#st-reset').click();
  await confirmAccept();
  await toast(/restablecidos/);
  await shot('settings-notifications-live');

  /* ================= RESPALDO ================= */
  await goto('backup');
  await page.locator('[name=backup_type][value=database]').check();
  await page.locator('#st-backup-create').click();
  await toast(/Respaldo 'Solo base de datos' creado/);
  await page.locator('[name=backup_type][value=full]').check();
  await page.locator('#st-backup-create').click();
  await toast(/Respaldo 'Completo' creado/);
  await page.locator('#st-backup-history-toggle').click();
  const historyRows = page.locator('#st-backup-history-body tr[data-backup-id]');
  note('respaldo: 2 en historial', (await countIs(historyRows, (n) => n >= 2)) >= 2);
  await expectText('respaldo: contador actualizado', '#st-backup-count', /^([2-9]|\d{2,})/);
  await historyRows.first().locator('[data-backup-menu]').click();
  await menuClick('Verificar integridad');
  await toast(/SHA-256 coincide/);
  await historyRows.first().locator('[data-backup-menu]').click();
  await menuClick('Ver detalle');
  await page.locator('#st-inspect-dialog[open]').waitFor();
  note('respaldo: detalle con manifiesto y SHA', /Manifiesto/.test(await page.locator('#st-inspect-meta').innerText()) && /SHA-256/.test(await page.locator('#st-inspect-meta').innerText()));
  await shot('settings-backup-inspect-dialog');
  await page.locator('#st-inspect-dialog [type=submit]').click();
  const download = page.waitForEvent('download');
  await historyRows.first().locator('[data-backup-menu]').click();
  await menuClick('Descargar');
  note('respaldo: descarga con nombre real', /ferreteria_conejo_e2e_.*\.(zip|sql)$/.test((await download).suggestedFilename()));
  // Programación automática real (schtasks) con nombre E2E dedicado
  await page.locator('[name="backup.auto_frequency"]').selectOption('daily');
  await page.locator('[name="backup.auto_time"]').fill('02:30');
  await page.locator('[name="backup.retention_days"]').fill('45');
  await page.locator('#st-schedule-apply').click();
  await toast(/Tarea programada registrada/);
  await page.locator('#st-backup-next:not(:has-text("No programado"))').waitFor({ timeout: 8000 }).catch(() => {});
  note('respaldo: próximo respaldo automático leído de la tarea', !/No programado/.test(await page.locator('#st-backup-next').innerText()), await page.locator('#st-backup-next').innerText());
  await page.reload({ waitUntil: 'networkidle' });
  note('respaldo: configuración automática persistida', (await page.locator('[name="backup.auto_frequency"]').inputValue()) === 'daily' && (await page.locator('[name="backup.retention_days"]').inputValue()) === '45');
  await shot('settings-backup-live-scheduled');
  await page.locator('[name="backup.auto_frequency"]').selectOption('off');
  await page.locator('#st-schedule-apply').click();
  await toast(/Tarea programada eliminada/);
  await expectText('respaldo: sin tarea => No programado', '#st-backup-next', /No programado/);
  // Ubicación: validación server-side de la ruta
  expected.add('/api/settings');
  await page.locator('[name="backup.directory"]').fill('..\\..\\Windows');
  await page.locator('#st-save').click();
  await toast(/'\.\.'|ruta|absoluta/i);
  await page.locator('[name="backup.directory"]').fill('');
  await page.locator('#st-save').click();
  await toast(/guardad/i);
  expected.delete('/api/settings');
  // Google Drive: proveedor no configurado => flujo de ayuda, nunca "conectado" falso
  note('drive: estado honesto', /proveedor no configurado/i.test(await page.locator('#st-drive-text').innerText()));
  await page.locator('#st-drive-help').click();
  await page.locator('#st-drive-help-dialog[open]').waitFor();
  note('drive: guía muestra URI de callback', /\/api\/settings\/backups\/drive\/callback/.test(await page.locator('#st-drive-redirect').innerText()));
  await page.locator('#st-drive-help-dialog [type=submit]').click();
  await page.locator('#st-backup-guide').click();
  await page.locator('#st-guide-dialog[open]').waitFor();
  note('guía: contenido real', /Restauración/.test(await page.locator('#st-guide-dialog').innerText()));
  await shot('settings-backup-guide-dialog');
  await page.locator('#st-guide-dialog [type=submit]').click();
  // Restauración en la base desechable (nunca ferreteria_conejo): confirmación tipada + respaldo de seguridad
  const databaseOption = await page.locator('#st-restore-select option', { hasText: 'Solo base de datos' }).first().getAttribute('value');
  await page.locator('#st-restore-select').selectOption(databaseOption);
  await page.locator('#st-restore-button').click();
  await page.locator('#st-restore-dialog[open]').waitFor();
  note('restaurar: destino es la base desechable', (await page.locator('#st-restore-meta').innerText()).includes('ferreteria_conejo_e2e'));
  note('restaurar: botón bloqueado hasta escribir RESTAURAR', await page.locator('#st-restore-submit').isDisabled());
  await page.locator('#st-restore-form [name=confirmation]').fill('restaurar');
  note('restaurar: minúsculas no habilitan', await page.locator('#st-restore-submit').isDisabled());
  await page.locator('#st-restore-form [name=confirmation]').fill('RESTAURAR');
  await shot('settings-backup-restore-dialog');
  const wrongConfirm = await context.request.post(`${base}/api/settings/backups/${databaseOption}/restore`, { data: { confirmation: 'restaurar' } });
  note('restaurar: servidor exige RESTAURAR exacto', wrongConfirm.status() === 400);
  await page.locator('#st-restore-submit').click();
  await page.locator('.st-toast:not([hidden])', { hasText: /Restauración completada/ }).waitFor({ timeout: 120000 });
  note('restaurar: completada', true);
  await expectText('restaurar: historial de restauraciones registra la corrida', '#st-restore-history-body', /Completada/);
  await expectText('restaurar: respaldo de seguridad previo registrado', '#st-backup-history-body', /ferreteria_conejo_e2e/);
  if (!(await page.locator('#st-backup-history-body').isVisible())) await page.locator('#st-backup-history-toggle').click(); // el historial se re-renderiza plegado tras restaurar
  const lastRow = page.locator('#st-backup-history-body tr[data-backup-id]').last();
  await lastRow.scrollIntoViewIfNeeded();
  await lastRow.locator('[data-backup-menu]').click();
  await menuClick('Eliminar');
  await confirmAccept();
  await toast(/eliminado/);
  await expectText('respaldo: fila marcada eliminada', '#st-backup-history-body', /Eliminado/);
  await shot('settings-backup-live');

  /* ================= MODO REFERENCIA: cero escrituras ================= */
  const countsRefBefore = await (await fetch(`${base}/__settings_e2e__/counts`)).json();
  for (const tab of allTabs) {
    await goto(tab, '&ref=1');
    for (const button of await page.locator('button:not([disabled])').all()) { if (await button.isVisible()) await button.click({ force: true, noWaitAfter: true }).catch(() => {}); }
  }
  const countsRefAfter = await (await fetch(`${base}/__settings_e2e__/counts`)).json();
  note('referencia: cero escrituras en BD', JSON.stringify(countsRefBefore) === JSON.stringify(countsRefAfter), JSON.stringify({ before: countsRefBefore, after: countsRefAfter }));

  /* ================= viewports ================= */
  for (const [w, h] of viewports) {
    await page.setViewportSize({ width: w, height: h });
    for (const tab of liveTabs) {
      await goto(tab);
      const geo = await overflow(page);
      note(`live ${tab} sin desborde ${w}`, geo.scrollWidth <= geo.innerWidth && geo.clipped.length === 0, JSON.stringify(geo));
      await shot(`settings-${tab}-${w}`);
    }
  }

  const failures = pageErrors.filter((e) => !/favicon/.test(e.message));
  fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ checks, pageErrors: failures }, null, 2));
  console.log(`\n${checks.filter((c) => c.ok).length}/${checks.length} comprobaciones OK; pageerrors: ${failures.length}`);
  if (failures.length) console.log(JSON.stringify(failures, null, 2));
  await browser.close();
  process.exit(failures.length ? 1 : 0);
})().catch((error) => { console.error(error); process.exit(1); });
