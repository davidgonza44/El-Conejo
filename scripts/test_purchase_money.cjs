/* Pure presentation regressions: no browser, database or HTTP requests. */
const assert = require('node:assert/strict');
global.window = {};
require('../app/static/js/purchase_money.js');
const money = window.PurchaseMoney;
assert.equal(money.format('178.50', 'USD'), '$ 178.50');
assert.equal(money.format('178.50', 'VES'), 'Bs. 178.50');
assert.equal(money.format('178.50', null), 'Moneda no registrada · 178.50');
assert.equal(money.format(undefined, 'USD'), '—');
assert.equal(money.totals([{currency:'USD', amount:'2.00'}, {currency:'VES', amount:'3.00'}]), '$ 2.00 / Bs. 3.00');
for (const [quantity, expected] of [[0,'0 renglones'], [1,'1 renglón'], [2,'2 renglones']]) {
  assert.equal(money.count(quantity, 'renglón', 'renglones'), expected);
}
console.log('PASS: USD, VES, unknown currency, empty amounts, separated totals and 0/1/multiple lines (8 assertions).');
