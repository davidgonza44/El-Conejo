/* Document denominations only; legacy documents without a quote stay unknown. */
window.PurchaseMoney = Object.freeze({
  format(value, currency) {
    if (value == null) return '—';
    const symbol = {USD:'$', VES:'Bs.'}[currency] || (/^[A-Z]{3}$/.test(currency) ? currency : 'Moneda no registrada ·');
    return symbol + ' ' + Number(value).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
  },
  totals(rows) {
    return rows.map(row => this.format(row.amount, row.currency)).join(' / ') || 'Sin importes';
  },
  count(value, singular, plural) {
    return value + ' ' + (Number(value) === 1 ? singular : plural);
  }
});
