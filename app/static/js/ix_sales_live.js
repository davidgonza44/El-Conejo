/* Live Nueva venta: misma geometría que la referencia, datos y escritura reales vía /api/sales.
   El cliente solo envía customer_id, método/condición, items {product_id, quantity, discount_percent}
   e idempotency_key; cash_received solo para efectivo. Precios, totales, fecha,
   vendedor y vuelto definitivo los fija el servidor. No corre en ?ref=1. */
(function () {
    'use strict';
    const form = document.getElementById('sv-form');
    if (!form || document.documentElement.dataset.referenceMode === '1') return;

    const $ = (id) => document.getElementById(id);
    const els = {
        feedback: $('sv-feedback'), save: $('sv-save'), cancel: $('sv-cancel'),
        customerSearch: $('sv-customer-search'), customerList: $('sv-customer-list'), customerId: $('sv-customer-id'),
        customer: $('sv-customer'), customerEmpty: $('sv-customer-empty'), customerRemove: $('sv-customer-remove'),
        productSearch: $('sv-product-search'), productList: $('sv-product-list'), addProduct: $('sv-add-product'),
        lines: $('sv-lines'), empty: $('sv-empty'), count: $('sv-count'),
        subtotal: $('sv-subtotal'), discount: $('sv-discount'), total: $('sv-total'),
        method: $('sv-payment-method'), cash: $('sv-cash-received'), change: $('sv-change-due')
    };
    const cart = new Map(); // product_id -> { product, quantity, discount }
    let idempotencyKey = null;
    let submitting = false;
    let totalCents = 0;
    let feedbackTimer;

    const { api, money, toCents, node, thumb, STOCK_TONE } = window.IXSales; // ix_sales_shared.js
    function cashText() {
        const value = els.cash.value.trim().replace(',', '.');
        return /^\d{1,10}(\.\d{1,2})?$/.test(value) ? value : null;
    }
    function renderCash() {
        const active = els.method.value === 'cash';
        $('sv-cash-field').hidden = $('sv-change-field').hidden = !active;
        els.cash.disabled = !active;
        els.cash.required = active;
        const text = cashText();
        const change = text === null ? null : toCents(text) - totalCents;
        els.change.textContent = change === null ? '—' : change < 0 ? 'Efectivo insuficiente' : money(change);
        els.cash.setAttribute('aria-invalid', String(active && text !== null && change < 0));
    }
    els.method.addEventListener('change', () => { els.cash.value = ''; renderCash(); });
    els.cash.addEventListener('input', renderCash);
    function feedback(text, tone) {
        els.feedback.textContent = text;
        els.feedback.className = `sv-feedback${tone ? ` is-${tone}` : ''}`;
        els.feedback.hidden = !text;
        clearTimeout(feedbackTimer);
        if (tone === 'success') feedbackTimer = setTimeout(() => { els.feedback.hidden = true; }, 8000);
    }

    /* ---------- Desplegables de búsqueda (combobox + listbox compartido) ---------- */
    const combo = (input, list, fetchItems, renderItem, onPick) =>
        window.IXSales.combo(input, list, fetchItems, renderItem, onPick, (error) => feedback(error.message, 'error'));

    /* ---------- Cliente ---------- */
    function selectCustomer(customer) {
        els.customerId.value = customer ? customer.id : '';
        els.customer.hidden = !customer;
        els.customerEmpty.hidden = !!customer;
        if (!customer) return;
        $('sv-customer-name').textContent = customer.name;
        $('sv-customer-name').title = customer.name;
        const bits = [customer.tax_id ? `RIF: ${customer.tax_id}` : null, customer.phone ? `Tel: ${customer.phone}` : null].filter(Boolean);
        $('sv-customer-line1').textContent = bits.length ? bits.join(' · ') : (customer.is_walk_in ? 'Cliente genérico sin datos fiscales' : 'Sin RIF ni teléfono registrados');
        $('sv-customer-line2').textContent = customer.email || '';
        els.customerSearch.value = '';
    }
    combo(els.customerSearch, els.customerList,
        async (term) => (await api(`/api/customers?status=active&per_page=10&search=${encodeURIComponent(term)}`)).items,
        (customer) => {
            const li = node('li', undefined, 'sv-option');
            li.append(node('span', customer.name, 'sv-option-title'));
            li.append(node('span', [customer.tax_id, customer.phone].filter(Boolean).join(' · ') || (customer.is_walk_in ? 'Cliente genérico' : '—'), 'sv-option-desc'));
            return li;
        },
        selectCustomer);
    els.customerRemove.addEventListener('click', () => { selectCustomer(null); els.customerSearch.focus(); });
    api('/api/customers?status=active&per_page=1').then((data) => {
        const first = data.items[0];
        if (first && first.is_walk_in && !els.customerId.value) selectCustomer(first);
    }).catch(() => { /* el usuario elige el cliente manualmente */ });

    /* ---------- Productos ---------- */
    function addToCart(product, quantity = 1) {
        const line = cart.get(product.id) || { product, quantity: 0, discount: 0 };
        line.product = product;
        line.quantity += quantity;
        cart.set(product.id, line);
        if (!idempotencyKey) idempotencyKey = crypto.randomUUID();
        renderCart();
        feedback('', null);
        const row = els.lines.querySelector(`[data-product-id="${product.id}"] input[name="quantity"]`);
        if (row) row.focus();
    }
    const productCombo = combo(els.productSearch, els.productList,
        async (term) => (await api(`/api/sales/products?limit=10&search=${encodeURIComponent(term)}`)).items,
        (product) => {
            const li = node('li', undefined, 'sv-option sv-option-product');
            li.append(thumb(product, 'sv-option-thumb'));
            const text = node('span', undefined, 'sv-option-text');
            text.append(node('span', product.name, 'sv-option-title'), node('span', `${product.code} · ${money(toCents(product.sale_price))}`, 'sv-option-desc'));
            li.append(text, node('span', `Stock: ${product.current_stock}`, `sv-option-stock is-${STOCK_TONE[product.stock_status] || 'gray'}`));
            if (product.current_stock < 1) li.classList.add('is-out');
            return li;
        },
        (product) => {
            if (product.current_stock < 1) { feedback(`'${product.name}' está agotado; no puede agregarse.`, 'error'); return; }
            addToCart(product);
        });
    // "Agregar producto": si hay una sugerencia activa la toma; si no, abre el selector compartido
    // (#spl-drawer) y fusiona la selección en el carrito (un renglón por producto, sin duplicados).
    const picker = window.IXSales.productPicker({
        enforceStock: true,
        onConfirm: (items) => { items.forEach(({ product, quantity }) => addToCart(product, quantity)); }
    });
    els.addProduct.addEventListener('click', (event) => {
        if (productCombo.pickActive()) return;
        if (picker) picker.open(event.currentTarget); else els.productSearch.focus();
    });

    /* ---------- Carrito ---------- */
    function lineCents(line) {
        const gross = toCents(line.product.sale_price) * line.quantity;
        const reduction = Math.round(gross * line.discount / 100);
        return { gross, reduction, net: gross - reduction };
    }
    function renderCart() {
        els.lines.replaceChildren();
        let subtotal = 0, discount = 0, index = 0;
        cart.forEach((line) => {
            const { gross, reduction, net } = lineCents(line);
            subtotal += gross; discount += reduction;
            const tr = node('tr');
            tr.dataset.productId = line.product.id;
            tr.append(node('td', String(++index), 'sa-muted'));
            const cell = node('td');
            const product = node('div', undefined, 'px-product sa-product');
            product.append(thumb(line.product, 'px-thumb sa-thumb'));
            const text = node('span', undefined, 'px-product-text');
            const name = node('span', line.product.name, 'px-product-name');
            name.title = line.product.name;
            text.append(name, node('span', line.product.code, 'px-product-sku'));
            product.append(text);
            cell.append(product);
            tr.append(cell, node('td', money(toCents(line.product.sale_price)), 'is-end'));
            const qty = node('input');
            Object.assign(qty, { type: 'number', name: 'quantity', min: '1', step: '1', value: String(line.quantity), className: 'sa-qty sv-input' });
            qty.setAttribute('aria-label', `Cantidad de ${line.product.name}`);
            qty.max = String(line.product.current_stock);
            const over = line.quantity > line.product.current_stock;
            qty.setAttribute('aria-invalid', String(over));
            qty.title = over ? `Stock disponible: ${line.product.current_stock}` : '';
            const disc = node('input');
            Object.assign(disc, { type: 'number', name: 'discount_percent', min: '0', max: '100', step: '0.01', value: String(line.discount), className: 'sa-disc sv-input' });
            disc.setAttribute('aria-label', `Descuento de ${line.product.name} (%)`);
            const remove = node('button', undefined, 'sa-remove');
            remove.type = 'button';
            remove.setAttribute('aria-label', `Quitar ${line.product.name}`);
            remove.innerHTML = '<i class="ti ti-trash" aria-hidden="true"></i>';
            const qtyCell = node('td'); qtyCell.append(qty);
            const discCell = node('td'); const wrap = node('span', undefined, 'sv-percent'); wrap.append(disc); discCell.append(wrap);
            const totalCell = node('td'); totalCell.append(node('strong', money(net)));
            const removeCell = node('td'); removeCell.append(remove);
            tr.append(qtyCell, discCell, totalCell, removeCell);
            if (over) tr.classList.add('is-over-stock');
            els.lines.append(tr);
        });
        els.empty.hidden = cart.size > 0;
        els.count.textContent = `${cart.size} producto${cart.size === 1 ? '' : 's'} agregado${cart.size === 1 ? '' : 's'}`;
        els.subtotal.textContent = money(subtotal);
        els.discount.textContent = money(discount);
        els.total.textContent = money(subtotal - discount);
        totalCents = subtotal - discount;
        renderCash();
        const overStock = [...cart.values()].filter((line) => line.quantity > line.product.current_stock);
        if (overStock.length) {
            const line = overStock[0];
            feedback(`Stock insuficiente para '${line.product.name}'. Disponible: ${line.product.current_stock}.`, 'warning');
        }
    }
    els.lines.addEventListener('change', (event) => {
        const tr = event.target.closest('tr');
        const line = tr && cart.get(Number(tr.dataset.productId));
        if (!line) return;
        if (event.target.name === 'quantity') {
            const value = Number(event.target.value);
            line.quantity = Number.isInteger(value) && value >= 1 ? value : 1;
        } else if (event.target.name === 'discount_percent') {
            const value = Number(event.target.value);
            line.discount = Number.isFinite(value) ? Math.min(100, Math.max(0, Math.round(value * 100) / 100)) : 0;
        }
        feedback('', null);
        renderCart();
    });
    els.lines.addEventListener('click', (event) => {
        const button = event.target.closest('.sa-remove');
        if (!button) return;
        cart.delete(Number(button.closest('tr').dataset.productId));
        if (!cart.size) idempotencyKey = null;
        feedback('', null);
        renderCart();
        els.productSearch.focus();
    });
    function resetCart() {
        cart.clear();
        idempotencyKey = null;
        els.cash.value = '';
        renderCart();
    }
    els.cancel.addEventListener('click', () => { resetCart(); feedback('', null); els.productSearch.focus(); });

    /* ---------- Guardar ---------- */
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (submitting || form.dataset.canSell !== '1') return;
        if (!els.customerId.value) { feedback('Seleccione un cliente antes de guardar la venta.', 'error'); els.customerSearch.focus(); return; }
        if (!cart.size) { feedback('Agregue al menos un producto a la venta.', 'error'); els.productSearch.focus(); return; }
        if (els.method.value === 'cash' && (cashText() === null || toCents(cashText()) < totalCents)) {
            feedback('Indique un efectivo recibido válido que cubra el total.', 'error');
            els.cash.setAttribute('aria-invalid', 'true');
            els.cash.focus();
            return;
        }
        submitting = true;
        els.save.disabled = true;
        els.save.textContent = 'Guardando…';
        const payload = {
            customer_id: Number(els.customerId.value),
            payment_method: $('sv-payment-method').value,
            payment_condition: $('sv-payment-condition').value,
            idempotency_key: idempotencyKey,
            items: [...cart.values()].map((line) => ({ product_id: line.product.id, quantity: line.quantity, discount_percent: String(line.discount) }))
        };
        if (els.method.value === 'cash') payload.cash_received = cashText();
        try {
            const sale = await api('/api/sales', { method: 'POST', body: JSON.stringify(payload) });
            resetCart();
            const cashResult = sale.change_due === null ? '' : ` Efectivo recibido: ${money(toCents(sale.cash_received))}. Vuelto: ${money(toCents(sale.change_due))}.`;
            feedback((sale.message || `Venta ${sale.sale_number} registrada correctamente.`) + cashResult, 'success');
            els.productSearch.focus();
        } catch (error) {
            feedback(error.message, 'error'); // el carrito se conserva para corregir
        } finally {
            submitting = false;
            els.save.disabled = false;
            els.save.textContent = 'Guardar';
        }
    });
    renderCart();
})();
