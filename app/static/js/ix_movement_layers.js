/* Debug reference interactions. No network, storage, or inventory API calls. */
(() => {
    'use strict';
    const source = document.getElementById('mv-fixture');
    if (document.documentElement.dataset.referenceMode !== '1' || !source) return;
    const fixture = JSON.parse(source.textContent);
    const rows = fixture.history.rows;
    const drawer = document.getElementById('mv-drawer');
    const overlay = document.getElementById('mv-overlay');
    const content = document.getElementById('mv-content');
    const menu = document.getElementById('mv-export');
    const shell = document.getElementById('ix-shell');
    // Move layers outside the shell so inert can isolate the modal safely.
    [drawer, overlay, menu, document.getElementById('mv-status')].forEach(node => document.body.append(node));
    const toolbar = [...document.querySelectorAll('.iv-toolbar-actions button')];
    const exportButton = toolbar.find(button => button.textContent.trim() === 'Exportar');
    let returnFocus = null;
    let savedOverflow = '';
    let savedInert = false;
    let toastTimer;
    let filterValues = {};
    const titles = {
        new: ['Nuevo movimiento', 'Registra una nueva entrada, salida o ajuste'],
        edit: ['Editar movimiento', 'Modifica la información del movimiento de inventario'],
        detail: ['Detalle del movimiento', 'Información completa del movimiento de inventario'],
        activities: ['Todas las actividades', 'Registro completo de movimientos de inventario'],
        filters: ['Filtros de movimientos', 'Aplica filtros para refinar los resultados'],
    };
    function announce(message) {
        const status = document.getElementById('mv-status');
        clearTimeout(toastTimer);
        status.textContent = message;
        status.hidden = false;
        toastTimer = setTimeout(() => { status.hidden = true; }, 4000);
    }
    function closeMenu(restore = false) {
        menu.hidden = true;
        exportButton.setAttribute('aria-expanded', 'false');
        if (restore) exportButton.focus();
    }
    function closeDrawer(restore = true) {
        if (drawer.hidden) return;
        drawer.hidden = true;
        overlay.hidden = true;
        shell.inert = savedInert;
        document.body.style.overflow = savedOverflow;
        if (returnFocus) returnFocus.setAttribute('aria-expanded', 'false');
        if (restore && returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    }
    function openDrawer(state, trigger, row) {
        if (state === 'edit' && row?.status[0] !== 'Pendiente') return;
        document.dispatchEvent(new Event('mv:close-inline'));
        closeMenu();
        closeDrawer(false);
        returnFocus = trigger;
        savedOverflow = document.body.style.overflow;
        savedInert = shell.inert;
        document.body.style.overflow = 'hidden';
        shell.inert = true;
        drawer.dataset.state = state;
        overlay.dataset.state = state;
        document.getElementById('mv-title').textContent = titles[state][0];
        document.getElementById('mv-subtitle').textContent = titles[state][1];
        const template = state === 'detail' ? `mv-detail-${row.ref}` :
            (['new', 'edit'].includes(state) ? 'mv-form-template' : `mv-${state}-template`);
        content.replaceChildren(document.getElementById(template).content.cloneNode(true));
        if (state === 'filters') {
            document.getElementById('mv-title').prepend(content.querySelector('[data-filter-icon]').content.cloneNode(true));
            restoreFilters();
        }
        if (['new', 'edit'].includes(state)) setupForm(state, row);
        if (state === 'detail') {
            content.querySelector('[data-mv-edit]')?.addEventListener('click', () => openDrawer('edit', returnFocus, row));
            content.querySelector('[data-mv-correction]')?.addEventListener('click', () => {
                announce('Corrección no disponible todavía. No se creó ningún movimiento ni se modificó el stock.');
            });
        }
        if (state === 'activities') setupActivities();
        drawer.hidden = false;
        overlay.hidden = false;
        content.scrollTop = 0;
        trigger?.setAttribute('aria-expanded', 'true');
        document.getElementById('mv-title').focus({ preventScroll: true });
    }
    function setValue(id, value) { document.getElementById(id).value = value || ''; }
    function setupForm(state, row) {
        content.querySelectorAll('[data-edit-only]').forEach(node => { node.hidden = state !== 'edit'; });
        content.querySelectorAll('[data-new-only]').forEach(node => { node.hidden = state === 'edit'; });
        document.getElementById('mv-date').required = state === 'new';
        document.getElementById('mv-edit-date').required = state === 'edit';
        if (state === 'edit') {
            const detail = fixture.layers.details[row.ref] || {};
            const values = { kind: row.kind[0], state: row.status[0], product: row.ref,
                reference: row.ref, user: row.user, quantity: String(Number(row.qty)),
                before: row.before, stock: row.after, reason: detail.reason, notes: detail.notes,
                'edit-date': `${row.date} ${row.time}` };
            Object.entries(values).forEach(([key, value]) => setValue(`mv-${key}`, value));
            content.querySelector('[data-notes-label]').textContent = 'Observaciones';
            content.querySelector('[data-save-label]').textContent = 'Guardar cambios';
            decorateEditForm();
            const cancel = content.querySelector('button[data-mv-close]');
            cancel.removeAttribute('data-mv-close');
            cancel.addEventListener('click', () => openDrawer('detail', returnFocus, row));
        }
        const notes = document.getElementById('mv-notes');
        const count = () => { content.querySelector('.mv-counter').textContent = `${notes.value.length}/500`; };
        notes.addEventListener('input', count);
        count();
        document.getElementById('mv-product').addEventListener('change', event => {
            const selected = rows.find(item => item.ref === event.target.value);
            setValue('mv-stock', selected?.after);
            setValue('mv-before', selected?.before);
        });
        content.querySelector('form').addEventListener('submit', event => {
            event.preventDefault();
            closeDrawer();
            announce('Simulación completada. No se guardaron datos ni se modificó el stock.');
        });
    }
    function decorateEditForm() {
        const user = document.getElementById('mv-user').closest('label');
        const date = document.getElementById('mv-edit-date').closest('[data-edit-only]');
        const row = document.createElement('div');
        row.className = 'mv-grid mv-edit-responsible-row';
        user.before(row);
        row.append(user, date);
        ['mv-kind', 'mv-state'].forEach(id => {
            const select = document.getElementById(id);
            select.classList.add('mv-edit-badged');
            const badge = document.createElement('span');
            badge.setAttribute('aria-hidden', 'true');
            const update = () => {
                const tones = {Entrada:'green', Salida:'red', Ajuste:'purple', Completado:'green', Pendiente:'orange'};
                badge.className = `mv-badge mv-edit-select-badge mv-${tones[select.value] || 'gray'}`;
                badge.textContent = select.value;
            };
            select.after(badge);
            select.addEventListener('change', update);
            update();
        });
        const product = document.getElementById('mv-product');
        product.closest('label').classList.add('mv-edit-product');
        const card = document.createElement('span');
        card.className = 'mv-edit-product-card';
        card.setAttribute('aria-hidden', 'true');
        const thumb = document.createElement('img');
        thumb.alt = '';
        const copy = document.createElement('span');
        const name = document.createElement('strong');
        const sku = document.createElement('small');
        copy.append(name, sku);
        card.append(thumb, copy);
        product.after(card);
        const updateProduct = () => {
            const selected = rows.find(item => item.ref === product.value);
            card.hidden = !selected;
            if (!selected) return;
            thumb.src = document.getElementById(`mv-detail-${selected.ref}`).content.querySelector('img').src;
            name.textContent = selected.product.name;
            sku.textContent = selected.product.sku;
        };
        product.addEventListener('change', updateProduct);
        updateProduct();
        ['mv-reference', 'mv-before', 'mv-stock', 'mv-reason'].forEach(id => {
            document.getElementById(id).closest('label').classList.add('mv-edit-required');
        });
        const reason = document.getElementById('mv-reason');
        reason.parentElement.append(document.getElementById('mv-kind').parentElement.querySelector('svg').cloneNode(true));
    }
    function restoreFilters() {
        const form = content.querySelector('form');
        Object.entries(filterValues).forEach(([name, value]) => {
            const control = form.elements.namedItem(name);
            if (control.type === 'checkbox') control.checked = value === 'on';
            else control.value = value;
        });
        form.addEventListener('submit', event => {
            event.preventDefault();
            const min = form.elements.namedItem('min').value;
            const max = form.elements.namedItem('max').value;
            if (min && max && Number(min) > Number(max)) {
                announce('El mínimo no puede ser mayor que el máximo.');
                return;
            }
            filterValues = Object.fromEntries(new FormData(form));
            closeDrawer();
            announce('Filtros simulados en modo referencia. El historial permanece sin cambios.');
        });
        form.addEventListener('reset', () => { filterValues = {}; });
    }
    function setupActivities() {
        const allRows = [...rows, ...fixture.layers.extra_activities];
        const list = content.querySelector('.mv-activity-list');
        const items = [...list.children];
        const normalize = text => text.toLocaleLowerCase('es').normalize('NFD').replace(/[\u0300-\u036f]/g, '');
        function filter() {
            const query = normalize(document.getElementById('mv-activity-search').value);
            const kind = document.getElementById('mv-activity-kind').value;
            const state = document.getElementById('mv-activity-state').value;
            items.forEach(item => {
                const row = allRows.find(value => value.ref === item.dataset.activityRef);
                item.hidden = !normalize(item.textContent).includes(query) ||
                    Boolean(kind && row.kind[0] !== kind) ||
                    Boolean(state && row.status[0] !== state);
            });
            const count = items.filter(item => !item.hidden).length;
            content.querySelector('[data-activity-count]').textContent = count ?
                `Mostrando ${count} de ${items.length} actividades` : 'No hay actividades con estos filtros';
        }
        content.querySelector('form').addEventListener('submit', event => event.preventDefault());
        content.querySelector('form').addEventListener('input', filter);
        content.querySelector('[data-activity-order]').addEventListener('change', event => {
            const ordered = [...items].sort((a, b) => {
                const index = ref => allRows.find(item => item.ref === ref);
                const key = item => {
                    const row = index(item.dataset.activityRef);
                    const [hour, minute] = row.time.split(':');
                    return Number(row.date.slice(0, 2)) * 1440 + (Number(hour) % 12 + (row.time.includes('p.m.') ? 12 : 0)) * 60 + Number(minute.slice(0, 2));
                };
                return (key(b) - key(a)) * (event.target.value === 'desc' ? 1 : -1);
            });
            list.append(...ordered);
        });
        filter();
    }
    function bindDrawer(button, state, row) {
        button.setAttribute('aria-controls', 'mv-drawer');
        button.setAttribute('aria-haspopup', 'dialog');
        button.setAttribute('aria-expanded', 'false');
        button.addEventListener('click', () => openDrawer(state, button, row));
    }
    toolbar.forEach(button => {
        const state = { 'Filtros': 'filters', 'Nuevo movimiento': 'new' }[button.textContent.trim()];
        if (state) bindDrawer(button, state);
    });
    document.querySelectorAll('[data-movement-ref]').forEach(tr => {
        const row = rows.find(item => item.ref === tr.dataset.movementRef);
        bindDrawer(tr.querySelector('[aria-label="Ver detalle"]'), 'detail', row);
    });
    bindDrawer(document.querySelector('[data-mv-open="activities"]'), 'activities');
    exportButton.setAttribute('aria-controls', 'mv-export');
    exportButton.setAttribute('aria-haspopup', 'menu');
    exportButton.setAttribute('aria-expanded', 'false');
    exportButton.addEventListener('click', () => {
        document.dispatchEvent(new Event('mv:close-inline'));
        if (!menu.hidden) { closeMenu(true); return; }
        closeDrawer(false);
        const bounds = exportButton.getBoundingClientRect();
        menu.style.left = `${Math.max(8, Math.min(bounds.left, window.innerWidth - 238))}px`;
        menu.style.top = `${bounds.bottom + 1}px`;
        menu.hidden = false;
        exportButton.setAttribute('aria-expanded', 'true');
        menu.querySelector('button').focus();
    });
    document.addEventListener('click', event => {
        if (event.target.closest('[data-mv-close]') || event.target === overlay) closeDrawer();
        if (!menu.contains(event.target) && !exportButton.contains(event.target)) closeMenu();
    });
    menu.addEventListener('click', event => {
        if (!event.target.closest('[data-mv-export]')) return;
        closeMenu(true);
        announce('Vista de referencia: no se generó ningún archivo ni impresión.');
    });
    menu.addEventListener('keydown', event => {
        const options = [...menu.querySelectorAll('button')];
        const index = options.indexOf(document.activeElement);
        const moves = { ArrowDown: (index + 1) % options.length, ArrowUp: (index + options.length - 1) % options.length,
            Home: 0, End: options.length - 1 };
        if (event.key in moves) { event.preventDefault(); options[moves[event.key]].focus(); }
        if (event.key === 'Tab') closeMenu();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            if (!drawer.hidden) { event.preventDefault(); closeDrawer(); }
            else if (!menu.hidden) { event.preventDefault(); closeMenu(true); }
        }
        if (event.key !== 'Tab' || drawer.hidden) return;
        const focusable = [...drawer.querySelectorAll('button, input, select, textarea, summary, [tabindex="0"]')]
            .filter(node => !node.disabled && node.getClientRects().length);
        const first = focusable[0];
        const last = focusable.at(-1);
        if (event.shiftKey && [first, document.getElementById('mv-title')].includes(document.activeElement)) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    window.addEventListener('resize', () => closeMenu());
    window.addEventListener('scroll', () => closeMenu(), true);
})();
