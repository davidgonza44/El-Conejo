/* Reference-only, ephemeral sale controls. No network, persistence or business logic. */
(() => {
    'use strict';
    if (document.documentElement.dataset.referenceMode !== '1' || !document.querySelector('.sa-new-page')) return;
    const fields = [...document.querySelectorAll('.sa-fields .sa-field-box')];
    if (fields.length !== 4) return;
    // Lucide icon geometry; kept local to avoid runtime dependencies.
    const paths = {
        check: '<path d="m20 6-11 11-5-5"/>', search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
        bank: '<path d="m3 10 9-7 9 7M3 10h18M4 21h16M6 10v11M10 10v11M14 10v11M18 10v11"/>',
        cash: '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2"/><path d="M6 12h.01M18 12h.01"/>',
        coin: '<circle cx="12" cy="12" r="10"/><path d="M12 6v12M15 8H10.5a2.5 2.5 0 0 0 0 5h3a2.5 2.5 0 0 1 0 5H9"/>',
        card: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M2 10h20M6 16h4"/>',
        clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
        settings: '<path d="m9.7 4.3.6-2.3h3.4l.6 2.3 2 .9 2.1-.7 1.7 2.9-1.6 1.7v2.3l1.6 1.7-1.7 2.9-2.1-.7-2 .9-.6 2.3h-3.4l-.6-2.3-2-.9-2.1.7-1.7-2.9 1.6-1.7V9.1L3.9 7.4l1.7-2.9 2.1.7Z"/><circle cx="12" cy="10.8" r="3"/>',
        file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h7M14 2l6 6M14 2v6h6v5M16 19h6M19 16v6"/>',
        detail: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 12h8M8 17h5"/>',
        send: '<path d="m22 2-7 20-4-9-9-4Z M22 2 11 13"/>',
        close: '<path d="m6 6 12 12M6 18 18 6"/>',
        left: '<path d="m15 18-6-6 6-6"/>', right: '<path d="m9 18 6-6-6-6"/>',
    };
    const icon = (name, tone = '') => `<svg class="sc-icon ${tone}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
    const sellers = ['Administrador', 'Carlos Mendoza', 'María Rodríguez', 'Jorge Ramírez', 'Ana Torres', 'Pedro Gómez'];
    const options = {
        seller: sellers.map((name, i) => [name, i === 0 ? 'bank' : null, '']),
        method: [['Transferencia','bank',''], ['Efectivo','cash','green'], ['Tarjeta de crédito','card','purple'], ['Tarjeta de débito','card','blue'], ['Cheque','card','orange'], ['Por cobrar','clock','red']],
        condition: [['Contado','coin','green'], ['15 días','clock','blue'], ['30 días','clock','purple'], ['60 días','clock','orange'], ['90 días','clock','red'], ['Personalizada','settings','']],
    };
    const panel = document.createElement('div');
    panel.id = 'sc-popover'; panel.className = 'sc-panel'; panel.setAttribute('popover', 'auto');
    document.body.append(panel);
    const toast = document.createElement('div');
    toast.className = 'sc-toast'; toast.setAttribute('role', 'status'); toast.hidden = true;
    document.body.append(toast);
    const triggers = new Map();
    let active = null;
    let selected = new Date(2024, 4, 31);
    let draft = new Date(selected);
    let month = new Date(2024, 4, 1);
    let quick = 'Hoy';
    let timer;
    const dateLabel = date => date.toLocaleDateString('es-VE', {day:'2-digit', month:'2-digit', year:'numeric'});
    const longDate = date => date.toLocaleDateString('es', {day:'numeric', month:'short', year:'numeric'});
    const sameDay = (a, b) => a.toDateString() === b.toDateString();
    const isOpen = () => panel.matches(':popover-open');
    function close(restore = true) {
        if (isOpen()) panel.hidePopover();
        triggers.get(active)?.setAttribute('aria-expanded', 'false');
        if (restore) triggers.get(active)?.focus();
    }
    function position() {
        if (!isOpen()) return;
        const anchor = triggers.get(active).getBoundingClientRect();
        const width = active === 'date' ? 688 : active === 'save' ? 290 : anchor.width;
        panel.style.width = `${Math.min(width, innerWidth - 24)}px`;
        const left = active === 'save' ? anchor.right - panel.offsetWidth : anchor.left;
        panel.style.left = `${Math.max(12, Math.min(left, innerWidth - panel.offsetWidth - 12))}px`;
        panel.style.top = `${Math.max(12, Math.min(anchor.bottom + 3, innerHeight - panel.offsetHeight - 12))}px`;
    }
    function renderOptions(query = '') {
        const normalize = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es');
        const current = triggers.get(active).firstChild.textContent.trim();
        const filtered = options[active].filter(([label]) => normalize(label).includes(normalize(query.trim())));
        panel.querySelector('.sc-options').innerHTML = filtered.map(([label, symbol, tone]) =>
            `<button type="button" class="sc-option" role="menuitemradio" aria-checked="${label === current}" data-value="${label}">${symbol ? icon(symbol, tone) : '<span class="sc-icon"></span>'}<span>${label}</span>${label === current ? icon('check', 'sc-check blue') : ''}</button>`
        ).join('') || '<p class="sc-empty">No se encontraron vendedores.</p>';
    }
    function renderDropdown() {
        panel.innerHTML = `${active === 'seller' ? `<label class="sc-search">${icon('search')}<input type="search" placeholder="Buscar vendedor..." aria-label="Buscar vendedor..."></label>` : ''}<div class="sc-options" role="menu" aria-label="${triggers.get(active).getAttribute('aria-label')}"></div>`;
        renderOptions();
        panel.querySelector('input')?.addEventListener('input', event => renderOptions(event.target.value));
    }
    function calendar(offset) {
        const first = new Date(month.getFullYear(), month.getMonth() + offset, 1);
        const days = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
        const title = first.toLocaleDateString('es', {month:'long', year:'numeric'}).replace(' de ', ' ');
        const cells = Array.from({length:first.getDay()}, () => '<span></span>');
        for (let day = 1; day <= days; day++) {
            const date = new Date(first.getFullYear(), first.getMonth(), day);
            cells.push(`<button type="button" data-day="${date.getTime()}" aria-label="${longDate(date)}" aria-pressed="${sameDay(date, draft)}">${day}</button>`);
        }
        return `<section class="sc-month"><header><button type="button" data-month="-1" aria-label="Mes anterior${offset ? ' del segundo calendario' : ''}">${icon('left')}</button><strong>${title}</strong><button type="button" data-month="1" aria-label="Mes siguiente${offset ? ' del segundo calendario' : ''}">${icon('right')}</button></header><div class="sc-week">${['Do','Lu','Ma','Mi','Ju','Vi','Sá'].map(day => `<span>${day}</span>`).join('')}</div><div class="sc-days">${cells.join('')}</div></section>`;
    }
    function renderDate() {
        panel.innerHTML = `<div class="sc-date-body"><nav aria-label="Accesos rápidos de fecha">${['Hoy','Últimos 7 días','Últimos 30 días','Este mes','Mes pasado','Rango personalizado'].map(label => `<button type="button" data-quick="${label}" aria-pressed="${quick === label}">${label}</button>`).join('')}</nav><div class="sc-calendars">${calendar(0)}${calendar(1)}</div></div><footer><span aria-live="polite">Fecha seleccionada: ${longDate(draft)}</span><div><button type="button" data-cancel>Cancelar</button><button type="button" data-apply>Aplicar</button></div></footer>`;
    }
    function renderSave() {
        panel.innerHTML = `<div role="menu" aria-label="Opciones de guardado">${[
            ['new','file','Guardar y nueva','Guarda la venta y crea otra nueva'],
            ['detail','detail','Guardar y ver detalle','Guarda y abre el detalle de la venta'],
            ['send','send','Guardar y enviar','Guarda y envía al cliente'],
        ].map(([action, symbol, label, subtitle]) => `<button type="button" role="menuitem" class="sc-save-option" data-save="${action}">${icon(symbol)}<span><strong>${label}</strong><small>${subtitle}</small></span></button>`).join('')}<hr><button type="button" role="menuitem" class="sc-save-option" data-cancel>${icon('close','red')}<strong>Cancelar</strong></button></div>`;
    }
    function open(kind) {
        const toggling = active === kind && isOpen();
        close(false);
        if (toggling) return;
        active = kind;
        panel.className = `sc-panel sc-${kind}`;
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-label', triggers.get(kind).getAttribute('aria-label'));
        if (kind === 'date') {
            draft = new Date(selected); month = new Date(selected.getFullYear(), selected.getMonth(), 1);
            quick = sameDay(selected, new Date(2024,4,31)) ? 'Hoy' : 'Rango personalizado';
            renderDate();
        } else if (kind === 'save') renderSave();
        else renderDropdown();
        panel.showPopover(); position();
        triggers.get(kind).setAttribute('aria-expanded', 'true');
        (panel.querySelector('input, [aria-checked="true"], [data-day][aria-pressed="true"]') || panel.querySelector('button')).focus({preventScroll:true});
    }
    function bind(trigger, kind) {
        triggers.set(kind, trigger);
        trigger.setAttribute('role', 'button'); trigger.setAttribute('tabindex', '0');
        trigger.removeAttribute('aria-readonly');
        trigger.setAttribute('aria-haspopup', 'dialog'); trigger.setAttribute('aria-expanded', 'false');
        trigger.setAttribute('aria-controls', panel.id); trigger.classList.add('sc-trigger');
        trigger.addEventListener('click', () => open(kind));
        trigger.addEventListener('keydown', event => {
            if (['Enter',' ','ArrowDown'].includes(event.key)) { event.preventDefault(); open(kind); }
        });
    }
    ['date','seller','method','condition'].forEach((kind, i) => bind(fields[i], kind));
    bind(document.querySelector('.sa-actionbar-actions .sa-icon-btn'), 'save');
    function simulate(action) {
        close(false);
        const messages = {
            primary: 'Guardado simulado. No se creó ninguna venta.',
            new: 'Guardar y nueva: simulación completada. Puedes continuar en esta vista de referencia.',
            detail: 'Guardar y ver detalle: simulación completada. No se creó ningún detalle real.',
            send: 'Guardar y enviar: simulación completada. No se envió ningún mensaje.',
        };
        toast.textContent = messages[action]; toast.hidden = false;
        clearTimeout(timer); timer = setTimeout(() => { toast.hidden = true; }, 6000);
    }
    document.querySelector('.sa-actionbar-actions .is-primary:not(.sa-icon-btn)').addEventListener('click', () => simulate('primary'));
    function chooseQuick(label) {
        quick = label;
        const dates = {'Hoy':new Date(2024,4,31), 'Últimos 7 días':new Date(2024,4,25), 'Últimos 30 días':new Date(2024,4,2), 'Este mes':new Date(2024,4,1), 'Mes pasado':new Date(2024,3,1)};
        // The sale field accepts one date: period shortcuts choose their start day.
        draft = dates[label] || draft;
        month = new Date(draft.getFullYear(), draft.getMonth(), 1);
        renderDate();
        if (label === 'Rango personalizado') panel.querySelector('[data-day][aria-pressed="true"]').focus();
        else [...panel.querySelectorAll('[data-quick]')].find(button => button.dataset.quick === label).focus();
    }
    function calendarAction(button) {
        if (button.dataset.quick) { chooseQuick(button.dataset.quick); return; }
        if (button.dataset.day) {
            draft = new Date(Number(button.dataset.day)); quick = 'Rango personalizado';
            renderDate(); panel.querySelector(`[data-day="${draft.getTime()}"]`).focus();
        } else if (button.dataset.month) {
            const name = button.getAttribute('aria-label');
            month.setMonth(month.getMonth() + Number(button.dataset.month)); renderDate();
            panel.querySelector(`[aria-label="${name}"]`).focus();
        }
    }
    panel.addEventListener('click', event => {
        const button = event.target.closest('button');
        if (!button) return;
        if (button.hasAttribute('data-cancel')) close();
        else if (button.hasAttribute('data-apply')) {
            selected = new Date(draft); fields[0].firstChild.textContent = dateLabel(selected); close();
        } else if (button.dataset.value) {
            triggers.get(active).firstChild.textContent = button.dataset.value; close();
        } else if (button.dataset.save) { simulate(button.dataset.save); triggers.get('save').focus(); }
        else calendarAction(button);
    });
    panel.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); close(); return; }
        if (!['ArrowDown','ArrowUp','Home','End'].includes(event.key) || event.target.matches('input')) return;
        const buttons = [...panel.querySelectorAll('button')];
        let index = buttons.indexOf(document.activeElement);
        if (event.key === 'Home') index = 0;
        else if (event.key === 'End') index = buttons.length - 1;
        else index = (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
        event.preventDefault(); buttons[index].focus();
    });
    panel.addEventListener('toggle', () => { if (!isOpen()) triggers.get(active)?.setAttribute('aria-expanded', 'false'); });
    document.addEventListener('click', event => {
        if (!event.composedPath().includes(panel) && !event.target.closest('.sc-trigger')) close(false);
    });
    document.addEventListener('focusin', event => {
        if (isOpen() && !panel.contains(event.target) && !event.target.closest('.sc-trigger')) close(false);
    });
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
})();
