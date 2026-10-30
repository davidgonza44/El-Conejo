/* Utilidades live compartidas por Nueva venta, Cotizaciones y Pedidos (nunca corre en ?ref=1):
   fetch JSON, formato de dinero, combobox de búsqueda (clientes/productos vía API), selector de
   productos (drawer #spl-*) y calendario de rango con presets calculados sobre el día comercial. */
(function () {
    'use strict';
    if (document.documentElement.dataset.referenceMode === '1') return;

    async function api(url, options = {}) {
        const headers = { ...(options.headers || {}) };
        if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
        const response = await fetch(url, { credentials: 'same-origin', ...options, headers });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(data.error || (response.status === 403 ? 'No tiene permiso para esta acción.' : 'No se pudo completar la operación.'));
            error.status = response.status;
            throw error;
        }
        return data;
    }
    const money = (cents) => `$ ${(cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    const toCents = (text) => Math.round(Number(text) * 100);
    const escape = (text) => String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const display = (iso) => (iso ? iso.split('-').reverse().join('/') : '');
    function node(tag, text, className) {
        const el = document.createElement(tag);
        if (text !== undefined && text !== null) el.textContent = text;
        if (className) el.className = className;
        return el;
    }
    const STOCK_TONE = { disponible: 'green', bajo_stock: 'orange', agotado: 'red', inactivo: 'gray' };
    function thumb(product, className) {
        const wrap = node('span', undefined, `ix-thumb ${className}`);
        if (product.image_url) {
            const img = node('img');
            img.src = product.image_url; img.alt = '';
            img.addEventListener('error', () => img.remove(), { once: true });
            wrap.append(img);
        }
        return wrap;
    }

    /* ---------- Combobox + listbox con búsqueda en la API (debounce 250 ms) ---------- */
    function combo(input, list, fetchItems, renderItem, onPick, onError) {
        let items = [];
        let active = -1;
        let timer;
        let requestId = 0;
        const close = () => { list.hidden = true; list.replaceChildren(); input.setAttribute('aria-expanded', 'false'); active = -1; };
        const highlight = (index) => {
            active = index;
            [...list.children].forEach((li, i) => li.setAttribute('aria-selected', String(i === index)));
            if (index >= 0) list.children[index].scrollIntoView({ block: 'nearest' });
        };
        const pick = (index) => { if (items[index]) { onPick(items[index]); close(); } };
        async function load() {
            const current = ++requestId;
            let result;
            try { result = await fetchItems(input.value.trim()); } catch (error) { if (onError) onError(error); return; }
            if (current !== requestId) return;
            items = result;
            list.replaceChildren();
            if (!items.length) list.append(node('li', 'Sin resultados.', 'sv-list-empty'));
            items.forEach((item, index) => {
                const li = renderItem(item);
                li.setAttribute('role', 'option');
                li.setAttribute('aria-selected', 'false');
                li.addEventListener('mousedown', (event) => { event.preventDefault(); pick(index); });
                list.append(li);
            });
            list.hidden = false;
            input.setAttribute('aria-expanded', 'true');
            highlight(items.length ? 0 : -1);
        }
        input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 250); });
        input.addEventListener('focus', () => { if (list.hidden) load(); });
        input.addEventListener('blur', () => setTimeout(close, 120));
        input.addEventListener('keydown', (event) => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (list.hidden) { load(); return; }
                highlight(event.key === 'ArrowDown' ? Math.min(active + 1, items.length - 1) : Math.max(active - 1, 0));
            } else if (event.key === 'Enter') {
                event.preventDefault();
                if (!list.hidden && active >= 0) pick(active);
            } else if (event.key === 'Escape') { close(); }
        });
        return { pickActive: () => { if (!list.hidden && active >= 0) { pick(active); return true; } return false; } };
    }
    function customerOption(customer) {
        const li = node('li', undefined, 'sv-option');
        li.append(node('span', customer.name, 'sv-option-title'));
        li.append(node('span', [customer.tax_id, customer.phone].filter(Boolean).join(' · ') || (customer.is_walk_in ? 'Cliente genérico' : '—'), 'sv-option-desc'));
        return li;
    }
    function productOption(product) {
        const li = node('li', undefined, 'sv-option sv-option-product');
        li.append(thumb(product, 'sv-option-thumb'));
        const text = node('span', undefined, 'sv-option-text');
        text.append(node('span', product.name, 'sv-option-title'), node('span', `${product.code} · ${money(toCents(product.sale_price))}`, 'sv-option-desc'));
        li.append(text, node('span', `Stock: ${product.current_stock}`, `sv-option-stock is-${STOCK_TONE[product.stock_status] || 'gray'}`));
        if (product.current_stock < 1) li.classList.add('is-out');
        return li;
    }
    /* Campo "id-input + id-list + id-id (hidden)": al elegir escribe el nombre y guarda el id. */
    function pickerField(id, kind, onError) {
        const input = document.getElementById(id), list = document.getElementById(`${id}-list`), hidden = document.getElementById(`${id}-id`);
        const url = kind === 'customer'
            ? (term) => `/api/customers?status=active&per_page=10&search=${encodeURIComponent(term)}`
            : (term) => `/api/sales/products?limit=10&search=${encodeURIComponent(term)}`;
        const set = (item) => { hidden.value = item.id; input.value = item.name; input.dataset.picked = item.name; };
        combo(input, list, async (term) => (await api(url(term))).items, kind === 'customer' ? customerOption : productOption,
            (item) => { set(item); hidden.dispatchEvent(new CustomEvent('picked', { detail: item })); }, onError);
        input.addEventListener('input', () => { if (input.value !== input.dataset.picked) { hidden.value = ''; delete input.dataset.picked; } });
        return { input, hidden, set, get value() { return hidden.value; }, reset() { input.value = ''; hidden.value = ''; delete input.dataset.picked; } };
    }

    /* ---------- Diálogos: bloqueo de scroll, foco de retorno, cierre por Escape/backdrop ---------- */
    function dialogControl(dialog, closeSelector) {
        let opener = null, previousOverflow = '';
        const close = () => {
            if (!dialog.open) return;
            dialog.close();
            document.body.style.overflow = previousOverflow;
            if (opener) opener.focus();
        };
        dialog.addEventListener('cancel', (event) => { event.preventDefault(); close(); });
        dialog.querySelectorAll(closeSelector).forEach((button) => button.addEventListener('click', close));
        dialog.addEventListener('click', (event) => {
            if (event.target !== dialog) return;
            const r = dialog.getBoundingClientRect();
            if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) close();
        });
        dialog.addEventListener('keydown', (event) => {
            if (event.key !== 'Tab') return;
            const focusable = [...dialog.querySelectorAll('button,input,select,textarea,summary')].filter((el) => !el.disabled && el.getClientRects().length);
            const first = focusable[0], last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        });
        return {
            close,
            open(trigger) {
                opener = trigger || document.activeElement;
                previousOverflow = document.body.style.overflow;
                document.body.style.overflow = 'hidden';
                dialog.showModal();
            }
        };
    }

    /* ---------- Selector de productos compartido (#spl-drawer) ----------
       enforceStock: la venta limita la cantidad al stock y bloquea agotados; cotización/pedido
       solo informan el stock (el servidor tampoco lo descuenta). onConfirm([{product, quantity}]). */
    function productPicker({ enforceStock, onConfirm }) {
        const $ = (id) => document.getElementById(`spl-${id}`);
        const drawer = $('drawer');
        if (!drawer) return null;
        const control = dialogControl(drawer, '[data-spl-close]');
        const selection = new Map(); // product_id -> { product, quantity }
        const drafts = new Map();
        let products = [], timer, requestId = 0, categoriesLoaded = false;
        const feedback = (text) => { $('drawer-feedback').textContent = text; };
        const limit = (p) => (enforceStock ? p.current_stock : 9999);
        const blocked = (p) => enforceStock && p.current_stock < 1;
        const tone = (p) => (p.current_stock < 1 ? 'out' : p.stock_status === 'bajo_stock' ? 'low' : 'available');

        function row(p) {
            const selected = selection.has(p.id);
            const tr = node('tr');
            tr.dataset.id = p.id;
            tr.setAttribute('aria-selected', String(selected));
            const cell = node('td');
            const wrap = node('div', undefined, 'spl-product');
            wrap.append(thumb(p, 'spl-thumb'));
            const name = node('div', p.name, 'spl-product-name');
            name.title = p.name;
            name.append(node('small', p.code));
            wrap.append(name);
            cell.append(wrap);
            const stock = node('td', String(p.current_stock), `spl-${tone(p)}`);
            const price = node('td', money(toCents(p.sale_price)));
            const qtyCell = node('td');
            const qty = node('input');
            Object.assign(qty, { type: 'number', min: '1', max: String(Math.max(1, limit(p))), step: '1', value: String(selection.get(p.id)?.quantity || drafts.get(p.id) || 1), disabled: blocked(p) });
            qty.setAttribute('aria-label', `Cantidad de ${p.name}`);
            qtyCell.append(qty);
            const actionCell = node('td');
            const button = node('button', selected ? 'Agregado' : 'Agregar', 'spl-button');
            button.type = 'button';
            button.disabled = blocked(p);
            button.setAttribute('aria-pressed', String(selected));
            if (blocked(p)) button.title = 'Producto agotado: no puede venderse.';
            actionCell.append(button);
            tr.append(cell, stock, price, qtyCell, actionCell);
            return tr;
        }
        function render() {
            $('products').replaceChildren(...products.map(row));
            $('empty').hidden = products.length !== 0;
        }
        function renderSelection() {
            $('count').textContent = `Productos seleccionados: ${selection.size}`;
            $('selected').replaceChildren(...[...selection.values()].map(({ product, quantity }) => node('li', `${product.name} × ${quantity}`)));
            $('confirm').disabled = selection.size === 0;
        }
        async function load() {
            const current = ++requestId;
            const params = new URLSearchParams({ limit: '50' });
            if ($('search').value.trim()) params.set('search', $('search').value.trim());
            if ($('category').value) params.set('category_id', $('category').value);
            if ($('stock').value) params.set('stock_status', $('stock').value);
            $('products').setAttribute('aria-busy', 'true');
            try {
                const data = await api(`/api/sales/products?${params}`);
                if (current !== requestId) return;
                products = data.items;
                render();
            } catch (error) {
                if (current === requestId) { products = []; render(); feedback(error.message); }
            } finally {
                if (current === requestId) $('products').setAttribute('aria-busy', 'false');
            }
        }
        async function loadCategories() {
            if (categoriesLoaded) return;
            const data = await api('/api/sales/options').catch(() => null);
            if (!data) return;
            categoriesLoaded = true;
            data.categories.forEach((c) => { const option = node('option', c.name); option.value = c.id; $('category').append(option); });
        }
        function validQuantity(input, p) {
            const value = Number(input.value);
            const ok = Number.isInteger(value) && value >= 1 && value <= Math.max(1, limit(p));
            input.setAttribute('aria-invalid', String(!ok));
            feedback(ok ? '' : `Cantidad inválida para ${p.name}. Ingresa un entero entre 1 y ${Math.max(1, limit(p))}.`);
            return ok ? value : null;
        }
        function syncRow(tr, p) {
            const selected = selection.has(p.id);
            tr.setAttribute('aria-selected', String(selected));
            const button = tr.querySelector('button');
            button.textContent = selected ? 'Agregado' : 'Agregar';
            button.setAttribute('aria-pressed', String(selected));
            renderSelection();
        }
        $('search').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 250); });
        $('category').addEventListener('change', load);
        $('stock').addEventListener('change', load);
        $('products').addEventListener('input', (event) => {
            if (!event.target.matches('input')) return;
            const tr = event.target.closest('tr'), p = products.find((item) => item.id === Number(tr.dataset.id));
            drafts.set(p.id, event.target.value);
            const value = validQuantity(event.target, p);
            if (value !== null && selection.has(p.id)) selection.get(p.id).quantity = value;
            else selection.delete(p.id);
            syncRow(tr, p);
        });
        $('products').addEventListener('click', (event) => {
            const button = event.target.closest('button');
            if (!button) return;
            const tr = button.closest('tr'), p = products.find((item) => item.id === Number(tr.dataset.id));
            const input = tr.querySelector('input');
            const value = validQuantity(input, p);
            if (value === null) { input.focus(); return; }
            if (selection.has(p.id)) selection.delete(p.id); else selection.set(p.id, { product: p, quantity: value });
            syncRow(tr, p);
        });
        $('confirm').addEventListener('click', () => {
            if (!selection.size) return;
            onConfirm([...selection.values()]);
            control.close();
        });
        return {
            open(trigger) {
                selection.clear(); drafts.clear(); products = [];
                $('search').value = ''; $('category').value = ''; $('stock').value = '';
                feedback(''); render(); renderSelection();
                control.open(trigger);
                loadCategories();
                load();
                $('search').focus();
            }
        };
    }

    /* ---------- Calendario doble con presets sobre el día comercial (America/Caracas) ----------
       prefix 'ql'|'ol' reutiliza la geometría/estilos de la referencia; onApply({start, end}). */
    function rangePicker(prefix, { today, onApply }) {
        const $ = (id) => document.getElementById(`${prefix}-${id}`);
        const layer = $('dates');
        const date = (s) => new Date(`${s}T12:00:00Z`);
        const iso = (d) => d.toISOString().slice(0, 10);
        const shift = (s, n) => { const d = date(s); d.setUTCDate(d.getUTCDate() + n); return iso(d); };
        const monthShift = (s, n) => { const d = date(s); d.setUTCMonth(d.getUTCMonth() + n); return iso(d); };
        const monthStart = (s) => `${s.slice(0, 7)}-01`;
        const presets = () => [[today, today], [shift(today, -6), today], [shift(today, -29), today],
            [monthStart(today), today], [monthStart(monthShift(monthStart(today), -1)), shift(monthStart(today), -1)]];
        let draft = { start: '', end: '' }, month = monthStart(today), pickingEnd = false, value = { start: '', end: '' };
        function calendar(s) {
            const d = date(s), offset = (d.getUTCDay() + 6) % 7;
            const title = new Intl.DateTimeFormat('es', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(d).replace(' de ', ' ');
            let html = `<section class="${prefix}-month"><header><button type="button" data-${prefix}-month="-1" aria-label="Mes anterior">‹</button><h3>${title}</h3><button type="button" data-${prefix}-month="1" aria-label="Mes siguiente">›</button></header><div class="${prefix}-week">${['lu', 'ma', 'mi', 'ju', 'vi', 'sá', 'do'].map((t) => `<span>${t}</span>`).join('')}</div><div class="${prefix}-days">`;
            const lastDay = date(shift(monthShift(s, 1), -1)).getUTCDate(), cells = Math.max(35, Math.ceil((offset + lastDay) / 7) * 7);
            for (let i = 0; i < cells; i++) {
                const day = shift(s, i - offset), other = day.slice(0, 7) !== s.slice(0, 7), end = !other && (day === draft.start || day === draft.end);
                const inRange = !other && draft.start && draft.end && day >= draft.start && day <= draft.end;
                html += `<button type="button" data-${prefix}-day="${day}" aria-label="${display(day)}" aria-pressed="${end}" class="${other ? 'is-other ' : ''}${inRange ? 'is-range ' : ''}${end ? 'is-end' : ''}">${date(day).getUTCDate()}</button>`;
            }
            return `${html}</div></section>`;
        }
        function paint() {
            $('calendars').innerHTML = calendar(month) + calendar(monthShift(month, 1));
            const format = (s) => (s ? new Intl.DateTimeFormat('es', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(date(s)).replaceAll(' de ', ' ') : 'Selecciona una fecha');
            $('range').textContent = `${format(draft.start)} – ${format(draft.end)}`;
            $('date-apply').disabled = !draft.end;
        }
        const quicks = [...layer.querySelectorAll(`[data-${prefix}-quick]`)];
        const markQuick = (index) => quicks.forEach((b) => b.classList.toggle('is-active', b.dataset[`${prefix}Quick`] === String(index)));
        $('calendars').addEventListener('click', (event) => {
            const b = event.target.closest('button');
            if (!b) return;
            const step = b.dataset[`${prefix}Month`];
            if (step) { month = monthShift(month, Number(step)); paint(); $('calendars').querySelector(`[data-${prefix}-month="${step}"]`).focus(); return; }
            const day = b.dataset[`${prefix}Day`];
            if (!day) return;
            if (!pickingEnd) { draft = { start: day, end: '' }; pickingEnd = true; }
            else { draft = { start: day < draft.start ? day : draft.start, end: day < draft.start ? draft.start : day }; pickingEnd = false; }
            markQuick(5);
            paint();
            $('calendars').querySelector(`[data-${prefix}-day="${day}"]`)?.focus();
        });
        quicks.forEach((b) => b.addEventListener('click', () => {
            const index = Number(b.dataset[`${prefix}Quick`]), range = presets()[index];
            draft = range ? { start: range[0], end: range[1] } : { start: '', end: '' };
            if (range) month = monthStart(range[0]);
            pickingEnd = false; markQuick(index); paint();
        }));
        $('date-apply').addEventListener('click', () => { value = { ...draft }; onApply(value); });
        return {
            layer,
            get value() { return value; },
            reset() { value = { start: '', end: '' }; },
            prepare() {
                draft = value.start ? { ...value } : { start: '', end: '' };
                month = monthStart(draft.start || today); pickingEnd = false;
                markQuick(value.start ? 5 : -1); paint();
            }
        };
    }

    window.IXSales = { api, money, toCents, escape, display, node, thumb, STOCK_TONE, combo, customerOption, productOption, pickerField, dialogControl, productPicker, rangePicker };
})();
