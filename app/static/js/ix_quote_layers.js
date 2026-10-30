/* Disposable quote fixtures: no network, storage, or business operations. */
(() => {
    'use strict';
    const source = document.getElementById('ql-fixture');
    if (!source || document.documentElement.dataset.referenceMode !== '1') return;
    const $ = id => document.getElementById(id);
    const main = $('ix-main'), body = main.querySelector('tbody');
    const chips = [...main.querySelectorAll('.sa-chip')];
    const triggers = [main.querySelector('.sa-toolbar .is-primary'), ...chips];
    const layers = [$('ql-drawer'), $('ql-dates'), $('ql-status'), $('ql-more')];
    const summary = main.querySelector('.sa-pagination-summary'), pages = main.querySelector('.sa-pages');
    const search = main.querySelector('.sa-toolbar-search input');
    const original = {rows:body.innerHTML, pages:pages.innerHTML, summary:summary.textContent};
    const statuses = ['Todos los estados','Enviada','Pendiente','Aceptada','Vencida','Rechazada'];
    const colors = ['#1675ff','#539cff','#ff9729','#33b653','#fa858e','#ee273e'];
    const fields = ['client','product','seller','validity','min','max','payment','from','to'];
    const catalog = [
        {name:'Cemento Gris 42.5 kg', sku:'CEM-001', cents:850, image:'cemento'},
        {name:'Cable Eléctrico THHN 12 AWG', sku:'CAB-012', cents:1875, image:'cable-rollo'},
    ];
    const iso = d => d.toISOString().slice(0, 10);
    const date = s => new Date(s + 'T12:00:00Z');
    const display = s => s ? s.split('-').reverse().join('/') : 'dd/mm/aaaa';
    const shift = (s, n) => { const d = date(s); d.setUTCDate(d.getUTCDate() + n); return iso(d); };
    const monthShift = (s, n) => { const d = date(s); d.setUTCMonth(d.getUTCMonth() + n); return iso(d); };
    const money = cents => '$ ' + (cents / 100).toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2});
    const escape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const seed = JSON.parse(source.textContent);
    // Complete the approved 89-row fixture without changing its original first page.
    const quotes = Array.from({length:89}, (_, i) => {
        const r = seed[i % seed.length];
        return {number:'COT-' + String(89 - i).padStart(6,'0'), date:i < 10 ? r[1].split('/').reverse().join('-') : shift('2024-05-25', -Math.floor((i - 10) / 2)),
            client:r[2], validity:r[3], total:Number(r[4].replace(/[^0-9.]/g,'')), status:r[5][0], tone:r[5][1],
            product:catalog[i % 2].name, seller:i % 2 ? 'María Pérez' : 'Administrador', payment:['Pendiente','Parcial','Pagado'][i % 3]};
    });
    let active = null, returnFocus = null, oldOverflow = '', page = 1;
    let filters = {start:'', end:'', status:'', more:{}};
    let draft = {start:'2024-05-01', end:'2024-05-31'}, month = '2024-05-01', pickingEnd = false;
    let products = [{id:0,qty:50,cents:850},{id:1,qty:20,cents:1875}];
    const single = $('ql-single-calendar');
    let dateTarget = null, singleMonth = '2024-06-01';

    function label(button, text) {
        const node = [...button.childNodes].find(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
        if (node) node.textContent = text;
    }
    function closeCalendar(restore = true) {
        single.hidden = true;
        if (dateTarget) { dateTarget.setAttribute('aria-expanded','false'); if (restore) dateTarget.focus(); }
        dateTarget = null;
    }
    function close(restore = true) {
        closeCalendar(false);
        if (!active) return;
        if (active instanceof HTMLDialogElement) active.close(); else active.hidden = true;
        triggers.forEach(t => t.setAttribute('aria-expanded','false'));
        document.body.style.overflow = oldOverflow;
        active = null;
        if (restore) returnFocus?.focus();
    }
    function place(layer, anchor) {
        const r = anchor.getBoundingClientRect();
        layer.style.left = Math.max(12, Math.min(r.left, innerWidth - layer.offsetWidth - 12)) + 'px';
        const below = r.bottom + 5, above = r.top - layer.offsetHeight - 5;
        layer.style.top = Math.max(12, Math.min(below + layer.offsetHeight > innerHeight && above > 12 ? above : below, innerHeight - layer.offsetHeight - 12)) + 'px';
    }
    function position() {
        if (active && !(active instanceof HTMLDialogElement)) place(active, returnFocus);
        if (dateTarget) place(single,dateTarget);
    }
    function setDate(button, value) {
        button.dataset.qlDate = value; button.querySelector('span').textContent = display(value);
    }
    function open(index) {
        const layer = layers[index];
        if (active === layer) { close(); return; }
        close(false); returnFocus = triggers[index]; oldOverflow = document.body.style.overflow;
        if (index === 1) {
            draft = filters.start ? {start:filters.start,end:filters.end} : {start:'2024-05-01',end:'2024-05-31'};
            month = draft.start.slice(0,7) + '-01'; pickingEnd = false;
            document.querySelectorAll('[data-ql-quick]').forEach(b => b.classList.toggle('is-active', b.dataset.qlQuick === (filters.start ? '5' : '3')));
            calendars();
        }
        if (index === 2) renderStatuses();
        if (index === 3) {
            fields.forEach(f => { const el = $('ql-filter-' + f); if (el.hasAttribute('data-ql-date')) setDate(el, filters.more[f] || ''); else el.value = filters.more[f] || ''; });
            $('ql-filter-error').textContent = '';
        }
        active = layer; returnFocus.setAttribute('aria-expanded','true');
        if (layer instanceof HTMLDialogElement) { document.body.style.overflow = 'hidden'; layer.showModal(); }
        else { layer.hidden = false; position(); layer.querySelector('[aria-selected="true"],button')?.focus(); }
    }
    triggers.forEach((trigger, i) => {
        trigger.setAttribute('aria-controls',layers[i].id); trigger.setAttribute('aria-haspopup',i === 2 ? 'listbox' : 'dialog'); trigger.setAttribute('aria-expanded','false');
        trigger.addEventListener('click', () => open(i));
    });
    layers.forEach(layer => {
        layer.querySelectorAll('[data-ql-close]').forEach(b => b.addEventListener('click', () => close()));
        layer.addEventListener('cancel', e => { e.preventDefault(); if (dateTarget) closeCalendar(); else close(); });
        layer.addEventListener('click', e => {
            if (!(layer instanceof HTMLDialogElement) || e.target !== layer) return;
            const r = layer.getBoundingClientRect();
            if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close();
        });
    });
    document.addEventListener('pointerdown', e => {
        if (dateTarget && !single.contains(e.target) && !dateTarget.contains(e.target)) closeCalendar(false);
        if (active && !(active instanceof HTMLDialogElement) && !active.contains(e.target) && !triggers.some(t => t.contains(e.target))) close();
    });
    document.addEventListener('keydown', e => {
        if (!active) return;
        if (e.key === 'Escape') { e.preventDefault(); if (dateTarget) closeCalendar(); else close(); return; }
        if (e.key !== 'Tab' || !(active instanceof HTMLDialogElement)) return;
        const focusable = [...active.querySelectorAll('button,input,select,textarea')].filter(el => !el.disabled && el.getClientRects().length);
        const first = focusable[0], last = focusable.at(-1);
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    addEventListener('resize',position); addEventListener('scroll',position,true);

    function matches(quote) {
        const m = filters.more;
        return ['client','product','seller','validity','payment'].every(k => !m[k] || m[k] === quote[k])
            && (!filters.status || quote.status === filters.status)
            && (!filters.start || quote.date >= filters.start) && (!filters.end || quote.date <= filters.end)
            && (!m.from || quote.date >= m.from) && (!m.to || quote.date <= m.to)
            && (!m.min || quote.total >= Number(m.min)) && (!m.max || quote.total <= Number(m.max))
            && (!search.value || [quote.number,quote.client,quote.product].some(v => v.toLocaleLowerCase('es').includes(search.value.toLocaleLowerCase('es'))));
    }
    function renderTable() {
        const rows = quotes.filter(matches), totalPages = Math.max(1,Math.ceil(rows.length / 10));
        page = Math.max(1,Math.min(page,totalPages)); const start = (page - 1) * 10;
        body.innerHTML = rows.slice(start,start + 10).map(r => `<tr><td><span class="sa-ref">${r.number}</span></td><td class="sa-muted">${display(r.date)}</td><td class="sa-muted">${escape(r.client)}</td><td class="sa-muted">${r.validity}</td><td><strong>${money(Math.round(r.total * 100))}</strong></td><td><span class="px-badge sa-badge is-${r.tone}">${r.status}</span></td><td></td></tr>`).join('') || '<tr><td colspan="7">No se encontraron cotizaciones con estos filtros.</td></tr>';
        summary.textContent = `Mostrando ${rows.length ? start + 1 : 0} a ${Math.min(start + 10,rows.length)} de ${rows.length} cotizaciones`;
        const button = (n,text,name,disabled = false) => `<button type="button" class="px-pg${n === page && /^\d+$/.test(text) ? ' is-active' : ''}" data-ql-page="${n}" aria-label="${name}" ${disabled ? 'disabled' : ''} ${n === page && /^\d+$/.test(text) ? 'aria-current="page"' : ''}>${text}</button>`;
        pages.innerHTML = button(page - 1,'‹','Página anterior',page === 1) + Array.from({length:totalPages},(_,i) => button(i + 1,String(i + 1),`Página ${i + 1}`)).join('') + button(page + 1,'›','Página siguiente',page === totalPages);
    }
    function apply() {
        page = 1; renderTable();
        label(chips[0],filters.start ? `${display(filters.start)} – ${display(filters.end)}` : 'Todas las fechas');
        label(chips[1],filters.status || 'Todos los estados');
        const count = Object.values(filters.more).filter(Boolean).length;
        label(chips[2],count ? `Más filtros (${count})` : 'Más filtros'); close();
    }
    pages.addEventListener('click',e => {
        const b = e.target.closest('button'); if (!b || b.disabled) return;
        if (b.dataset.qlPage) page = Number(b.dataset.qlPage);
        else if (/^\d+$/.test(b.textContent.trim())) page = Number(b.textContent.trim());
        else page += b.getAttribute('aria-label') === 'Página anterior' ? -1 : 1;
        renderTable();
    });
    search.addEventListener('input',() => { page = 1; renderTable(); });
    function renderStatuses() {
        $('ql-status').innerHTML = statuses.map((s,i) => `<button type="button" role="option" aria-selected="${(filters.status || statuses[0]) === s}" data-ql-status="${i}"><span class="ql-dot" style="--dot:${colors[i]}"></span>${s}<span class="ql-check" aria-hidden="true">${(filters.status || statuses[0]) === s ? '✓' : ''}</span></button>`).join('');
    }
    $('ql-status').addEventListener('click',e => { const b = e.target.closest('[data-ql-status]'); if (!b) return; filters.status = Number(b.dataset.qlStatus) ? statuses[Number(b.dataset.qlStatus)] : ''; apply(); });
    $('ql-status').addEventListener('keydown',e => {
        if (!['ArrowDown','ArrowUp','Home','End'].includes(e.key)) return;
        e.preventDefault(); const options = [...$('ql-status').querySelectorAll('button')];
        let next = options.indexOf(document.activeElement) + (e.key === 'ArrowUp' ? -1 : 1);
        if (e.key === 'Home') next = 0; if (e.key === 'End') next = options.length - 1;
        options[(next + options.length) % options.length].focus();
    });

    function calendar(s, selection, singleMode = false) {
        const d = date(s), offset = (d.getUTCDay() + 6) % 7;
        const title = new Intl.DateTimeFormat('es',{month:'long',year:'numeric',timeZone:'UTC'}).format(d).replace(' de ',' ');
        let html = `<section class="ql-month"><header><button type="button" data-ql-month="-1" aria-label="Mes anterior">‹</button><h3>${title}</h3><button type="button" data-ql-month="1" aria-label="Mes siguiente">›</button></header><div class="ql-week">${['lu','ma','mi','ju','vi','sá','do'].map(t => `<span>${t}</span>`).join('')}</div><div class="ql-days">`;
        const lastDay = date(shift(monthShift(s,1),-1)).getUTCDate(), cells = Math.max(35,Math.ceil((offset + lastDay) / 7) * 7);
        for (let i = 0; i < cells; i++) {
            const day = shift(s,i - offset), other = day.slice(0,7) !== s.slice(0,7), end = !other && (day === selection.start || day === selection.end);
            html += `<button type="button" data-ql-day="${day}" aria-label="${display(day)}" aria-pressed="${end}" class="${other ? 'is-other ' : ''}${!other && !singleMode && selection.start && day >= selection.start && day <= selection.end ? 'is-range ' : ''}${end ? 'is-end' : ''}">${date(day).getUTCDate()}</button>`;
        }
        return html + '</div></section>';
    }
    function calendars() {
        $('ql-calendars').innerHTML = calendar(month,draft) + calendar(monthShift(month,1),draft);
        const format = s => s ? new Intl.DateTimeFormat('es',{day:'2-digit',month:'short',year:'numeric',timeZone:'UTC'}).format(date(s)).replaceAll(' de ',' ') : 'Selecciona una fecha';
        $('ql-range').textContent = `${format(draft.start)} - ${format(draft.end)}`;
        $('ql-date-apply').disabled = !draft.end;
    }
    $('ql-calendars').addEventListener('click',e => {
        const b = e.target.closest('button'); if (!b) return;
        if (b.dataset.qlMonth) { month = monthShift(month,Number(b.dataset.qlMonth)); calendars(); $('ql-calendars').querySelector(`[data-ql-month="${b.dataset.qlMonth}"]`).focus(); return; }
        const day = b.dataset.qlDay; if (!day) return;
        if (!pickingEnd) { draft = {start:day,end:''}; pickingEnd = true; }
        else { draft = {start:day < draft.start ? day : draft.start,end:day < draft.start ? draft.start : day}; pickingEnd = false; }
        document.querySelectorAll('[data-ql-quick]').forEach(b => b.classList.toggle('is-active',b.dataset.qlQuick === '5'));
        calendars(); $('ql-calendars').querySelector(`[data-ql-day="${day}"]`)?.focus();
    });
    document.querySelectorAll('[data-ql-quick]').forEach(b => b.addEventListener('click',() => {
        // The fixture clock is 31 May 2024, independent of today's system clock.
        const ranges = [['2024-05-31','2024-05-31'],['2024-05-25','2024-05-31'],['2024-05-02','2024-05-31'],['2024-05-01','2024-05-31'],['2024-04-01','2024-04-30']];
        const range = ranges[Number(b.dataset.qlQuick)];
        if (range) { draft = {start:range[0],end:range[1]}; month = range[0].slice(0,7) + '-01'; } else draft = {start:'',end:''};
        pickingEnd = false; document.querySelectorAll('[data-ql-quick]').forEach(el => el.classList.toggle('is-active',el === b)); calendars();
    }));
    $('ql-date-apply').addEventListener('click',() => { filters.start = draft.start; filters.end = draft.end; apply(); });
    document.querySelectorAll('[data-ql-date]').forEach(b => b.addEventListener('click',() => {
        if (dateTarget === b) { closeCalendar(); return; }
        closeCalendar(false); dateTarget = b; singleMonth = (b.dataset.qlDate || '2024-06-03').slice(0,7) + '-01';
        active.append(single); single.hidden = false; b.setAttribute('aria-expanded','true');
        single.innerHTML = calendar(singleMonth,{start:b.dataset.qlDate,end:''},true); position();
        (single.querySelector('.is-end') || single.querySelector('[data-ql-day]')).focus();
    }));
    single.addEventListener('click',e => {
        const b = e.target.closest('button'); if (!b || !dateTarget) return;
        if (b.dataset.qlMonth) {
            singleMonth = monthShift(singleMonth,Number(b.dataset.qlMonth)); single.innerHTML = calendar(singleMonth,{start:dateTarget.dataset.qlDate,end:''},true);
            single.querySelector(`[data-ql-month="${b.dataset.qlMonth}"]`).focus(); position(); return;
        }
        if (b.dataset.qlDay) { setDate(dateTarget,b.dataset.qlDay); closeCalendar(); }
    });
    $('ql-filter-form').addEventListener('submit',e => {
        e.preventDefault(); const more = Object.fromEntries(fields.map(f => { const el = $('ql-filter-' + f); return [f,el.hasAttribute('data-ql-date') ? el.dataset.qlDate : el.value]; }));
        if ((more.from && more.to && more.from > more.to) || (more.min && more.max && Number(more.min) > Number(more.max))) { $('ql-filter-error').textContent = 'El valor inicial no puede superar al final.'; return; }
        filters.more = more; apply();
    });
    $('ql-reset').addEventListener('click',() => {
        filters = {start:'',end:'',status:'',more:{}}; search.value = ''; $('ql-filter-form').reset();
        setDate($('ql-filter-from'),''); setDate($('ql-filter-to'),''); apply();
        body.innerHTML = original.rows; pages.innerHTML = original.pages; summary.textContent = original.summary;
    });

    function totals() {
        const subtotal = products.reduce((sum,p) => sum + p.cents * p.qty,0);
        const percentage = Math.max(0,Math.min(100,Number($('ql-discount').value) || 0));
        const discount = Math.round(subtotal * percentage / 100);
        $('ql-subtotal').textContent = money(subtotal); $('ql-discount-total').textContent = money(discount); $('ql-total').textContent = money(subtotal - discount);
    }
    function renderProducts() {
        $('ql-products').innerHTML = products.map(p => {
            const c = catalog[p.id];
            return `<tr data-product="${p.id}"><td><div class="ql-product"><img src="/static/img/reference/${c.image}.png" alt=""><span>${escape(c.name)}<small>SKU: ${c.sku}</small></span></div></td><td><input type="number" min="0" max="999999" step="0.01" data-price value="${(p.cents / 100).toFixed(2)}" aria-label="Precio ${escape(c.name)}"></td><td><input type="number" min="1" max="9999" step="1" data-qty value="${p.qty}" aria-label="Cantidad ${escape(c.name)}"></td><td><strong>${money(p.cents * p.qty)}</strong></td><td><button type="button" class="ql-delete" data-remove aria-label="Eliminar ${escape(c.name)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg></button></td></tr>`;
        }).join('') || '<tr><td colspan="5">Agrega productos a la cotización.</td></tr>';
        totals();
    }
    $('ql-products').addEventListener('click',e => {
        const b = e.target.closest('[data-remove]'); if (!b) return;
        products = products.filter(p => p.id !== Number(b.closest('[data-product]').dataset.product)); renderProducts(); $('ql-add').focus();
    });
    $('ql-products').addEventListener('change',e => {
        if (!e.target.matches('input')) return;
        const p = products.find(p => p.id === Number(e.target.closest('[data-product]').dataset.product));
        const value = Number(e.target.value), price = e.target.hasAttribute('data-price');
        if (e.target.validity.valid && Number.isFinite(value)) { if (price) p.cents = Math.round(value * 100); else p.qty = value; }
        renderProducts(); $('ql-products').querySelector(`[data-product="${p.id}"] [data-${price ? 'price' : 'qty'}]`).focus();
    });
    $('ql-discount').addEventListener('input',totals);
    $('ql-notes').addEventListener('input',() => { $('ql-note-count').textContent = `${$('ql-notes').value.length}/500`; });
    $('ql-add').addEventListener('click',() => { $('ql-add-controls').hidden = !$('ql-add-controls').hidden; if (!$('ql-add-controls').hidden) $('ql-catalog').focus(); });
    $('ql-add-confirm').addEventListener('click',() => {
        const id = Number($('ql-catalog').value), p = products.find(p => p.id === id);
        if (p) p.qty = Math.min(9999,p.qty + 1); else products.push({id,qty:1,cents:catalog[id].cents});
        renderProducts(); $('ql-add-controls').hidden = true; $('ql-add').focus();
    });
    $('ql-quote-form').addEventListener('submit',e => {
        e.preventDefault();
        if (!products.length) { $('ql-quote-error').textContent = 'Agrega al menos un producto.'; return; }
        close(); $('ql-notice').textContent = 'Simulación completada. No se creó ninguna cotización real.'; $('ql-notice').hidden = false;
        setTimeout(() => { $('ql-notice').hidden = true; },5000);
    });
    renderProducts();
})();
