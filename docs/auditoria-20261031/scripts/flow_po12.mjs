import { start, stop, login, go, shot, fields, flushErrors } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Recepción';
await start();
const inv = await login('inventario', OUT);
await go(inv, '/purchases/receptions');
await inv.getByRole('button', { name: 'Nueva recepción' }).click(); await inv.waitForTimeout(1000);
const d = inv.locator('dialog[open]').last();
await d.locator('select').first().selectOption({ label: 'OC-000004 - E2E Proveedor 3DFAAF' }); await inv.waitForTimeout(1200);
console.log('TRAS ELEGIR OC:\n' + (await d.innerText()).replace(/\n+/g, ' | ').slice(0, 1500));
const qty = d.locator('input[type=number]').first();
console.log('cantidad attrs', await qty.evaluate((e) => ({ min: e.min, max: e.max, step: e.step, value: e.value })));
await shot(inv, 'oc_elegida', d);
const save = d.getByRole('button', { name: 'Guardar recepción' });
async function tryQty(v, label) {
  await qty.fill(String(v)); await inv.waitForTimeout(300);
  console.log(`[${label}] estado mostrado:`, await d.locator('input').evaluateAll((els) => els.filter((e) => e.value === 'Parcial' || e.value === 'Completa' || e.value === 'Completada').map((e) => e.value)));
  if (await save.isDisabled()) { const t = (await d.innerText()).replace(/\n+/g, ' | '); console.log(`[${label}] botón Guardar deshabilitado; texto:`, t.slice(t.indexOf('Productos de la orden'), t.indexOf('Productos de la orden') + 400)); await shot(inv, label, d); return true; }
  await save.click(); await inv.waitForTimeout(1200);
  const open = await d.isVisible();
  console.log(`[${label}] abierto=${open} nativo=${inv.audit.lastNative || ''} errores=${(await d.locator('[role=alert]:visible, .is-error:visible, [class*=error]:visible').allInnerTexts().catch(() => [])).join('/')}`, flushErrors(inv, A).map((x) => x.slice(0, 160)));
  inv.audit.lastNative = '';
  if (open) await shot(inv, label, d);
  return open;
}



await d.locator('textarea[name=notes]').fill('Llegaron 120 m; el proveedor envía el resto la próxima semana.');

await tryQty(120, 'parcial_120');
await go(inv, '/purchases/receptions');
console.log('lista:', (await inv.locator('tbody').innerText()).replace(/\s+/g, ' '));
await inv.locator('tbody tr').first().locator('button').first().click(); await inv.waitForTimeout(1200);
const det = inv.locator('dialog[open], [role=dialog]:visible').last();
console.log('DETALLE REC:\n' + (await det.innerText()).replace(/\n+/g, ' | '));
await shot(inv, 'detalle_rec', det);
await stop(OUT);
