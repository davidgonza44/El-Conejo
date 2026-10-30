/* Disposable orders reference: no network, storage, or business operations. */
(() => {
    'use strict';
    const source = document.getElementById('ol-fixture');
    if (!source || document.documentElement.dataset.referenceMode !== '1') return;
    const $ = id => document.getElementById(id);
    const main = $('ix-main');
    const chips = [...main.querySelectorAll('.sa-chip')];
    const triggers = [main.querySelector('.sa-toolbar .is-primary'), ...chips];
    const layers = [$('ol-drawer'), $('ol-dates'), $('ol-status'), $('ol-more')];
    const body = main.querySelector('tbody');
    const summary = main.querySelector('.sa-pagination-summary');
    const pages = main.querySelector('.sa-pages');
    const search = main.querySelector('.sa-toolbar-search input');
    const originalRows = body.innerHTML, originalPages = pages.innerHTML, originalSummary = summary.textContent;
    const statuses = ['Todos los estados', 'Pendiente', 'En proceso', 'Completado', 'Cancelado'];
    const colors = ['#2473ee', '#f5b34b', '#f29927', '#32af58', '#eb4b55'];
    const fields = ['client', 'product', 'seller', 'priority', 'from', 'to', 'payment', 'min', 'max'];
    const catalog = [
        {name:'Cemento Gris 42.5 kg', sku:'CEM-001', cents:750, image:'cemento'},
        {name:'Tubo PVC 1/2" x 3m', sku:'PVC-001', cents:230, image:'tubo-pvc'},
    ];
    const iso = d => d.toISOString().slice(0, 10);
    const date = s => new Date(s + 'T12:00:00Z');
    const display = s => s ? s.split('-').reverse().join('/') : 'Selecciona una fecha';
    const fromDisplay = s => s.split('/').reverse().join('-');
    const validDate = s => /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(date(s).valueOf()) && iso(date(s)) === s;
    const shift = (s, n) => { const d = date(s); d.setUTCDate(d.getUTCDate() + n); return iso(d); };
    const money = cents => '$' + (cents / 100).toFixed(2);
    const escape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const seed = JSON.parse(source.textContent);
    // Preserve the approved first page; complete its 67-item pagination deterministically.
    const orders = Array.from({length:67}, (_, i) => {
        const r = seed[i % seed.length];
        return {number:'PED-' + String(67 - i).padStart(6, '0'), date:i < 10 ? fromDisplay(r[1]) : shift('2024-05-25', -Math.floor((i - 10) / 2)),
            client:r[2], count:Number(r[3]), delivery:fromDisplay(r[4]), status:r[5][0], tone:r[5][1],
            product:catalog[i % 2].name, seller:i % 2 ? 'María Pérez' : 'Administrador',
            priority:['Normal','Alta','Baja'][i % 3], payment:['Pendiente','Parcial','Pagado'][i % 3]};
    });
    let active = null, returnFocus = null, oldOverflow = '', page = 1;
    let filters = {start:'', end:'', status:'', more:{}};
    let draft = {start:'2024-05-20', end:'2024-06-06'}, month = '2024-05-01', pickingEnd = false;
    let products = [{id:0, qty:10}, {id:1, qty:5}];

    function label(button, text) {
        const node = [...button.childNodes].find(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
        if (node) node.textContent = text;
    }
    function close(restore = true) {
        if (!active) return;
        if (active instanceof HTMLDialogElement) active.close(); else active.hidden = true;
        triggers.forEach(t => t.setAttribute('aria-expanded', 'false'));
        document.body.style.overflow = oldOverflow;
        active = null;
        if (restore) returnFocus?.focus();
    }
    function position() {
        if (!active || active instanceof HTMLDialogElement) return;
        const rect = returnFocus.getBoundingClientRect();
        active.style.left = Math.max(12, Math.min(rect.left, innerWidth - active.offsetWidth - 12)) + 'px';
        active.style.top = Math.max(12, Math.min(rect.bottom + 5, innerHeight - active.offsetHeight - 12)) + 'px';
    }
    function open(index) {
        const layer = layers[index];
        if (active === layer) { close(); return; }
        close(false); returnFocus = triggers[index]; oldOverflow = document.body.style.overflow;
        if (index === 1) {
            draft = filters.start ? {start:filters.start, end:filters.end} : {start:'2024-05-20', end:'2024-06-06'};
            month = draft.start.slice(0, 7) + '-01'; pickingEnd = false; calendars();
            document.querySelectorAll('[data-ol-quick]').forEach(b => b.classList.toggle('is-active', b.dataset.olQuick === '5'));
        }
        if (index === 2) renderStatuses();
        if (index === 3) { fields.forEach(f => $('ol-filter-' + f).value = filters.more[f] || ''); $('ol-filter-error').textContent = ''; }
        active = layer; returnFocus.setAttribute('aria-expanded', 'true');
        if (layer instanceof HTMLDialogElement) { document.body.style.overflow = 'hidden'; layer.showModal(); }
        else { layer.hidden = false; position(); layer.querySelector('button')?.focus(); }
    }
    triggers.forEach((trigger, i) => {
        trigger.setAttribute('aria-controls', layers[i].id);
        trigger.setAttribute('aria-haspopup', i === 2 ? 'listbox' : 'dialog');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.addEventListener('click', () => open(i));
    });
    layers.forEach(layer => {
        layer.querySelectorAll('[data-ol-close]').forEach(b => b.addEventListener('click', () => close()));
        layer.addEventListener('cancel', e => { e.preventDefault(); close(); });
        layer.addEventListener('click', e => {
            if (!(layer instanceof HTMLDialogElement) || e.target !== layer) return;
            const r = layer.getBoundingClientRect();
            if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close();
        });
    });
    document.addEventListener('pointerdown', e => {
        if (active && !(active instanceof HTMLDialogElement) && !active.contains(e.target) && !triggers.some(t => t.contains(e.target))) close();
    });
    document.addEventListener('keydown', e => {
        if (!active) return;
        if (e.key === 'Escape') { e.preventDefault(); close(); return; }
        if (e.key !== 'Tab' || !(active instanceof HTMLDialogElement)) return;
        const focusable = [...active.querySelectorAll('button,input,select,textarea')].filter(el => !el.disabled && el.getClientRects().length);
        const first = focusable[0], last = focusable.at(-1);
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    addEventListener('resize', position);
    addEventListener('scroll', position, true);

    function matches(order) {
        const m = filters.more;
        const equal = ['client','product','seller','priority','payment'].every(k => !m[k] || m[k] === order[k]);
        return equal && (!filters.status || order.status === filters.status)
            && (!filters.start || order.date >= filters.start) && (!filters.end || order.date <= filters.end)
            && (!m.from || order.delivery >= m.from) && (!m.to || order.delivery <= m.to)
            && (!m.min || order.count >= Number(m.min)) && (!m.max || order.count <= Number(m.max))
            && (!search.value || [order.number, order.client, order.product].some(v => v.toLocaleLowerCase('es').includes(search.value.toLocaleLowerCase('es'))));
    }
    function renderTable() {
        const rows = orders.filter(matches), totalPages = Math.max(1, Math.ceil(rows.length / 10));
        page = Math.min(page, totalPages);
        const start = (page - 1) * 10;
        body.innerHTML = rows.slice(start, start + 10).map(r => `<tr><td><span class="sa-ref">${r.number}</span></td><td class="sa-muted">${display(r.date)}</td><td class="sa-muted">${escape(r.client)}</td><td>${r.count}</td><td class="sa-muted">${display(r.delivery)}</td><td><span class="px-badge sa-badge is-${r.tone}">${r.status}</span></td></tr>`).join('') || '<tr><td colspan="6">No se encontraron pedidos con estos filtros.</td></tr>';
        summary.textContent = `Mostrando ${rows.length ? start + 1 : 0} a ${Math.min(start + 10, rows.length)} de ${rows.length} pedidos`;
        const button = (n, text, name, disabled = false) => `<button type="button" class="px-pg${n === page && /^\d+$/.test(text) ? ' is-active' : ''}" data-ol-page="${n}" aria-label="${name}" ${disabled ? 'disabled' : ''} ${n === page && /^\d+$/.test(text) ? 'aria-current="page"' : ''}>${text}</button>`;
        pages.innerHTML = button(page - 1, '‹', 'Página anterior', page === 1) + Array.from({length:totalPages}, (_, i) => button(i + 1, String(i + 1), `Página ${i + 1}`)).join('') + button(page + 1, '›', 'Página siguiente', page === totalPages);
    }
    function apply() {
        page = 1; renderTable();
        label(chips[0], filters.start ? `${display(filters.start)} – ${display(filters.end)}` : 'Todas las fechas');
        label(chips[1], filters.status || 'Todos los estados');
        const count = Object.values(filters.more).filter(Boolean).length;
        label(chips[2], count ? `Más filtros (${count})` : 'Más filtros'); close();
    }
    pages.addEventListener('click', e => {
        const button = e.target.closest('button'); if (!button || button.disabled) return;
        const text = button.textContent.trim(), name = button.getAttribute('aria-label');
        if (button.dataset.olPage) page = Number(button.dataset.olPage);
        else if (/^\d+$/.test(text)) page = Number(text);
        else page = Math.max(1, page + (name === 'Página anterior' ? -1 : 1));
        renderTable();
    });
    search.addEventListener('input', () => { page = 1; renderTable(); });
    function renderStatuses() {
        $('ol-status').innerHTML = statuses.map((s, i) => `<button type="button" role="option" aria-selected="${(filters.status || statuses[0]) === s}" data-ol-status="${i}"><span class="ol-dot" style="--dot:${colors[i]}"></span>${s}<span class="ol-check" aria-hidden="true">${(filters.status || statuses[0]) === s ? '✓' : ''}</span></button>`).join('');
    }
    $('ol-status').addEventListener('click', e => {
        const b = e.target.closest('[data-ol-status]'); if (!b) return;
        filters.status = Number(b.dataset.olStatus) ? statuses[Number(b.dataset.olStatus)] : ''; apply();
    });
    $('ol-status').addEventListener('keydown', e => {
        if (!['ArrowDown','ArrowUp','Home','End'].includes(e.key)) return;
        e.preventDefault(); const options = [...$('ol-status').querySelectorAll('button')];
        let next = options.indexOf(document.activeElement) + (e.key === 'ArrowUp' ? -1 : 1);
        if (e.key === 'Home') next = 0; if (e.key === 'End') next = options.length - 1;
        options[(next + options.length) % options.length].focus();
    });
    function monthShift(s, n) { const d = date(s); d.setUTCMonth(d.getUTCMonth() + n); return iso(d); }
    function calendar(s, second) {
        const d = date(s), offset = (d.getUTCDay() + 6) % 7;
        const title = new Intl.DateTimeFormat('es', {month:'long',year:'numeric',timeZone:'UTC'}).format(d).replace(' de ', ' ');
        let html = `<section class="ol-month"><header><button type="button" data-ol-month="-1" aria-label="Mes anterior" ${second ? 'style="visibility:hidden" tabindex="-1"' : ''}>‹</button><h3>${title}</h3><button type="button" data-ol-month="1" aria-label="Mes siguiente" ${second ? '' : 'style="visibility:hidden" tabindex="-1"'}>›</button></header><div class="ol-week">${['Lu','Ma','Mi','Ju','Vi','Sá','Do'].map(t => `<span>${t}</span>`).join('')}</div><div class="ol-days">`;
        const lastDay = date(shift(monthShift(s, 1), -1)).getUTCDate();
        const cells = Math.max(35, Math.ceil((offset + lastDay) / 7) * 7);
        for (let i = 0; i < cells; i++) {
            const day = shift(s, i - offset), other = day.slice(0, 7) !== s.slice(0, 7);
            const end = day === draft.start || day === draft.end;
            html += `<button type="button" data-ol-day="${day}" aria-label="${display(day)}" aria-pressed="${end}" class="${other ? 'is-other ' : ''}${draft.start && day >= draft.start && day <= draft.end ? 'is-range ' : ''}${end ? 'is-end' : ''}">${date(day).getUTCDate()}</button>`;
        }
        return html + '</div></section>';
    }
    function calendars() {
        $('ol-calendars').innerHTML = calendar(month, false) + calendar(monthShift(month, 1), true);
        const days = draft.end ? Math.round((date(draft.end) - date(draft.start)) / 86400000) + 1 : 0;
        $('ol-range').textContent = `${display(draft.start)} → ${display(draft.end)}${days ? ` (${days} días)` : ''}`;
        $('ol-date-apply').disabled = !draft.end;
    }
    $('ol-calendars').addEventListener('click', e => {
        const b = e.target.closest('button'); if (!b) return;
        if (b.dataset.olMonth) { month = monthShift(month, Number(b.dataset.olMonth)); calendars(); return; }
        const day = b.dataset.olDay; if (!day) return;
        if (!pickingEnd) { draft = {start:day, end:''}; pickingEnd = true; }
        else { draft = {start:day < draft.start ? day : draft.start, end:day < draft.start ? draft.start : day}; pickingEnd = false; }
        document.querySelectorAll('[data-ol-quick]').forEach(b => b.classList.toggle('is-active', b.dataset.olQuick === '5'));
        calendars(); $('ol-calendars').querySelector(`[data-ol-day="${day}"]`)?.focus();
    });
    document.querySelectorAll('[data-ol-quick]').forEach(b => b.addEventListener('click', () => {
        // "Today" is frozen to the fixture's reference clock, never the computer clock.
        const ranges = [['2024-05-31','2024-05-31'],['2024-05-25','2024-05-31'],['2024-05-02','2024-05-31'],['2024-05-01','2024-05-31'],['2024-04-01','2024-04-30']];
        const range = ranges[Number(b.dataset.olQuick)];
        if (range) { draft = {start:range[0], end:range[1]}; month = range[0].slice(0, 7) + '-01'; }
        else draft = {start:'', end:''};
        pickingEnd = false; document.querySelectorAll('[data-ol-quick]').forEach(el => el.classList.toggle('is-active', el === b)); calendars();
    }));
    $('ol-date-apply').addEventListener('click', () => { filters.start = draft.start; filters.end = draft.end; apply(); });
    $('ol-filter-form').addEventListener('submit', e => {
        e.preventDefault(); const more = Object.fromEntries(fields.map(f => [f, $('ol-filter-' + f).value]));
        if ((more.from && more.to && more.from > more.to) || (more.min && more.max && Number(more.min) > Number(more.max))) {
            $('ol-filter-error').textContent = 'El valor inicial no puede superar al final.'; return;
        }
        filters.more = more; apply();
    });
    $('ol-reset').addEventListener('click', () => {
        filters = {start:'', end:'', status:'', more:{}}; search.value = ''; $('ol-filter-form').reset(); apply();
        body.innerHTML = originalRows; pages.innerHTML = originalPages; summary.textContent = originalSummary;
    });
    function renderProducts() {
        $('ol-products').innerHTML = products.map(p => {
            const c = catalog[p.id];
            return `<tr data-product="${p.id}"><td><div class="ol-product"><img src="/static/img/reference/${c.image}.png" alt=""><span>${escape(c.name)}<small>SKU: ${c.sku}</small></span></div></td><td>${money(c.cents)}</td><td><div class="ol-qty"><button type="button" data-qty="-1" aria-label="Reducir ${escape(c.name)}" ${p.qty === 1 ? 'disabled' : ''}>−</button><input type="number" min="1" max="9999" step="1" value="${p.qty}" aria-label="Cantidad ${escape(c.name)}"><button type="button" data-qty="1" aria-label="Aumentar ${escape(c.name)}" ${p.qty === 9999 ? 'disabled' : ''}>+</button></div></td><td><strong>${money(c.cents * p.qty)}</strong></td><td><button type="button" class="ol-delete" data-remove aria-label="Eliminar ${escape(c.name)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg></button></td></tr>`;
        }).join('') || '<tr><td colspan="5">Agrega productos al pedido.</td></tr>';
        const subtotal = products.reduce((sum, p) => sum + catalog[p.id].cents * p.qty, 0), tax = Math.round(subtotal * .16);
        $('ol-subtotal-label').textContent = `Subtotal (${products.length} productos)`;
        $('ol-subtotal').textContent = money(subtotal); $('ol-tax').textContent = money(tax); $('ol-total').textContent = money(subtotal + tax);
    }
    $('ol-products').addEventListener('click', e => {
        const b = e.target.closest('button'), row = b?.closest('[data-product]'); if (!row) return;
        const id = Number(row.dataset.product), p = products.find(p => p.id === id);
        if (b.hasAttribute('data-remove')) products = products.filter(p => p.id !== id);
        else p.qty = Math.max(1, Math.min(9999, p.qty + Number(b.dataset.qty)));
        const action = b.hasAttribute('data-remove') ? '[data-remove]' : `[data-qty="${b.dataset.qty}"]`;
        renderProducts();
        const target = $('ol-products').querySelector(`[data-product="${id}"] ${action}`);
        (target && !target.disabled ? target : $('ol-add')).focus();
    });
    $('ol-products').addEventListener('change', e => {
        if (!e.target.matches('input')) return;
        const p = products.find(p => p.id === Number(e.target.closest('[data-product]').dataset.product));
        const value = Number(e.target.value);
        if (Number.isInteger(value) && value >= 1 && value <= 9999) p.qty = value;
        renderProducts(); $('ol-products').querySelector(`[data-product="${p.id}"] input`).focus();
    });
    $('ol-add').addEventListener('click', () => { $('ol-add-controls').hidden = !$('ol-add-controls').hidden; if (!$('ol-add-controls').hidden) $('ol-catalog').focus(); });
    $('ol-add-confirm').addEventListener('click', () => {
        const id = Number($('ol-catalog').value), p = products.find(p => p.id === id);
        if (p) p.qty = Math.min(9999, p.qty + 1); else products.push({id, qty:1});
        renderProducts(); $('ol-add-controls').hidden = true; $('ol-add').focus();
    });
    $('ol-order-form').addEventListener('submit', e => {
        e.preventDefault();
        if (!products.length) { $('ol-order-error').textContent = 'Agrega al menos un producto.'; return; }
        const ordered = fromDisplay($('ol-order-date').value), delivery = fromDisplay($('ol-order-delivery').value);
        if (!validDate(ordered) || !validDate(delivery)) { $('ol-order-error').textContent = 'Introduce fechas válidas en formato dd/mm/aaaa.'; return; }
        if (delivery < ordered) { $('ol-order-error').textContent = 'La entrega no puede ser anterior al pedido.'; return; }
        close(); $('ol-notice').textContent = 'Simulación completada. No se creó ningún pedido real.'; $('ol-notice').hidden = false;
        setTimeout(() => { $('ol-notice').hidden = true; }, 5000);
    });
    renderProducts();
})();
