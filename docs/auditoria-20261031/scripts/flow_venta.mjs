import { start, stop, login, go, shot, flushErrors, TAG } from './lib.mjs';
const OUT = 'out/flows'; const A = 'Ventas';
await start();
const p = await login('vendedor', OUT);
await go(p, '/sales/customers');
const dlg = () => p.locator('dialog[open]').last();
async function customer(label, v) {
    await p.getByRole('button', { name: /Nuevo cliente/ }).click(); await p.waitForTimeout(600);
    for (const [k, val] of Object.entries(v)) await p.fill('#cu-' + k, val);
    await dlg().locator('button[type=submit], button:has-text("Guardar")').last().click(); await p.waitForTimeout(1200);
    const open = await p.locator('dialog[open]').count();
    const msg = open ? await dlg().innerText() : '';
    console.log(`[cliente ${label}] abierto=${open} msg=${(msg.match(/.*(inválid|obligat|existe|formato|debe|error|requier).*/gim) || []).join(' / ')} nativo=${p.audit.lastNative || ''}`);
    if (open) { await shot(p, 'cli_' + label.replace(/\W+/g, '_'), dlg()); await p.keyboard.press('Escape'); await p.waitForTimeout(300); }
    for (const n of flushErrors(p, A)) console.log('   ', n.slice(0, 180));
}
await customer('rif invalido', { name: 'Cliente RIF malo', 'tax-id': 'abc123', email: 'x@y.com' });
await customer('correo invalido', { name: 'Cliente correo malo', email: 'no-es-correo' });
await customer('telefono letras', { name: 'Cliente tel malo', phone: 'llamar luego' });
await customer('completo', { name: 'Constructora Los Andes ' + TAG, 'tax-id': 'J-40123456-7', phone: '0414-1234567', email: 'compras@losandes.example' });
await customer('duplicado', { name: 'Constructora Los Andes ' + TAG, 'tax-id': 'J-40123456-7' });
await go(p, '/sales/customers');
const row = p.locator('tr', { hasText: 'Los Andes ' + TAG }).first();
console.log('fila cliente:', (await row.innerText()).replace(/\s+/g, ' '));
await shot(p, 'clientes_lista');
console.log('acciones:', await row.locator('button,a').evaluateAll((b) => b.map((x) => x.getAttribute('aria-label') || x.textContent.trim())));

// Nota de entrega
await go(p, '/delivery-notes');
await p.click('#btn-new-note'); await p.waitForTimeout(700);
await p.click('#create-confirm, #create-save-btn').catch(() => {});
await p.locator('#create-modal button:has-text("Revisar")').click(); await p.waitForTimeout(600);
console.log('revisar vacío ->', await p.locator('#create-form-error').innerText().catch(() => ''));
await p.fill('#c-customer-search', 'Los Andes'); await p.waitForTimeout(1200);
console.log('resultados búsqueda:', (await p.locator('#c-customer-results').innerText()).replace(/\s+/g, ' '));
await p.locator('#c-customer-results button, #c-customer-results li, #c-customer-results [role=option]').first().click(); await p.waitForTimeout(500);
console.log('copiado: nombre=', await p.inputValue('#c-customer-name'), 'doc=', await p.inputValue('#c-customer-document'), 'tel=', await p.inputValue('#c-customer-phone'), 'dir=', await p.inputValue('#c-customer-address'));
await p.fill('#c-customer-address', 'Av. Bolívar, Valencia');
const opt = (code) => p.locator('#c-product option', { hasText: code }).first().getAttribute('value');
await p.selectOption('#c-product', await opt('AUD-CAB')); await p.fill('#c-quantity', '500'); await p.click('#btn-add-line'); await p.waitForTimeout(500);
console.log('cantidad > stock ->', await p.locator('#create-form-error').innerText().catch(() => ''), '| hint', await p.locator('#c-product-hint').innerText().catch(() => ''));
await p.fill('#c-quantity', '10'); await p.click('#btn-add-line'); await p.waitForTimeout(400);
await p.selectOption('#c-product', await opt('AUD-CAB')); await p.fill('#c-quantity', '5'); await p.click('#btn-add-line'); await p.waitForTimeout(400);
await p.selectOption('#c-product', await opt('E2E-CLAVO')); await p.fill('#c-quantity', '3'); await p.click('#btn-add-line'); await p.waitForTimeout(400);
console.log('líneas:', (await p.locator('#create-lines-tbody').innerText()).replace(/\s+/g, ' '), '| total', await p.locator('#create-total').innerText());
await shot(p, 'nota_lineas', p.locator('#create-modal'));
await p.locator('#create-modal button:has-text("Revisar")').click(); await p.waitForTimeout(800);
console.log('confirmación:', (await p.locator('#create-confirm').innerText()).replace(/\s+/g, ' '));
await shot(p, 'nota_confirmar', p.locator('#create-modal'));
await p.click('#create-save-btn'); await p.waitForTimeout(2000);
console.log('tras emitir: alerta=', await p.locator('#page-alert').innerText().catch(() => ''), 'modal detalle visible', await p.locator('#detail-modal').isVisible());
for (const n of flushErrors(p, A)) console.log('   ', n.slice(0, 200));
await shot(p, 'tras_emitir');
await stop(OUT);
