/* Shared approved inline controls; reference filters fixtures, live delegates SQL queries. */
(() => {
    'use strict';
    const source = document.getElementById('mv-fixture');
    const live = window.MovementsLive;
    if (!live && (document.documentElement.dataset.referenceMode !== '1' || !source)) return;
    const fixture = live ? null : JSON.parse(source.textContent);
    const form = document.querySelector('.iv-mov-filters');
    const controls = [...form.querySelectorAll('.px-select-box')];
    const keys = ['kind', 'user', 'date'];
    const labels = ['Tipo', 'Responsable', 'Rango de fechas'];
    const months = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sept', 'oct', 'nov', 'dic'];
    const date = (year, month, day) => new Date(Date.UTC(year, month, day));
    const iso = value => value.toISOString().slice(0, 10);
    const parse = text => {
        const [day, month, year] = text.trim().split(/\s+/);
        return iso(date(Number(year), months.indexOf(month.replace('.', '')), Number(day)));
    };
    const commercialToday = new Intl.DateTimeFormat('en-CA', {timeZone:'America/Caracas',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
    const initialRange = live ? [commercialToday, commercialToday] : fixture.filters.date[1].split(' - ').map(parse);
    const initial = () => ({ kind: '', user: '', date: [...initialRange] });
    let selected = initial();
    let draft = [...initialRange];
    let choosingEnd = false;
    let activeQuick = '';
    let viewedMonth;
    let opened = null;
    const nodes = live ? [] : [...document.querySelectorAll('[data-movement-ref]')];
    const entries = nodes.map(node => {
        const row = fixture.history.rows.find(item => item.ref === node.dataset.movementRef);
        return { node, row, day: parse(row.date) };
    });
    const anchor = new Date(`${live ? commercialToday : entries.map(item => item.day).sort().at(-1)}T00:00:00Z`);
    const popover = document.createElement('div');
    popover.id = 'mv-inline-popover';
    popover.className = 'mv-inline-popover';
    popover.hidden = true;
    document.body.append(popover);
    const empty = document.createElement('tr');
    empty.className = 'mv-inline-empty';
    const cell = document.createElement('td');
    cell.colSpan = 10;
    cell.textContent = 'No hay movimientos con estos filtros';
    empty.append(cell);
    empty.hidden = true;
    if (!live) nodes[0].parentElement.append(empty);
    const summary = document.querySelector('.iv-history .px-pagination-summary');
    summary.setAttribute('aria-live', 'polite');
    const pages = document.querySelector('.iv-history .px-pages');
    if (!live) [...pages.children].forEach(node => {
        if (node.matches('[aria-current="page"]')) return;
        if (node.hasAttribute('aria-label')) node.disabled = true;
        else node.hidden = true;
    });
    function format(value) {
        const [year, month, day] = value.split('-');
        return `${day} ${months[Number(month) - 1]}. ${year}`;
    }
    const rangeText = range => range.map(format).join(' - ');
    function applyFilters() {
        if (live) {
            live.state.page = 1;
            live.state.movement_type = selected.kind;
            live.state.user_id = selected.user;
            if (opened === 2) [live.state.date_from, live.state.date_to] = selected.date;
            syncLive(); live.refresh(); return;
        }
        entries.forEach(({ node, row, day }) => {
            node.hidden = Boolean(selected.kind && row.kind[0] !== selected.kind) ||
                Boolean(selected.user && row.user !== selected.user) ||
                day < selected.date[0] || day > selected.date[1];
        });
        const count = entries.filter(({ node }) => !node.hidden).length;
        empty.hidden = count !== 0;
        summary.textContent = count ? `Mostrando 1 a ${count} de ${count} movimientos` : 'Mostrando 0 de 0 movimientos';
        controls.forEach((control, index) => {
            const value = keys[index] === 'date' ? rangeText(selected.date) : selected[keys[index]] || 'Todos';
            control.querySelector('span').textContent = value;
            control.setAttribute('aria-label', `${labels[index]}: ${value}`);
        });
    }
    function syncLive() {
        selected.kind=live.state.movement_type; selected.user=live.state.user_id;
        selected.date=live.state.date_from && live.state.date_to ? [live.state.date_from,live.state.date_to] : [...initialRange];
        const typeNames={entrada:'Entrada',salida:'Salida',ajuste:'Ajuste'};
        const range=live.state.date_from || live.state.date_to ? `${live.state.date_from || 'Inicio'} - ${live.state.date_to || 'Actualidad'}`:'Todas las fechas';
        const values=[typeNames[selected.kind] || 'Todos',live.users.find(user=>String(user.id)===String(selected.user))?.name || 'Todos',range];
        controls.forEach((control,index)=>{control.querySelector('span').textContent=values[index];control.setAttribute('aria-label',`${labels[index]}: ${values[index]}`);});
    }
    function icon(name) {
        const paths = {
            Todos: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/>',
            Entrada: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
            Salida: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
            Ajuste: '<path d="M21 4H14M10 4H3M21 12H12M8 12H3M21 20H18M14 20H3M14 2v4M8 10v4M18 18v4"/>',
            check: '<path d="m20 6-11 11-5-5"/>',
            previous: '<path d="m15 18-6-6 6-6"/>',
            next: '<path d="m9 18 6-6-6-6"/>',
        };
        return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
    }
    function button(text, className, action) {
        const node = document.createElement('button');
        node.type = 'button';
        node.className = className;
        node.textContent = text;
        node.addEventListener('click', action);
        return node;
    }
    function close(restore = false) {
        if (opened === null) return;
        const control = controls[opened];
        control.setAttribute('aria-expanded', 'false');
        popover.hidden = true;
        opened = null;
        if (restore) control.focus({ preventScroll: true });
    }
    function place() {
        if (opened === null) return;
        const box = controls[opened].getBoundingClientRect();
        popover.style.width = `${opened === 2 ? Math.min(740, innerWidth - 24) : Math.max(box.width, opened === 0 ? 210 : 0)}px`;
        const width = popover.getBoundingClientRect().width;
        popover.style.left = `${Math.max(12, Math.min(box.left, innerWidth - width - 24))}px`;
        popover.style.top = `${box.bottom + 5}px`;
        popover.style.maxHeight = `${Math.max(120, innerHeight - box.bottom - 17)}px`;
    }
    function renderOptions(index) {
        if(live){
            const options=index===0 ? [{id:'entrada',name:'Entrada'},{id:'salida',name:'Salida'},{id:'ajuste',name:'Ajuste'}] : live.users;
            [{id:'',name:'Todos'},...options].forEach(value=>{
                const option=button(value.name,'mv-inline-option',()=>{selected[keys[index]]=String(value.id);applyFilters();close(true);});
                option.dataset.value=String(value.id);
                if(index===1 && value.id)option.title=`Usuario #${value.id}`;
                option.disabled=Boolean(value.disabled);option.setAttribute('role','option');option.setAttribute('aria-selected',String(String(selected[keys[index]])===String(value.id)));popover.append(option);
            }); return;
        }
        const options = [fixture.layers.types, [...new Set(fixture.history.rows.map(row => row.user))]][index];
        ['', ...options].forEach(value => {
            const text = value || 'Todos';
            const option = button(text, 'mv-inline-option', () => {
                selected[keys[index]] = value;
                applyFilters();
                close(true);
            });
            option.setAttribute('role', 'option');
            option.setAttribute('aria-selected', String(selected[keys[index]] === value));
            if (index === 0) {
                const glyph = document.createElement('span');
                glyph.className = `mv-inline-kind mv-inline-kind-${options.indexOf(value) + 1}`;
                glyph.innerHTML = icon(text);
                option.prepend(glyph);
            }
            if (selected[keys[index]] === value) option.insertAdjacentHTML('beforeend', icon('check'));
            popover.append(option);
        });
    }
    function quickRange(index) {
        const y = anchor.getUTCFullYear(), m = anchor.getUTCMonth(), d = anchor.getUTCDate();
        const ranges = [
            [anchor, anchor], [date(y, m, d - 6), anchor], [date(y, m, d - 29), anchor],
            [date(y, m, 1), date(y, m + 1, 0)], [date(y, m - 1, 1), date(y, m, 0)],
            [date(y, m - 2, 1), anchor], [date(y, m - 5, 1), anchor],
            [date(y, 0, 1), date(y, 11, 31)], [date(y - 1, 0, 1), date(y - 1, 11, 31)],
        ];
        return ranges[index]?.map(iso);
    }
    function selectDay(value) {
        activeQuick = 'Rango personalizado';
        if (!choosingEnd) { draft = [value, value]; choosingEnd = true; }
        else { draft = [draft[0], value].sort(); choosingEnd = false; }
        renderDate(value);
    }
    function calendar(month, index) {
        const panel = document.createElement('section');
        panel.className = 'mv-inline-month';
        const heading = document.createElement('header');
        const title = month.toLocaleDateString('es', { month: 'long', year: 'numeric', timeZone: 'UTC' });
        heading.textContent = title;
        const nav = button('', 'mv-inline-month-nav', () => {
            viewedMonth = date(viewedMonth.getUTCFullYear(), viewedMonth.getUTCMonth() + (index ? 1 : -1), 1);
            renderDate();
            popover.querySelectorAll('.mv-inline-month-nav')[index].focus();
        });
        nav.setAttribute('aria-label', index ? 'Mes siguiente' : 'Mes anterior');
        nav.innerHTML = icon(index ? 'next' : 'previous');
        heading.append(nav);
        const grid = document.createElement('div');
        grid.className = 'mv-inline-days';
        ['Lu', 'Ma', 'Mi', 'Ju', 'Vi', 'Sá', 'Do'].forEach(day => {
            const label = document.createElement('span'); label.textContent = day; grid.append(label);
        });
        const offset = (month.getUTCDay() + 6) % 7;
        for (let i = 0; i < 42; i++) {
            const value = date(month.getUTCFullYear(), month.getUTCMonth(), i - offset + 1);
            const key = iso(value);
            const day = button(String(value.getUTCDate()), 'mv-inline-day', () => selectDay(key));
            day.dataset.day = key;
            day.setAttribute('aria-label', value.toLocaleDateString('es', { dateStyle: 'full', timeZone: 'UTC' }));
            day.classList.toggle('is-muted', value.getUTCMonth() !== month.getUTCMonth());
            day.classList.toggle('is-between', key > draft[0] && key < draft[1]);
            day.classList.toggle('is-endpoint', draft.includes(key));
            day.setAttribute('aria-pressed', String(key >= draft[0] && key <= draft[1]));
            grid.append(day);
        }
        panel.append(heading, grid);
        return panel;
    }
    function renderDate(focusDay) {
        popover.replaceChildren();
        const shortcuts = document.createElement('nav');
        shortcuts.className = 'mv-inline-shortcuts';
        shortcuts.setAttribute('aria-label', 'Accesos rápidos');
        ['Hoy', 'Últimos 7 días', 'Últimos 30 días', 'Este mes', 'Mes pasado', 'Últimos 3 meses', 'Últimos 6 meses', 'Este año', 'Año pasado', 'Rango personalizado'].forEach((text, index) => {
            const option = button(text, 'mv-inline-quick', () => {
                activeQuick = text;
                const range = quickRange(index);
                if (range) { draft = range; viewedMonth = new Date(`${draft[0].slice(0, 7)}-01T00:00:00Z`); }
                choosingEnd = false;
                renderDate();
                popover.querySelectorAll('.mv-inline-quick')[index].focus();
            });
            option.setAttribute('aria-pressed', String(text === activeQuick));
            shortcuts.append(option);
        });
        const right = document.createElement('div');
        right.className = 'mv-inline-date-main';
        const calendars = document.createElement('div');
        calendars.className = 'mv-inline-calendars';
        calendars.append(calendar(viewedMonth, 0), calendar(date(viewedMonth.getUTCFullYear(), viewedMonth.getUTCMonth() + 1, 1), 1));
        const footer = document.createElement('footer');
        footer.className = 'mv-inline-date-footer';
        const range = document.createElement('span');
        range.textContent = rangeText(draft);
        range.setAttribute('aria-live', 'polite');
        footer.append(range, button('Cancelar', 'mv-inline-cancel', () => close(true)), button('Aplicar', 'mv-inline-apply', () => {
            selected.date = [...draft]; applyFilters(); close(true);
        }));
        right.append(calendars, footer);
        popover.append(shortcuts, right);
        if (focusDay) popover.querySelector(`[data-day="${focusDay}"]`)?.focus();
        place();
    }
    function open(index) {
        if(live)syncLive();
        if (opened === index) { close(true); return; }
        close();
        opened = index;
        controls[index].setAttribute('aria-expanded', 'true');
        popover.hidden = false;
        popover.classList.toggle('is-date', index === 2);
        popover.setAttribute('role', index === 2 ? 'dialog' : 'listbox');
        popover.setAttribute('aria-label', labels[index]);
        popover.replaceChildren();
        if (index === 2) {
            draft = [...selected.date]; choosingEnd = false; activeQuick = '';
            viewedMonth = new Date(`${draft[0].slice(0, 7)}-01T00:00:00Z`);
            renderDate();
        } else renderOptions(index);
        place();
        (popover.querySelector('[aria-selected="true"]') || popover.querySelector('button')).focus({ preventScroll: true });
    }
    controls.forEach((control, index) => {
        control.setAttribute('role', 'button');
        control.tabIndex = 0;
        control.dataset.mvInline = keys[index];
        control.setAttribute('aria-haspopup', index === 2 ? 'dialog' : 'listbox');
        control.setAttribute('aria-controls', popover.id);
        control.setAttribute('aria-expanded', 'false');
        control.addEventListener('click', () => open(index));
        control.addEventListener('keydown', event => {
            if (['Enter', ' ', 'ArrowDown'].includes(event.key)) { event.preventDefault(); open(index); }
        });
    });
    popover.addEventListener('keydown', event => {
        if (opened === 2) return;
        const options = [...popover.querySelectorAll('button')];
        const index = options.indexOf(document.activeElement);
        const moves = { ArrowDown: (index + 1) % options.length, ArrowUp: (index + options.length - 1) % options.length, Home: 0, End: options.length - 1 };
        if (event.key in moves) { event.preventDefault(); options[moves[event.key]].focus(); }
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && opened !== null) { event.preventDefault(); close(true); }
    });
    document.addEventListener('click', event => {
        // Calendar clicks can replace their own target while bubbling; retain the original event path.
        if (opened !== null && !event.composedPath().includes(popover) && !controls[opened].contains(event.target)) close();
    });
    document.addEventListener('focusin', event => {
        if (opened !== null && !popover.contains(event.target) && !controls[opened].contains(event.target)) close();
    });
    document.addEventListener('mv:close-inline', () => close());
    window.addEventListener('resize', place);
    window.addEventListener('scroll', event => { if (!popover.contains(event.target)) place(); }, true);
    form.addEventListener('reset', () => { close(); selected = initial(); if(live)syncLive();else applyFilters(); });
    if(live){syncLive();document.addEventListener('mv:filters-sync',syncLive);}else applyFilters();
})();
