/* Categories reference interactions. No network, storage, downloads or fixture mutations. */
(() => {
    'use strict';
    const dialog = document.getElementById('cl-dialog');
    if (!dialog || document.documentElement.dataset.referenceMode !== '1' ||
        new URLSearchParams(location.search).get('ref') !== '1') return;
    const content = document.getElementById('cl-content');
    const menu = document.getElementById('cl-menu');
    const submenu = document.getElementById('cl-submenu');
    const feedback = document.getElementById('cl-feedback');
    const rows = JSON.parse(document.getElementById('cl-fixture').textContent);
    const colors = {blue:'#1477f5',green:'#20aa58',orange:'#ff941c',purple:'#8658d4',teal:'#13b9b1',gray:'#87929f'};
    let index = 0, opener = null, menuOpener = null, fromDetail = false, timer;
    let previousOverflow = '', previousPadding = '';
    const $ = selector => content.querySelector(selector);
    const clone = id => document.getElementById(id).content.cloneNode(true);

    function closeMenu(restore = false) {
        menu.hidden = true;
        submenu.hidden = true;
        menu.querySelector('[data-cl-action=status]').setAttribute('aria-expanded', 'false');
        if (menuOpener) menuOpener.setAttribute('aria-expanded', 'false');
        if (restore) menuOpener?.focus();
    }
    function notify(message) {
        clearTimeout(timer);
        feedback.textContent = `Demostración: ${message}. No se guardaron cambios ni archivos.`;
        feedback.hidden = false;
        timer = setTimeout(() => { feedback.hidden = true; }, 6000);
    }
    function selectColor(color) {
        content.querySelectorAll('[data-cl-color]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.clColor === color));
        });
        $('[data-cl-preview-avatar]').style.setProperty('--cl-color', color);
    }
    function preview() {
        $('[data-cl-preview-name]').textContent = $('#cl-name').value.trim() || 'Nombre de la categoría';
        $('[data-cl-preview-description]').textContent = $('#cl-description').value.trim() || 'Descripción de la categoría aparecerá aquí en dos líneas máximo.';
        $('#cl-counter').textContent = `${$('#cl-description').value.length}/${$('#cl-description').maxLength}`;
        const inactive = $('#cl-state').value === 'Inactiva';
        $('[data-cl-status]').textContent = $('#cl-state').value;
        $('[data-cl-status]').classList.toggle('is-inactive', inactive);
        $('.cl-select').classList.toggle('is-inactive', inactive);
    }
    function fillForm(edit) {
        content.replaceChildren(clone('cl-form-template'));
        content.querySelectorAll('[data-cl-new-only]').forEach(node => { node.hidden = edit; });
        content.querySelectorAll('[data-cl-edit-only]').forEach(node => { node.hidden = !edit; });
        if (edit) {
            const row = rows[index];
            $('#cl-name').value = row.name;
            $('#cl-description').value = row.description;
            $('#cl-description').maxLength = 500;
            $('#cl-state').value = row.status[0];
            $('[data-cl-selected-name]').textContent = row.name;
            $('[data-cl-avatar]').replaceChildren(clone(`cl-avatar-${index}`));
            $('[data-cl-avatar]').style.setProperty('--cl-color', colors[row.tone]);
            $('[data-cl-edit-metrics]').replaceChildren(clone(`cl-metrics-${index}`));
            $('[data-cl-edit-metrics]').hidden = false;
            $('[data-cl-action=save]').lastChild.textContent = 'Guardar cambios';
            selectColor(colors[row.tone]);
        }
        preview();
    }
    function open(state, trigger = null, returnToDetail = false) {
        if (!dialog.open) {
            opener = trigger || menuOpener || document.activeElement;
            previousOverflow = document.body.style.overflow;
            previousPadding = document.body.style.paddingRight;
            const scrollbar = window.innerWidth - document.documentElement.clientWidth;
            if (scrollbar) document.body.style.paddingRight = `${parseFloat(getComputedStyle(document.body).paddingRight) + scrollbar}px`;
            document.body.style.overflow = 'hidden';
        }
        closeMenu();
        feedback.hidden = true;
        fromDetail = returnToDetail;
        dialog.dataset.state = state;
        const titles = {new:'Nueva categoría',detail:'Detalle de la categoría',edit:'Editar categoría',export:'Exportar listado de categorías'};
        const subtitles = {new:'Registra una nueva categoría para organizar el catálogo.',edit:'Modifica la información de la categoría seleccionada.',export:'Selecciona el formato y las opciones para exportar el listado de categorías.'};
        document.getElementById('cl-title').textContent = titles[state];
        document.getElementById('cl-subtitle').textContent = subtitles[state] || '';
        document.getElementById('cl-subtitle').hidden = state === 'detail';
        if (state === 'new' || state === 'edit') fillForm(state === 'edit');
        else content.replaceChildren(clone(state === 'detail' ? `cl-detail-${index}` : 'cl-export-template'));
        if (!dialog.open) dialog.showModal();
        document.getElementById('cl-title').focus();
    }
    function close() {
        if (!dialog.open) return;
        dialog.close();
        document.body.style.overflow = previousOverflow;
        document.body.style.paddingRight = previousPadding;
        opener?.focus();
    }
    function cancel() {
        if (dialog.dataset.state === 'edit' && fromDetail) open('detail');
        else close();
    }
    function simulate(action) {
        const messages = {duplicate:'duplicación simulada',active:'cambio a Activa simulado',inactive:'cambio a Inactiva simulado',delete:'eliminación deshabilitada en modo de referencia',export:'exportación simulada',save:'guardado simulado'};
        if (action === 'save' && !$('form').reportValidity()) return;
        closeMenu(true);
        close();
        notify(messages[action]);
    }
    function act(action) {
        const handlers = {
            detail: () => open('detail'),
            edit: () => open('edit', null, dialog.open && dialog.dataset.state === 'detail'),
            status: () => {
                submenu.hidden = !submenu.hidden;
                menu.querySelector('[data-cl-action=status]').setAttribute('aria-expanded', String(!submenu.hidden));
                if (!submenu.hidden) submenu.querySelector('button').focus();
            },
            all: () => {
                close();
                notify('la consulta de todos los productos no está habilitada');
            },
        };
        if (handlers[action]) handlers[action]();
        else simulate(action);
    }
    function showMenu(button) {
        const same = menuOpener === button && !menu.hidden;
        closeMenu();
        if (same) return;
        close();
        menuOpener = button;
        menu.hidden = false;
        button.setAttribute('aria-expanded', 'true');
        const rect = button.getBoundingClientRect();
        const left = Math.max(8, Math.min(rect.left, innerWidth - menu.offsetWidth - 12));
        const top = rect.bottom + menu.offsetHeight + 8 > innerHeight ? rect.top - menu.offsetHeight - 4 : rect.bottom + 2;
        menu.style.left = `${left}px`;
        menu.style.top = `${Math.max(8, top)}px`;
        menu.querySelector('button').focus();
    }
    document.querySelectorAll('.iv-cat-table tbody tr').forEach((row, rowIndex) => {
        const buttons = row.querySelectorAll('.iv-row-actions button');
        buttons.forEach((button, actionIndex) => {
            button.setAttribute('aria-haspopup', actionIndex === 2 ? 'menu' : 'dialog');
            if (actionIndex === 2) {
                button.setAttribute('aria-controls', 'cl-menu');
                button.setAttribute('aria-expanded', 'false');
            }
            button.addEventListener('click', () => {
                index = rowIndex;
                if (actionIndex === 2) showMenu(button);
                else open(actionIndex === 0 ? 'detail' : 'edit', button);
            });
        });
    });
    document.querySelectorAll('.iv-cat-page .iv-actions button').forEach(button => {
        const state = button.textContent.trim() === 'Nueva categoría' ? 'new' : 'export';
        button.setAttribute('aria-haspopup', 'dialog');
        button.addEventListener('click', () => open(state, button));
    });
    dialog.addEventListener('click', event => {
        if (event.target.closest('[data-cl-close]')) close();
        else if (event.target.closest('[data-cl-cancel]')) cancel();
        else if (event.target.closest('[data-cl-color]')) selectColor(event.target.closest('[data-cl-color]').dataset.clColor);
        else if (event.target.closest('[data-cl-action]')) act(event.target.closest('[data-cl-action]').dataset.clAction);
    });
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    dialog.addEventListener('keydown', event => {
        if (event.key !== 'Tab') return;
        const controls = [...dialog.querySelectorAll('button, input, select, textarea, [tabindex="0"]')]
            .filter(node => !node.disabled && node.getClientRects().length);
        const first = controls[0], last = controls[controls.length - 1];
        const atStart = document.activeElement === first || document.activeElement.id === 'cl-title';
        if (event.shiftKey && atStart) {
            event.preventDefault();
            last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
        }
    });
    dialog.addEventListener('submit', event => { event.preventDefault(); simulate('save'); });
    content.addEventListener('input', () => { if ($('form')) preview(); });
    content.addEventListener('change', () => { if ($('form')) preview(); });
    menu.addEventListener('click', event => {
        const button = event.target.closest('[data-cl-action]');
        if (button) act(button.dataset.clAction);
    });
    document.addEventListener('click', event => {
        if (!menu.hidden && !menu.contains(event.target) && !event.target.closest('.iv-row-actions')) closeMenu();
    });
    document.addEventListener('keydown', event => {
        if (menu.hidden) return;
        if (event.key === 'Escape' || event.key === 'Tab') {
            if (event.key === 'Escape') event.preventDefault();
            closeMenu(true);
            return;
        }
        if (!['ArrowDown','ArrowUp','Home','End','ArrowLeft','ArrowRight'].includes(event.key)) return;
        event.preventDefault();
        if (event.key === 'ArrowLeft') {
            submenu.hidden = true;
            const parent = menu.querySelector('[data-cl-action=status]');
            parent.setAttribute('aria-expanded', 'false');
            parent.focus();
        } else if (event.key === 'ArrowRight') {
            if (document.activeElement.dataset.clAction === 'status') act('status');
        } else {
            const buttons = [...(submenu.hidden ? menu : submenu).children].filter(node => node.matches('button'));
            const current = buttons.indexOf(document.activeElement);
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
            buttons[next].focus();
        }
    });
    window.addEventListener('resize', () => closeMenu());
    document.addEventListener('scroll', () => closeMenu(), true);
})();
