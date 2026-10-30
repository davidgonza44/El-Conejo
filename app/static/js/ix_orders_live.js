/* Pedidos live: configuración de la página genérica (ix_sales_docs_live.js) sobre /api/orders.
   Un pedido no descuenta ni reserva stock; "Productos" en la lista son unidades (SUM cantidad).
   Metadatos persistidos por pedido: prioridad, estado de pago (Pendiente/Pagado), teléfono, dirección
   y notas. Menú de acciones por fila: detalle, editar, cambiar estado, cambiar estado de pago,
   duplicar, imprimir y cancelar. IVA y pagos parciales no existen: no se muestran. */
(function () {
    'use strict';
    if (document.documentElement.dataset.referenceMode === '1' || !window.IXSales || !window.IXSales.documentPage) return;
    const S = window.IXSales;
    const { money, toCents, escape, node } = S;
    const { dateOnly, cell, badge, ref } = S.cells;
    const $ = (id) => document.getElementById(id);
    const TRASH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg>';
    const DOTS = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/></svg>';
    const image = (product) => (product.image_url ? `<img src="${escape(product.image_url)}" alt="">` : '<span class="ix-thumb ol-thumb" aria-hidden="true"></span>');
    const today = $('ov-live')?.dataset.today || '';
    const API = '/api/orders';
    const tone = (label, t) => { const td = node('td'); td.append(node('span', label, `px-badge sa-badge is-${t}`)); return td; };
    const field = (id) => $(`ol-order-${id}`);

    /* ---------- Detalle (drawer de solo lectura) ---------- */
    const detail = S.dialogControl($('ol-detail'), '[data-ol-close]');
    function openDetail(order, trigger) {
        const rows = [
            ['Número', order.order_number], ['Fecha', dateOnly(order.ordered_at)], ['Cliente', order.customer ? order.customer.name : '—'],
            ['Vendedor', order.seller?.name || '—'], ['Entrega estimada', dateOnly(order.estimated_delivery_date)], ['Prioridad', order.priority_label],
            ['Estado de pago', order.payment_status_label], ['Estado', order.status_label], ['Teléfono / Contacto', order.contact_phone || '—'],
            ['Dirección de entrega', order.delivery_address || '—'], ['Notas', order.notes || '—']
        ];
        $('ol-detail-desc').textContent = `Pedido ${order.order_number}`;
        $('ol-detail-fields').replaceChildren(...rows.flatMap(([k, v]) => { const dd = node('dd', v); dd.title = v; return [node('dt', k), dd]; }));
        $('ol-detail-lines').innerHTML = order.items.map((l) => `<tr><td title="${escape(l.product_name)}"><div class="ol-product">${image(l)}<span>${escape(l.product_name)}<small>SKU: ${escape(l.product_code)}</small></span></div></td><td>${money(toCents(l.unit_price))}</td><td>${l.quantity}</td><td><strong>${money(toCents(l.line_subtotal))}</strong></td></tr>`).join('');
        $('ol-detail-subtotal').textContent = money(toCents(order.subtotal));
        $('ol-detail-total').textContent = money(toCents(order.total));
        detail.open(trigger);
    }

    /* ---------- Diálogos compactos con radios (estado / estado de pago) ---------- */
    function radioDialog(id, { name, key, choices, url, done }) {
        const control = S.dialogControl($(`ol-${id}`), '[data-ol-close]');
        let current = null, trigger = null;
        function open(order, anchor) {
            current = order; trigger = anchor;
            $(`ol-${id}-desc`).textContent = `Pedido: ${order.order_number}`;
            $(`ol-${id}-error`).textContent = '';
            const items = choices(order);
            $(`ol-${id}-options`).replaceChildren(...items.map((c, i) => {
                const label = node('label', undefined, 'ol-radio');
                const input = node('input'); input.type = 'radio'; input.name = name; input.value = c.value; input.checked = c.value === order[key] || (items.every((x) => x.value !== order[key]) && i === 0);
                label.append(input, node('span', c.label));
                return label;
            }));
            control.open(anchor);
            $(`ol-${id}-options`).querySelector('input:checked')?.focus();
        }
        $(`ol-${id}-form`).addEventListener('submit', async (event) => {
            event.preventDefault();
            const value = $(`ol-${id}-options`).querySelector('input:checked')?.value;
            if (!value) { $(`ol-${id}-error`).textContent = 'Selecciona una opción.'; return; }
            if (value === current[key]) { control.close(); return; }
            const save = $(`ol-${id}-save`); save.disabled = true;
            try {
                const doc = await S.api(url(current), { method: 'PATCH', body: JSON.stringify({ [key]: value }) });
                control.close(); done(doc);
            } catch (error) { $(`ol-${id}-error`).textContent = error.message; } finally { save.disabled = false; }
        });
        return { open };
    }

    /* ---------- Menú de acciones (⋯) ---------- */
    const menu = $('ol-menu');
    const MENU_ITEMS = (o, canWrite) => [
        ['detail', 'Ver detalle', true],
        ['edit', 'Editar pedido', canWrite && o.editable],
        ['state', 'Cambiar estado', canWrite && o.allowed_transitions.length > 0],
        ['payment', 'Cambiar estado de pago', canWrite],
        ['duplicate', 'Duplicar pedido', canWrite],
        ['print', 'Imprimir', true],
        ['cancel', 'Cancelar pedido', canWrite && o.allowed_transitions.includes('cancelled')]
    ];
    let menuOrder = null, menuCtx = null;
    menu.addEventListener('click', (event) => {
        const b = event.target.closest('[data-action]');
        if (b) run(b.dataset.action);
    });
    menu.addEventListener('keydown', (event) => {
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const items = [...menu.querySelectorAll('button:not(:disabled)')];
        let next = items.indexOf(document.activeElement) + (event.key === 'ArrowUp' ? -1 : 1);
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = items.length - 1;
        items[(next + items.length) % items.length].focus();
    });
    const stateDialog = radioDialog('state', {
        name: 'ol-state', key: 'status', url: (o) => `${API}/${encodeURIComponent(o.order_number)}/status`,
        choices: (o) => o.allowed_transitions.map((v) => ({ value: v, label: menuCtx.options.statuses.find((s) => s.value === v)?.label || v })),
        done: (doc) => { menuCtx.feedback(`${doc.order_number} ahora está "${doc.status_label}".`, 'success'); menuCtx.load(); }
    });
    const paymentDialog = radioDialog('payment', {
        name: 'ol-payment', key: 'payment_status', url: (o) => `${API}/${encodeURIComponent(o.order_number)}/payment-status`,
        choices: () => menuCtx.options.payment_statuses,
        done: (doc) => { menuCtx.feedback(`${doc.order_number}: estado de pago "${doc.payment_status_label}".`, 'success'); menuCtx.load(); }
    });
    async function run(action) {
        const o = menuOrder, anchor = menuCtx.anchor, number = encodeURIComponent(o.order_number);
        menuCtx.closePopover(false);
        try {
            if (action === 'detail') openDetail(await S.api(`${API}/${number}`), anchor);
            else if (action === 'edit') menuCtx.openDrawer(anchor, await S.api(`${API}/${number}`));
            else if (action === 'state') stateDialog.open(o, anchor);
            else if (action === 'payment') paymentDialog.open(o, anchor);
            else if (action === 'print') window.open(`/sales/orders/${number}/print`, '_blank', 'noopener');
            else if (action === 'duplicate') {
                const doc = await S.api(`${API}/${number}/duplicate`, { method: 'POST', body: '{}' });
                menuCtx.feedback(doc.message, 'success'); menuCtx.load();
            } else if (action === 'cancel' && window.confirm(`¿Cancelar el pedido ${o.order_number}? Esta acción no se puede deshacer.`)) {
                const doc = await S.api(`${API}/${number}/status`, { method: 'PATCH', body: JSON.stringify({ status: 'cancelled' }) });
                menuCtx.feedback(`${doc.order_number} ahora está "${doc.status_label}".`, 'success'); menuCtx.load();
            }
        } catch (error) { menuCtx.feedback(error.message, 'error'); }
    }
    function actions(o, ctx) {
        const td = node('td', undefined, 'sv-actions ov-actions');
        const b = node('button', undefined, 'ov-dots');
        b.type = 'button'; b.innerHTML = DOTS;
        b.setAttribute('aria-label', `Acciones de ${o.order_number}`); b.setAttribute('aria-haspopup', 'menu'); b.setAttribute('aria-controls', 'ol-menu');
        b.addEventListener('click', () => {
            menuOrder = o; menuCtx = { ...ctx, anchor: b, get options() { return ctx.options; } };
            menu.replaceChildren(...MENU_ITEMS(o, ctx.canWrite).flatMap(([action, label, enabled]) => {
                const item = node('button', label, action === 'cancel' ? 'is-danger' : undefined);
                item.type = 'button'; item.dataset.action = action; item.setAttribute('role', 'menuitem'); item.disabled = !enabled;
                return action === 'cancel' ? [node('hr'), item] : [item];
            }));
            ctx.openPopover(menu, b);
        });
        td.append(b);
        return td;
    }

    /* ---------- Página ---------- */
    S.documentPage({
        prefix: 'ol', root: 'ov-live', api: API, numberKey: 'order_number', noun: ['pedido', 'pedidos'],
        ids: { rows: 'ov-rows', empty: 'ov-empty', summary: 'ov-summary', pages: 'ov-pages', feedback: 'ov-feedback' },
        statusKey: 'statuses',
        statusOrder: ['pending', 'processing', 'completed', 'cancelled'],
        statusColors: { '': '#e07b10', pending: '#c98c00', processing: '#c98c00', completed: '#32af58', cancelled: '#eb4b55' },
        cells: (o) => [ref(o.order_number), cell(dateOnly(o.ordered_at), 'sa-muted'), cell(o.customer ? o.customer.name : '—', 'sa-muted', o.customer && o.customer.name),
            cell(String(o.units), undefined, `${o.line_count} renglón${o.line_count === 1 ? '' : 'es'} · total ${money(toCents(o.total))}`), cell(dateOnly(o.estimated_delivery_date), 'sa-muted'),
            tone(o.priority_label, o.priority_tone), tone(o.payment_status_label, o.payment_status_tone), badge(o)],
        actions,
        moreFields: [
            { id: 'client', param: 'customer_id', kind: 'customer' }, { id: 'product', param: 'product_id', kind: 'product' },
            { id: 'seller', param: 'seller_id' }, { id: 'priority', param: 'priority' }, { id: 'payment', param: 'payment_status' },
            { id: 'from', param: 'delivery_from' }, { id: 'to', param: 'delivery_to' },
            { id: 'min', param: 'units_min' }, { id: 'max', param: 'units_max' }
        ],
        moreInvalid: (v) => ((v.delivery_from && v.delivery_to && v.delivery_from > v.delivery_to) || (v.units_min && v.units_max && Number(v.units_min) > Number(v.units_max))
            ? 'El valor inicial no puede superar al final.' : ''),
        drawer: {
            form: 'order-form', error: 'order-error', save: 'save', emptyText: 'Agrega productos al pedido.',
            lineHtml: ({ product, quantity }) => `<tr data-product="${product.id}"><td><div class="ol-product">${image(product)}<span>${escape(product.name)}<small>SKU: ${escape(product.code)}</small></span></div></td>
                <td>${money(toCents(product.sale_price))}</td>
                <td><div class="ol-qty"><button type="button" data-step="-1" aria-label="Reducir ${escape(product.name)}" ${quantity === 1 ? 'disabled' : ''}>−</button><input type="number" min="1" max="9999" step="1" data-qty value="${quantity}" aria-label="Cantidad ${escape(product.name)}"><button type="button" data-step="1" aria-label="Aumentar ${escape(product.name)}" ${quantity === 9999 ? 'disabled' : ''}>+</button></div></td>
                <td><strong>${money(toCents(product.sale_price) * quantity)}</strong></td>
                <td><button type="button" class="ol-delete" data-remove aria-label="Eliminar ${escape(product.name)}">${TRASH}</button></td></tr>`,
            totals(lines) {
                const subtotal = lines.reduce((sum, l) => sum + toCents(l.product.sale_price) * l.quantity, 0);
                $('ol-subtotal-label').textContent = `Subtotal (${lines.length} producto${lines.length === 1 ? '' : 's'})`;
                $('ol-subtotal').textContent = money(subtotal);
                $('ol-total').textContent = money(subtotal); // sin IVA en esta versión
            },
            reset(order) {
                const editing = Boolean(order);
                $('ol-new-title').textContent = editing ? 'Editar pedido' : 'Nuevo pedido';
                $('ol-new-desc').textContent = editing ? `Pedido ${order.order_number} · ${order.status_label}` : 'Registra un nuevo pedido de cliente';
                $('ol-save').lastChild.textContent = editing ? 'Guardar cambios' : 'Guardar pedido';
                field('delivery').value = editing ? order.estimated_delivery_date : today;
                field('delivery').min = today <= field('delivery').value ? today : field('delivery').value;
                field('status').value = editing ? order.status : 'pending';
                field('status').disabled = editing; // el estado operativo cambia desde "Cambiar estado"
                field('priority').value = editing ? order.priority : 'medium';
                field('payment').value = editing ? order.payment_status : 'pending';
                field('phone').value = editing ? order.contact_phone || '' : '';
                field('address').value = editing ? order.delivery_address || '' : '';
                field('notes').value = editing ? order.notes || '' : '';
            },
            payload(order) {
                const delivery = field('delivery').value;
                if (!/^\d{4}-\d{2}-\d{2}$/.test(delivery)) return 'Indica la fecha estimada de entrega.';
                if (delivery < today && !(order && delivery === order.estimated_delivery_date)) return 'La entrega no puede ser anterior a la fecha del pedido.';
                const payload = {
                    estimated_delivery_date: delivery, priority: field('priority').value, payment_status: field('payment').value,
                    contact_phone: field('phone').value.trim() || null, delivery_address: field('address').value.trim() || null, notes: field('notes').value.trim() || null
                };
                if (!order) payload.status = field('status').value;
                else if (delivery === order.estimated_delivery_date) delete payload.estimated_delivery_date; // una entrega pasada no se revalida
                return payload;
            }
        }
    });
    // Al elegir cliente en un pedido nuevo, sugerir su teléfono (editable; se guarda por pedido).
    $('ol-customer-id').addEventListener('picked', (event) => { if (!field('phone').value && event.detail.phone) field('phone').value = event.detail.phone; });
})();
