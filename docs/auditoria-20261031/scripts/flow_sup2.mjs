import { start, stop, login, go, shot, flushErrors, TAG } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Proveedores';
await start();
const as = await login('asistente', OUT);
await go(as, '/suppliers');
const full = { name: 'Distribuidora Eléctrica Carabobo ' + TAG, tax_id: 'J-4' + String(Date.now()).slice(-7) + '-1', contact_name: 'Luis Herrera', contact_title: 'Gerente de ventas',
  phone: '0241-8234567', email: 'ventas@dielca.example', address: 'Zona Industrial Norte, galpón 14', city: 'Valencia', state_province: 'Carabobo',
  category: 'Material eléctrico', notes: 'Despacha martes y jueves. Crédito a 15 días.' };
async function attempt(label, over) {
  await as.locator('main button:visible', { hasText: 'Nuevo proveedor' }).click(); await as.waitForTimeout(800);
  const d = as.locator('dialog[open]').last();
  for (const [k, v] of Object.entries({ ...full, ...over })) await d.locator(`[name=${k}]`).fill(v);
  const send = d.getByRole('button', { name: 'Enviar propuesta' });
  if (await send.isDisabled()) { const inv = await d.locator(':invalid, [aria-invalid=true]').evaluateAll((e) => e.map((x) => x.name + ':' + (x.validationMessage || ''))); const vis = await d.locator('.is-invalid:visible, [class*=error]:visible, [role=alert]:visible').allInnerTexts(); console.log(`[${label}] Enviar DESHABILITADO; inválidos=${JSON.stringify(inv)} mensajes visibles=${JSON.stringify(vis)}`); await shot(as, label, d); await d.getByRole('button', { name: 'Cancelar' }).click(); await as.waitForTimeout(400); return; }
  await send.click(); await as.waitForTimeout(1300);
  const open = await d.isVisible();
  const msg = open ? (await d.innerText()).split('\n').filter((l) => /inválid|formato|debe|existe|obligat|admite|no puede|caracteres/i.test(l)).join(' / ') : '';
  console.log(`[${label}] abierto=${open} msg=${msg} nativo=${as.audit.lastNative || ''}`, flushErrors(as, A).map((x) => x.slice(0, 170)));
  as.audit.lastNative = '';
  if (open) { await shot(as, label, d); await d.getByRole('button', { name: 'Cancelar' }).click(); await as.waitForTimeout(400); }
}





await attempt('completo', {});
const ad = await login('admin', OUT);
await go(ad, '/suppliers?approval_status=pending');
const row = ad.locator('tbody tr', { hasText: 'Carabobo ' + TAG }).first();
console.log('fila admin:', (await row.innerText()).replace(/\s+/g, ' '));
await row.locator('button[aria-label^="Ver detalle"]').click(); await ad.waitForTimeout(1200);
const det = ad.locator('dialog[open]').last();
const txt = await det.innerText();
console.log('DETALLE:\n' + txt.replace(/\n+/g, ' | '));
for (const [k, v] of Object.entries(full)) if (!txt.includes(v)) console.log('  NO APARECE en detalle:', k, '=', v);
await shot(ad, 'detalle_admin', det);
for (const tab of ['Compras', 'Contactos', 'Historial']) {
  await det.getByRole('tab', { name: tab }).click().catch(() => det.getByText(tab, { exact: true }).click()); await ad.waitForTimeout(600);
  console.log(`PESTAÑA ${tab}:`, (await det.innerText()).replace(/\n+/g, ' | ').slice(0, 600));
  await shot(ad, 'tab_' + tab, det);
}
await det.getByRole('tab', { name: 'Información' }).click().catch(() => {}); await ad.waitForTimeout(400);
await det.getByRole('button', { name: 'Aprobar' }).click(); await ad.waitForTimeout(1500);
console.log('aprobar ->', flushErrors(ad, A), ad.audit.lastNative || '');
await go(ad, '/suppliers');
console.log('fila tras aprobar:', (await ad.locator('tbody tr', { hasText: 'Carabobo ' + TAG }).first().innerText()).replace(/\s+/g, ' '));
await stop(OUT);
