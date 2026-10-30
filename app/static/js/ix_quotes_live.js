/* Cotizaciones live: configuración de la página genérica (ix_sales_docs_live.js) sobre /api/quotes.
   Fecha y vendedor los fija el servidor; los precios son el sale_price vigente copiado por la API;
   "Vencida" es un estado calculado por el servidor y nunca se escribe desde aquí. */
(function () {
    'use strict';
    if (document.documentElement.dataset.referenceMode === '1' || !window.IXSales || !window.IXSales.documentPage) return;
    const S = window.IXSales;
    const { money, toCents, escape } = S;
    const { dateOnly, cell, badge, ref, strong } = S.cells;
    const $ = (id) => document.getElementById(id);
    const TRASH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg>';
    const image = (product) => (product.image_url ? `<img src="${escape(product.image_url)}" alt="">` : '<span class="ix-thumb ql-thumb" aria-hidden="true"></span>');

    S.documentPage({
        prefix: 'ql', root: 'qv-live', api: '/api/quotes', numberKey: 'quote_number', noun: ['cotización', 'cotizaciones'],
        ids: { rows: 'qv-rows', empty: 'qv-empty', summary: 'qv-summary', pages: 'qv-pages', feedback: 'qv-feedback' },
        statusKey: 'filter_statuses',
        statusOrder: ['sent', 'pending', 'accepted', 'expired', 'rejected'],
        statusColors: { '': '#1675ff', sent: '#539cff', pending: '#ff9729', accepted: '#33b653', expired: '#fa858e', rejected: '#ee273e' },
        cells: (q) => [ref(q.quote_number), cell(dateOnly(q.quoted_at), 'sa-muted'), cell(q.customer ? q.customer.name : '—', 'sa-muted', q.customer && q.customer.name),
            cell(q.validity_label, 'sa-muted', `Vence el ${S.display(q.valid_until)}`), strong(money(toCents(q.total))), badge(q)],
        moreFields: [
            { id: 'client', param: 'customer_id', kind: 'customer' }, { id: 'product', param: 'product_id', kind: 'product' },
            { id: 'seller', param: 'seller_id' }, { id: 'validity', param: 'validity_days' },
            { id: 'min', param: 'total_min' }, { id: 'max', param: 'total_max' }, { id: 'from', param: 'date_from' }, { id: 'to', param: 'date_to' }
        ],
        moreInvalid: (v) => ((v.date_from && v.date_to && v.date_from > v.date_to) || (v.total_min && v.total_max && Number(v.total_min) > Number(v.total_max))
            ? 'El valor inicial no puede superar al final.' : ''),
        drawer: {
            form: 'quote-form', error: 'quote-error', save: 'save', emptyText: 'Agrega productos a la cotización.',
            lineHtml: ({ product, quantity }) => `<tr data-product="${product.id}"><td><div class="ql-product">${image(product)}<span>${escape(product.name)}<small>SKU: ${escape(product.code)}</small></span></div></td>
                <td>${money(toCents(product.sale_price))}</td>
                <td><input type="number" min="1" max="9999" step="1" data-qty value="${quantity}" aria-label="Cantidad ${escape(product.name)}"></td>
                <td><strong>${money(toCents(product.sale_price) * quantity)}</strong></td>
                <td><button type="button" class="ql-delete" data-remove aria-label="Eliminar ${escape(product.name)}">${TRASH}</button></td></tr>`,
            totals(lines) {
                const subtotal = lines.reduce((sum, l) => sum + toCents(l.product.sale_price) * l.quantity, 0);
                const percent = Math.max(0, Math.min(100, Number($('ql-discount').value) || 0));
                const discount = Math.round(subtotal * percent / 100);
                $('ql-subtotal').textContent = money(subtotal);
                $('ql-discount-total').textContent = money(discount);
                $('ql-total').textContent = money(subtotal - discount);
            },
            onInput(rerender) {
                $('ql-discount').addEventListener('input', rerender);
                $('ql-notes').addEventListener('input', () => { $('ql-note-count').textContent = `${$('ql-notes').value.length}/500`; });
            },
            reset() {
                $('ql-validity').value = '30'; $('ql-quote-status').value = 'pending';
                $('ql-notes').value = ''; $('ql-note-count').textContent = '0/500'; $('ql-discount').value = '0';
            },
            payload() {
                const discount = Number($('ql-discount').value);
                if (!Number.isFinite(discount) || discount < 0 || discount > 100) return 'El descuento debe estar entre 0 y 100 %.';
                return {
                    validity_days: Number($('ql-validity').value), status: $('ql-quote-status').value,
                    discount_percent: String(Math.round(discount * 100) / 100), notes: $('ql-notes').value.trim()
                };
            }
        }
    });
})();
