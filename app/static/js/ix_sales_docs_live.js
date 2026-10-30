/* Página live de documentos comerciales (Cotizaciones y Pedidos): lista paginada con búsqueda,
   rango de fechas, estado y "Más filtros" resueltos en el servidor; drawer de creación conectado a
   la API y cambio de estado por fila. Cada página aporta su configuración (ix_quotes_live.js,
   ix_orders_live.js). Un documento nunca toca stock: aquí solo se muestran datos de la API. */
(function () {
    'use strict';
    if (document.documentElement.dataset.referenceMode === '1' || !window.IXSales) return;
    const S = window.IXSales;
    const { node, money, toCents, escape } = S;
    const PER_PAGE = 10; // misma densidad que la referencia

    const dateOnly = (iso) => {
        if (!iso) return '—';
        if (iso.length === 10) return S.display(iso);
        const stamp = /Z|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`;
        return new Intl.DateTimeFormat('es-VE', { day: '2-digit', month: '2-digit', year: '2-digit', timeZone: 'America/Caracas' }).format(new Date(stamp)).replace(/(\d\d)$/, '20$1');
    };
    const cell = (text, className, title) => { const td = node('td', text, className); if (title) td.title = title; return td; };
    const badge = (item) => { const td = node('td'); td.append(node('span', item.status_label, `px-badge sa-badge is-${item.status_tone}`)); return td; };
    const ref = (text) => { const td = node('td'); td.append(node('span', text, 'sa-ref')); return td; };
    const strong = (text) => { const td = node('td'); td.append(node('strong', text)); return td; };
    S.cells = { dateOnly, cell, badge, ref, strong };

    S.documentPage = function documentPage(cfg) {
        const root = document.getElementById(cfg.root);
        if (!root) return;
        const $ = (id) => document.getElementById(id);
        const p = (id) => $(`${cfg.prefix}-${id}`);
        const canWrite = root.dataset.canSell === '1';
        const today = root.dataset.today;
        const els = {
            rows: $(cfg.ids.rows), empty: $(cfg.ids.empty), summary: $(cfg.ids.summary), pages: $(cfg.ids.pages), feedback: $(cfg.ids.feedback),
            search: root.querySelector('.sa-toolbar-search input'), primary: root.querySelector('.sa-toolbar .is-primary'),
            filterButton: root.querySelector('.sa-toolbar .sa-icon-btn'), chips: [...root.querySelectorAll('.sa-chip')]
        };
        const state = { page: 1, search: '', status: '', more: {} };
        let options = null, requestId = 0, searchTimer, feedbackTimer, popover = null, popoverAnchor = null;

        function feedback(text, tone) {
            els.feedback.textContent = text;
            els.feedback.className = `sv-feedback${tone ? ` is-${tone}` : ''}`;
            els.feedback.hidden = !text;
            clearTimeout(feedbackTimer);
            if (tone === 'success') feedbackTimer = setTimeout(() => { els.feedback.hidden = true; }, 8000);
        }
        function chipLabel(chip, text) {
            const textNode = [...chip.childNodes].find((n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
            if (textNode) textNode.textContent = text;
        }

        /* ---------- Capas flotantes (fechas, estado) ancladas al chip ---------- */
        function place(layer, anchor) {
            const r = anchor.getBoundingClientRect();
            layer.style.left = `${Math.max(12, Math.min(r.left, innerWidth - layer.offsetWidth - 12))}px`;
            const below = r.bottom + 5, above = r.top - layer.offsetHeight - 5;
            layer.style.top = `${Math.max(12, Math.min(below + layer.offsetHeight > innerHeight && above > 12 ? above : below, innerHeight - layer.offsetHeight - 12))}px`;
        }
        function closePopover(restoreFocus = true) {
            if (!popover) return;
            popover.hidden = true;
            popoverAnchor.setAttribute('aria-expanded', 'false');
            if (restoreFocus) popoverAnchor.focus();
            popover = null; popoverAnchor = null;
        }
        function openPopover(layer, anchor) {
            if (popover === layer) { closePopover(); return; }
            closePopover(false);
            popover = layer; popoverAnchor = anchor;
            layer.hidden = false;
            anchor.setAttribute('aria-expanded', 'true');
            place(layer, anchor);
            (layer.querySelector('[aria-selected="true"]') || layer.querySelector('button'))?.focus();
        }
        document.addEventListener('pointerdown', (event) => {
            if (popover && !popover.contains(event.target) && !popoverAnchor.contains(event.target)) closePopover(false);
        });
        document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && popover) { event.preventDefault(); closePopover(); } });
        addEventListener('resize', () => { if (popover) place(popover, popoverAnchor); });

        /* ---------- Rango de fechas (calendario doble, presets sobre el día comercial) ---------- */
        const range = S.rangePicker(cfg.prefix, {
            today,
            onApply(value) {
                chipLabel(els.chips[0], value.start ? `${S.display(value.start)} – ${S.display(value.end)}` : 'Todas las fechas');
                closePopover(); state.page = 1; load();
            }
        });
        range.layer.querySelectorAll(`[data-${cfg.prefix}-close]`).forEach((b) => b.addEventListener('click', () => closePopover()));
        els.chips[0].setAttribute('aria-controls', range.layer.id);
        els.chips[0].setAttribute('aria-haspopup', 'dialog');
        els.chips[0].addEventListener('click', () => { range.prepare(); openPopover(range.layer, els.chips[0]); });

        /* ---------- Estado (listbox) ---------- */
        const statusLayer = p('status');
        function renderStatuses() {
            const items = [{ value: '', label: 'Todos los estados' }, ...cfg.statusOrder.map((value) => options[cfg.statusKey].find((s) => s.value === value)).filter(Boolean)];
            statusLayer.replaceChildren(...items.map((s) => {
                const b = node('button', undefined, undefined);
                b.type = 'button'; b.setAttribute('role', 'option'); b.dataset.value = s.value;
                const selected = state.status === s.value;
                b.setAttribute('aria-selected', String(selected));
                const dot = node('span', undefined, `${cfg.prefix}-dot`);
                dot.style.setProperty('--dot', cfg.statusColors[s.value] || '#1675ff');
                b.append(dot, document.createTextNode(s.label), node('span', selected ? '✓' : '', `${cfg.prefix}-check`));
                return b;
            }));
        }
        statusLayer.addEventListener('click', (event) => {
            const b = event.target.closest('[role="option"]');
            if (!b) return;
            state.status = b.dataset.value;
            chipLabel(els.chips[1], b.textContent.replace('✓', '').trim());
            closePopover(); state.page = 1; load();
        });
        statusLayer.addEventListener('keydown', (event) => {
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const items = [...statusLayer.querySelectorAll('button')];
            let next = items.indexOf(document.activeElement) + (event.key === 'ArrowUp' ? -1 : 1);
            if (event.key === 'Home') next = 0;
            if (event.key === 'End') next = items.length - 1;
            items[(next + items.length) % items.length].focus();
        });
        els.chips[1].setAttribute('aria-controls', statusLayer.id);
        els.chips[1].addEventListener('click', () => { if (options) { renderStatuses(); openPopover(statusLayer, els.chips[1]); } });

        /* ---------- Más filtros (diálogo) ---------- */
        const more = S.dialogControl(p('more'), `[data-${cfg.prefix}-close]`);
        const fields = cfg.moreFields.map((f) => ({ ...f, el: p(`filter-${f.id}`), picker: f.kind ? S.pickerField(`${cfg.prefix}-filter-${f.id}`, f.kind, (e) => feedback(e.message, 'error')) : null }));
        const fieldValue = (f) => (f.picker ? f.picker.value : f.el.value.trim());
        const openMore = (trigger) => {
            fields.forEach((f) => { if (f.picker) { if (!state.more[f.param]) f.picker.reset(); } else f.el.value = state.more[f.param] || ''; });
            p('filter-error').textContent = '';
            more.open(trigger);
        };
        els.chips[2].setAttribute('aria-controls', p('more').id);
        els.chips[2].setAttribute('aria-haspopup', 'dialog');
        els.chips[2].addEventListener('click', () => openMore(els.chips[2]));
        if (els.filterButton) els.filterButton.addEventListener('click', () => openMore(els.filterButton));
        p('filter-form').addEventListener('submit', (event) => {
            event.preventDefault();
            const values = Object.fromEntries(fields.map((f) => [f.param, fieldValue(f)]));
            const bad = cfg.moreInvalid(values);
            if (bad) { p('filter-error').textContent = bad; return; }
            state.more = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ''));
            const count = Object.keys(state.more).length;
            chipLabel(els.chips[2], count ? `Más filtros (${count})` : 'Más filtros');
            more.close(); state.page = 1; load();
        });
        p('reset').addEventListener('click', () => {
            fields.forEach((f) => (f.picker ? f.picker.reset() : (f.el.value = '')));
            state.more = {}; state.status = ''; state.search = ''; els.search.value = ''; range.reset();
            chipLabel(els.chips[0], 'Todas las fechas'); chipLabel(els.chips[1], 'Todos los estados'); chipLabel(els.chips[2], 'Más filtros');
            p('filter-error').textContent = '';
            more.close(); state.page = 1; load();
        });

        /* ---------- Lista ---------- */
        function pageButton(text, page, opts = {}) {
            const button = node('button', text, `px-pg${opts.active ? ' is-active' : ''}`);
            button.type = 'button';
            if (opts.label) { button.setAttribute('aria-label', opts.label); button.innerHTML = `<i class="ti ${opts.icon}" aria-hidden="true"></i>`; }
            if (opts.active) button.setAttribute('aria-current', 'page');
            button.disabled = opts.disabled === true;
            button.addEventListener('click', () => { state.page = page; load(); });
            return button;
        }
        function renderPages(data) {
            els.pages.replaceChildren();
            const pages = Math.max(data.pages, 1);
            els.pages.append(pageButton('', Math.max(1, data.page - 1), { label: 'Página anterior', icon: 'ti-chevron-left', disabled: data.page <= 1 }));
            const visible = [...new Set([1, pages, data.page - 1, data.page, data.page + 1].filter((n) => n >= 1 && n <= pages))].sort((a, b) => a - b);
            let previous = 0;
            visible.forEach((n) => {
                if (n - previous > 1) { const gap = node('span', '...', 'px-pg is-gap'); gap.setAttribute('aria-hidden', 'true'); els.pages.append(gap); }
                els.pages.append(pageButton(String(n), n, { active: n === data.page }));
                previous = n;
            });
            els.pages.append(pageButton('', Math.min(pages, data.page + 1), { label: 'Página siguiente', icon: 'ti-chevron-right', disabled: data.page >= pages }));
            const first = data.total ? (data.page - 1) * data.per_page + 1 : 0;
            els.summary.textContent = data.total ? `Mostrando ${first} a ${first + data.count - 1} de ${data.total} ${cfg.noun[1]}` : `Sin ${cfg.noun[1]} registradas`;
        }
        function actionsCell(item) {
            if (cfg.actions) return cfg.actions(item, ctx);
            const td = node('td', undefined, 'sv-actions');
            const transitions = item.allowed_transitions || [];
            if (!canWrite || !transitions.length) { td.textContent = '—'; return td; }
            const select = node('select', undefined, 'sv-status-select');
            select.setAttribute('aria-label', `Cambiar estado de ${item[cfg.numberKey]}`);
            select.append(node('option', 'Cambiar estado…'));
            transitions.forEach((value) => {
                const option = node('option', options ? options[cfg.statusKey].find((s) => s.value === value)?.label || value : value);
                option.value = value;
                select.append(option);
            });
            select.addEventListener('change', async () => {
                if (!select.value) return;
                select.disabled = true;
                try {
                    const doc = await S.api(`${cfg.api}/${encodeURIComponent(item[cfg.numberKey])}/status`, { method: 'PATCH', body: JSON.stringify({ status: select.value }) });
                    feedback(`${item[cfg.numberKey]} ahora está "${doc.status_label}".`, 'success');
                    load();
                } catch (error) {
                    feedback(error.message, 'error');
                    select.value = ''; select.disabled = false;
                }
            });
            td.append(select);
            return td;
        }
        function renderRows(items) {
            els.rows.replaceChildren(...items.map((item) => {
                const tr = node('tr');
                tr.append(...cfg.cells(item), actionsCell(item));
                return tr;
            }));
            els.empty.hidden = items.length > 0;
        }
        async function load() {
            const current = ++requestId;
            els.rows.setAttribute('aria-busy', 'true');
            const params = new URLSearchParams({ page: String(state.page), per_page: String(PER_PAGE) });
            if (state.search) params.set('search', state.search);
            if (state.status) params.set('status', state.status);
            if (range.value.start) { params.set('date_from', range.value.start); params.set('date_to', range.value.end); }
            Object.entries(state.more).forEach(([key, value]) => params.set(key, value));
            try {
                const data = await S.api(`${cfg.api}?${params}`);
                if (current !== requestId) return;
                renderRows(data.items);
                renderPages(data);
            } catch (error) {
                if (current !== requestId) return;
                els.rows.replaceChildren(); els.pages.replaceChildren();
                els.summary.textContent = error.message;
            } finally {
                if (current === requestId) els.rows.setAttribute('aria-busy', 'false');
            }
        }
        els.search.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => { state.search = els.search.value.trim(); state.page = 1; load(); }, 250);
        });

        /* ---------- Drawer de creación / edición ---------- */
        const drawer = S.dialogControl(p('drawer'), `[data-${cfg.prefix}-close]`);
        const customer = S.pickerField(`${cfg.prefix}-customer`, 'customer', (e) => feedback(e.message, 'error'));
        const lines = new Map(); // product_id -> { product, quantity }
        const errorBox = p(cfg.drawer.error);
        let submitting = false, editing = null, locked = false; // locked: edición sin cambios estructurales
        function renderLines() {
            p('products').innerHTML = [...lines.values()].map((line) => cfg.drawer.lineHtml(line)).join('')
                || `<tr><td colspan="5">${cfg.drawer.emptyText}</td></tr>`;
            if (locked) p('products').querySelectorAll('button,input').forEach((el) => { el.disabled = true; });
            cfg.drawer.totals([...lines.values()]);
        }
        const picker = S.productPicker({
            enforceStock: false,
            onConfirm(items) {
                items.forEach(({ product, quantity }) => {
                    const line = lines.get(product.id) || { product, quantity: 0 };
                    line.quantity = Math.min(9999, line.quantity + quantity);
                    lines.set(product.id, line);
                });
                errorBox.textContent = '';
                renderLines();
            }
        });
        p('add').addEventListener('click', (event) => picker.open(event.currentTarget));
        p('products').addEventListener('click', (event) => {
            const b = event.target.closest('button'), tr = b?.closest('[data-product]');
            if (!tr) return;
            const id = Number(tr.dataset.product), line = lines.get(id);
            if (b.hasAttribute('data-remove')) lines.delete(id);
            else if (b.dataset.step) line.quantity = Math.max(1, Math.min(9999, line.quantity + Number(b.dataset.step)));
            renderLines();
            const target = p('products').querySelector(`[data-product="${id}"] ${b.hasAttribute('data-remove') ? '[data-remove]' : `[data-step="${b.dataset.step}"]`}`);
            (target && !target.disabled ? target : p('add')).focus();
        });
        p('products').addEventListener('change', (event) => {
            if (!event.target.matches('input[data-qty]')) return;
            const line = lines.get(Number(event.target.closest('[data-product]').dataset.product));
            const value = Number(event.target.value);
            if (Number.isInteger(value) && value >= 1 && value <= 9999) line.quantity = value;
            renderLines();
            p('products').querySelector(`[data-product="${line.product.id}"] input[data-qty]`)?.focus();
        });
        if (cfg.drawer.onInput) cfg.drawer.onInput(renderLines);
        function openDrawer(trigger, item) {
            editing = item || null;
            locked = Boolean(item) && item.structural_editable === false;
            lines.clear(); customer.reset(); errorBox.textContent = '';
            if (item) {
                customer.set(item.customer);
                item.items.forEach((l) => lines.set(l.product_id, { product: { id: l.product_id, name: l.product_name, code: l.product_code, sale_price: l.unit_price, image_url: l.image_url }, quantity: l.quantity }));
            }
            customer.input.disabled = locked; p('add').disabled = locked;
            cfg.drawer.reset(item);
            renderLines();
            drawer.open(trigger);
            // En edición el cliente ya está elegido: enfocar el primer control editable (evita abrir el combobox).
            (item ? p(cfg.drawer.form).querySelector('input:not([type="search"]):not([readonly]):not(:disabled), select:not(:disabled)') : customer.input)?.focus();
        }
        els.primary.setAttribute('aria-controls', p('drawer').id);
        els.primary.setAttribute('aria-haspopup', 'dialog');
        if (!canWrite) { els.primary.disabled = true; els.primary.title = `Su rol no puede crear ${cfg.noun[1]}.`; }
        els.primary.addEventListener('click', () => openDrawer(els.primary));
        p(cfg.drawer.form).addEventListener('submit', async (event) => {
            event.preventDefault();
            if (submitting || !canWrite) return;
            if (!customer.value) { errorBox.textContent = 'Selecciona un cliente.'; customer.input.focus(); return; }
            if (!lines.size) { errorBox.textContent = 'Agrega al menos un producto.'; p('add').focus(); return; }
            const extra = cfg.drawer.payload(editing);
            if (typeof extra === 'string') { errorBox.textContent = extra; return; }
            const structural = locked ? {} : { customer_id: Number(customer.value), items: [...lines.values()].map((l) => ({ product_id: l.product.id, quantity: l.quantity })) };
            const payload = { ...structural, ...extra };
            const save = p(cfg.drawer.save);
            submitting = true; save.disabled = true; errorBox.textContent = '';
            try {
                const doc = editing
                    ? await S.api(`${cfg.api}/${encodeURIComponent(editing[cfg.numberKey])}`, { method: 'PATCH', body: JSON.stringify(payload) })
                    : await S.api(cfg.api, { method: 'POST', body: JSON.stringify(payload) });
                drawer.close();
                feedback(doc.message || `${doc[cfg.numberKey]} ${editing ? 'actualizado' : 'guardado'} correctamente.`, 'success');
                if (!editing) state.page = 1;
                load();
            } catch (error) {
                errorBox.textContent = error.message; // el borrador se conserva para corregir
            } finally {
                submitting = false; save.disabled = false;
            }
        });

        /* Contexto para acciones por fila definidas por la página (cfg.actions). */
        const ctx = { canWrite, feedback, load, openDrawer, openPopover, closePopover, node, get options() { return options; } };

        /* ---------- Arranque ---------- */
        S.api(`${cfg.api}/options`).then((data) => {
            options = data;
            const sellers = p('filter-seller');
            if (sellers) data.sellers.forEach((s) => { const option = node('option', s.name); option.value = s.id; sellers.append(option); });
            if (cfg.onOptions) cfg.onOptions(data);
            load();
        }).catch((error) => { els.summary.textContent = error.message; els.rows.setAttribute('aria-busy', 'false'); });
    };
})();
