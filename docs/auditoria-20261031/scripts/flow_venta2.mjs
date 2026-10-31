import { start, stop, login, go, shot, flushErrors } from './lib.mjs';
import fs from 'node:fs';
const OUT = 'out/flows'; const A = 'Notas';
await start();
const p = await login('vendedor', OUT);
await go(p, '/delivery-notes');
console.log('KPIs:', (await p.locator('#kpi-issued, #kpi-cancelled, #kpi-total-amount, #kpi-average').allInnerTexts()).join(' | '));
console.log('fila:', (await p.locator('#notes-tbody tr').first().innerText()).replace(/\s+/g, ' '));
await shot(p, 'lista');
// línea duplicada
await p.click('#btn-new-note'); await p.waitForTimeout(600);
const v = await p.locator('#c-product option', { hasText: 'AUD-CAB' }).first().getAttribute('value');
await p.selectOption('#c-product', v); await p.fill('#c-quantity', '2'); await p.click('#btn-add-line'); await p.waitForTimeout(300);
await p.selectOption('#c-product', v).catch((e) => console.log('no se puede reelegir:', e.message.slice(0, 80)));
await p.fill('#c-quantity', '4'); await p.click('#btn-add-line'); await p.waitForTimeout(400);
console.log('duplicado -> líneas:', (await p.locator('#create-lines-tbody').innerText()).replace(/\s+/g, ' '), '| error:', await p.locator('#create-form-error').innerText(), '| hint:', await p.locator('#c-product-hint').innerText());
await p.keyboard.press('Escape'); await p.waitForTimeout(400);
await go(p, '/delivery-notes');
await p.locator('#notes-tbody tr', { hasText: 'NE-000002' }).locator('button, a').first().click(); await p.waitForTimeout(1200);
console.log('DETALLE:\n' + await p.locator('#detail-modal').innerText());
await shot(p, 'detalle', p.locator('#detail-modal .modal-content, #detail-modal').first());
const btnCancel = p.locator('#btn-cancel-note');
console.log('vendedor ve Cancelar:', await btnCancel.isVisible());
const [dl] = await Promise.all([p.waitForEvent('download', { timeout: 8000 }).catch(() => null), p.click('#btn-download-pdf')]);
if (dl) { const f = OUT + '/NE-000002.pdf'; await dl.saveAs(f); console.log('PDF', fs.statSync(f).size, 'bytes'); }
else { await p.waitForTimeout(1500); console.log('sin descarga; páginas', p.context().pages().map((x) => x.url())); }
for (const n of flushErrors(p, A)) console.log('   ', n.slice(0, 200));
await stop(OUT);
