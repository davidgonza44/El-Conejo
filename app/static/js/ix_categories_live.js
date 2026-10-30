/* Live Categorías: misma UI aprobada (capas cl-*), datos y escrituras reales vía /api/categories. No corre en ?ref=1. */
(() => {
    'use strict';
    const dialog = document.getElementById('cl-dialog');
    if (!dialog || new URLSearchParams(location.search).get('ref') === '1') return;

    const canWrite = window.CAN_WRITE === true;
    const canPropose = window.CAN_PROPOSE === true;
    const canApprove = window.CAN_APPROVE === true;
    const canEdit = category => canWrite && canPropose && !category.has_pending_change &&
        (category.approval_status !== 'pending' || category.proposed_by_user_id === window.CURRENT_USER_ID);
    const canDecide = category => canApprove && (category.pending_change
        ? category.pending_change.proposed_by_user_id !== window.CURRENT_USER_ID
        : category.approval_status === 'pending' && category.proposed_by_user_id !== window.CURRENT_USER_ID);
    const approvalLabel = category => ({ pending: 'Pendiente', rejected: 'Rechazada', approved: 'Aprobada' }[category.approval_status] || '—');
    const canViewCost = window.CAN_VIEW_COST === true;
    const columnCount = canViewCost ? 6 : 5;
    const content = document.getElementById('cl-content');
    const menu = document.getElementById('cl-menu');
    const feedback = document.getElementById('cl-feedback');
    const els = {
        search: document.getElementById('ix-cat-search'),
        status: document.getElementById('ix-cat-status'),
        approval: document.getElementById('ix-cat-approval'),
        sort: document.getElementById('ix-cat-sort'),
        clear: document.getElementById('ix-cat-clear'),
        perPage: document.getElementById('ix-cat-per-page'),
        body: document.getElementById('ix-cat-body'),
        wrap: document.getElementById('ix-cat-wrap'),
        summary: document.getElementById('ix-cat-page-summary'),
        pages: document.getElementById('ix-cat-pages'),
        legend: document.getElementById('ix-cat-legend'),
        donut: document.querySelector('#ix-cat-donut canvas')
    };
    /* Paleta aprobada: identificador persistido -> hex del selector del drawer. */
    const HEX = {};
    const COLOR_NAMES = {}, ICON_NAMES = {};
    document.getElementById('cl-form-template').content.querySelectorAll('[data-cl-tone]').forEach(button => {
        HEX[button.dataset.clTone] = button.dataset.clColor;
        COLOR_NAMES[button.dataset.clTone] = button.getAttribute('aria-label');
    });
    const DEFAULT_TONE = 'blue';
    const tone = value => (Object.hasOwn(HEX, value) ? value : DEFAULT_TONE);
    const ICONS = new Map([...document.getElementById('cl-form-template').content.querySelectorAll('[data-cl-icon]')]
        .map(button => {
            ICON_NAMES[button.dataset.clIcon] = button.getAttribute('aria-label');
            return [button.dataset.clIcon, button.querySelector('svg')];
        }));
    const categoryIcon = value => (ICONS.get(value) || ICONS.get('tag')).cloneNode(true);
    const TAG_ICON = '<svg viewBox="0 0 24 24" fill="currentColor" class="ix-svg" aria-hidden="true"><path d="M3 3h8.6a2 2 0 0 1 1.4.6l8.4 8.4a2 2 0 0 1 0 2.8l-6.6 6.6a2 2 0 0 1-2.8 0L3.6 13A2 2 0 0 1 3 11.6z"/><circle cx="8" cy="8" r="1.8" fill="#fff"/></svg>';
    const state = { search: '', status: 'all', approvalStatus: '', sort: 'name-asc', page: 1, perPage: 10 };
    let current = null, opener = null, menuOpener = null, fromDetail = false, timer, searchTimer, saving = false;
    let previousOverflow = '', previousPadding = '';
    const $ = selector => content.querySelector(selector);
    const clone = id => document.getElementById(id).content.cloneNode(true);

    async function api(url, options = {}) {
        const headers = { ...(options.headers || {}) };
        if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
        const response = await fetch(url, { credentials: 'same-origin', ...options, headers });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(data.error || ({ 403: 'No tiene permiso para esta acción.', 404: 'La categoría no existe.' }[response.status] || 'No se pudieron cargar las categorías.'));
            error.status = response.status;
            throw error;
        }
        return data;
    }
    const money = (value, currency) => value == null ? '—' : `${{USD: '$', VES: 'Bs.'}[currency] || 'Moneda no registrada ·'} ${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    function valuationText(row) {
        const amounts = (row.totals_by_currency || []).map(item => money(item.amount, item.currency));
        if (row.unknown_currency_products) amounts.push(`${row.unknown_currency_products} sin moneda, excluidos`);
        return amounts.join(' · ') || 'Sin costos denominados';
    }
    const integer = value => Number(value || 0).toLocaleString('en-US');
    function formatDate(iso) {
        const date = iso ? new Date(iso) : null;
        if (!date || Number.isNaN(date.getTime())) return '—';
        return date.toLocaleString('es-VE', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    }
    function node(tag, text, className) {
        const el = document.createElement(tag);
        if (text !== undefined && text !== null) el.textContent = text;
        if (className) el.className = className;
        return el;
    }
    function notify(message) {
        clearTimeout(timer);
        feedback.textContent = message;
        feedback.hidden = false;
        timer = setTimeout(() => { feedback.hidden = true; }, 6000);
    }
    function formError(message) {
        let error = $('[data-cl-error]');
        if (!error) {
            error = node('p', undefined, 'cl-error');
            error.setAttribute('data-cl-error', '');
            error.setAttribute('role', 'alert');
            $('.cl-body').prepend(error);
        }
        error.textContent = message;
    }
    function statusBadge(category) {
        const approved = category.approval_status === 'approved';
        const label = category.has_pending_change ? 'Cambio pendiente' : approved ? (category.is_active ? 'Activa' : 'Inactiva') : approvalLabel(category);
        const color = category.has_pending_change ? 'orange' : approved ? (category.is_active ? 'green' : 'gray') : category.approval_status === 'pending' ? 'orange' : 'red';
        const badge = node('span', label, `px-badge is-${color} iv-badge-dot`);
        badge.title = approvalLabel(category);
        return badge;
    }
    function iconButton(label, glyph, handler) {
        const button = node('button', undefined, 'iv-icon-btn');
        button.type = 'button';
        button.setAttribute('aria-label', label);
        button.innerHTML = `<i class="ti ti-${glyph}" aria-hidden="true"></i>`;
        button.addEventListener('click', () => handler(button));
        return button;
    }

    /* ---- Listado, KPIs y distribución ---- */
    function renderRows(items, total) {
        els.body.replaceChildren();
        if (!items.length) {
            const cell = node('td', total === 0 ? 'No hay categorías registradas.' : 'No se encontraron categorías con estos filtros.', 'px-cell-muted');
            cell.colSpan = columnCount;
            const row = node('tr');
            row.append(cell);
            els.body.append(row);
            return;
        }
        items.forEach(category => {
            const row = node('tr');
            row.dataset.categoryId = String(category.id);
            const name = node('span', undefined, 'iv-cat-name');
            const icon = node('span', undefined, `iv-cat-icon tone-${tone(category.color)}`);
            icon.setAttribute('aria-hidden', 'true');
            icon.append(categoryIcon(category.icon));
            const label = node('span', category.name, 'iv-cat-label');
            label.title = category.name;
            name.append(icon, label);
            const nameCell = node('td');
            nameCell.append(name);
            const description = node('td', category.description || '—', 'px-cell-muted');
            description.title = category.description || '';
            const products = node('td', `${integer(category.products_count)} productos`, 'is-center px-cell-muted');
            const status = node('td', undefined, 'is-center');
            status.append(statusBadge(category));
            const actions = node('div', undefined, 'iv-row-actions');
            actions.append(iconButton('Ver detalle', 'eye', button => openLayer('detail', category, button)));
            if (canEdit(category)) {
                actions.append(iconButton('Editar', 'pencil', button => openLayer('edit', category, button)));
            }
            if (canEdit(category) || canPropose || canDecide(category)) {
                const more = iconButton('Más acciones', 'dots-vertical', button => showMenu(button, category));
                more.setAttribute('aria-haspopup', 'menu');
                more.setAttribute('aria-controls', 'cl-menu');
                more.setAttribute('aria-expanded', 'false');
                actions.append(more);
            }
            const actionCell = node('td', undefined, 'is-center');
            actionCell.append(actions);
            row.append(nameCell, description, products);
            if (canViewCost && Object.prototype.hasOwnProperty.call(category, 'inventory_value')) {
                row.append(node('td', valuationText(category), 'is-center'));
            }
            row.append(status, actionCell);
            els.body.append(row);
        });
    }
    function pageButton(label, glyph, disabled, target) {
        const button = node('button', undefined, 'px-pg');
        button.type = 'button';
        button.setAttribute('aria-label', label);
        button.innerHTML = `<i class="ti ti-chevron-${glyph}" aria-hidden="true"></i>`;
        button.disabled = disabled;
        button.addEventListener('click', () => { state.page = target; reload(); });
        return button;
    }
    function renderPager(pg, count) {
        const start = count ? (pg.page - 1) * pg.per_page + 1 : 0;
        const end = count ? start + count - 1 : 0;
        els.summary.textContent = `Mostrando ${start} a ${end} de ${pg.total} categorías`;
        els.pages.replaceChildren(pageButton('Página anterior', 'left', !pg.has_prev, pg.page - 1));
        for (let n = 1; n <= pg.pages; n += 1) {
            const button = node('button', String(n), `px-pg${n === pg.page ? ' is-active' : ''}`);
            button.type = 'button';
            if (n === pg.page) button.setAttribute('aria-current', 'page');
            button.addEventListener('click', () => { state.page = n; reload(); });
            els.pages.append(button);
        }
        els.pages.append(pageButton('Página siguiente', 'right', !pg.has_next, pg.page + 1));
    }
    function renderSummary(summary) {
        document.getElementById('ix-cat-kpi-total').textContent = integer(summary.total);
        document.getElementById('ix-cat-kpi-sub').textContent = `Activas: ${summary.active}  ·  Inactivas: ${summary.inactive}`;
        document.getElementById('ix-cat-kpi-products').textContent = integer(summary.products);
        const valueKpi = document.getElementById('ix-cat-kpi-value');
        if (valueKpi && Object.prototype.hasOwnProperty.call(summary, 'inventory_value')) {
            valueKpi.textContent = valuationText(summary);
        }
        /* Presentación: la geometría aprobada muestra 6 leyendas; el resto se agrupa en "Otros". */
        let slices = summary.distribution.map(item => ({ name: item.name, percent: item.percent, count: item.count, tone: tone(item.color) }));
        if (slices.length > 6) {
            const rest = slices.slice(5);
            slices = slices.slice(0, 5).concat({
                name: 'Otros', tone: 'gray',
                count: rest.reduce((sum, item) => sum + item.count, 0),
                percent: Math.round(rest.reduce((sum, item) => sum + item.percent, 0) * 10) / 10
            });
        }
        els.legend.replaceChildren();
        if (!slices.length) els.legend.append(node('li', 'No hay categorías registradas.', 'px-cell-muted'));
        slices.forEach(item => {
            const li = node('li');
            const name = node('span', item.name, 'iv-type-name');
            name.title = item.name;
            li.append(node('span', undefined, `ix-legend-dot iv-dot-${item.tone}`), name, node('span', `${item.percent}%`, 'iv-type-value'));
            els.legend.append(li);
        });
        const chart = typeof Chart !== 'undefined' && els.donut ? Chart.getChart(els.donut) : null;
        if (!chart) return;
        const palette = { blue: '#1a73e8', green: '#3fae62', orange: '#fdb429', purple: '#8e73e4', teal: '#20c2c5', gray: '#a6adb8', red: '#dc2626', pink: '#e93d94', yellow: '#fbc21b', brown: '#92400e', navy: '#1e3a8a' };
        const hasProducts = slices.some(item => item.count > 0);
        chart.data.datasets[0].data = hasProducts ? slices.map(item => item.count) : [1];
        chart.data.datasets[0].backgroundColor = hasProducts ? slices.map(item => palette[item.tone]) : ['#e9eef6'];
        chart.update();
    }
    function listQuery() {
        const params = new URLSearchParams({ status: state.status, sort: state.sort, page: String(state.page), per_page: String(state.perPage) });
        if (state.approvalStatus) params.set('approval_status', state.approvalStatus);
        if (state.search) params.set('search', state.search);
        return params;
    }
    async function reload() {
        els.wrap.setAttribute('aria-busy', 'true');
        try {
            const [list, summary] = await Promise.all([api(`/api/categories?${listQuery()}`), api('/api/categories/summary')]);
            renderRows(list.items, summary.total);
            state.page = list.pagination.page;
            renderPager(list.pagination, list.items.length);
            renderSummary(summary);
        } catch (error) {
            els.body.replaceChildren();
            const cell = node('td', `${error.message} `, 'px-cell-muted');
            cell.colSpan = columnCount;
            const retry = node('button', 'Reintentar', 'px-btn');
            retry.type = 'button';
            retry.addEventListener('click', reload);
            cell.append(retry);
            const row = node('tr');
            row.append(cell);
            els.body.append(row);
            els.summary.textContent = '';
        } finally {
            els.wrap.setAttribute('aria-busy', 'false');
        }
    }

    /* ---- Capas (drawer + menú): misma cáscara que la referencia ---- */
    function closeMenu(restore = false) {
        menu.hidden = true;
        if (menuOpener) menuOpener.setAttribute('aria-expanded', 'false');
        if (restore) menuOpener?.focus();
    }
    function selectColor(hex) {
        content.querySelectorAll('[data-cl-color]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.clColor === hex));
        });
        $('[data-cl-preview-avatar]')?.style.setProperty('--cl-color', hex);
        $('[data-cl-avatar]')?.style.setProperty('--cl-color', hex);
    }
    function selectedTone() {
        return content.querySelector('[data-cl-color][aria-pressed=true]')?.dataset.clTone || DEFAULT_TONE;
    }
    function selectIcon(value) {
        const key = ICONS.has(value) ? value : 'tag';
        content.querySelectorAll('[data-cl-icon]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.clIcon === key));
        });
        content.querySelectorAll('[data-cl-preview-avatar], [data-cl-avatar]').forEach(avatar => {
            avatar.replaceChildren(categoryIcon(key));
        });
    }
    function preview() {
        const nameField = $('#cl-name'), description = $('#cl-description');
        if (!nameField) return;
        if ($('[data-cl-preview-name]')) {
            $('[data-cl-preview-name]').textContent = nameField.value.trim() || 'Nombre de la categoría';
            $('[data-cl-preview-description]').textContent = description.value.trim() || 'Descripción de la categoría aparecerá aquí en dos líneas máximo.';
        }
        $('#cl-counter').textContent = `${description.value.length}/${description.maxLength}`;
        const proposed = !canApprove || dialog.dataset.state === 'new';
        const inactive = proposed || $('#cl-state').value === 'Inactiva';
        content.querySelectorAll('[data-cl-status]').forEach(badge => {
            badge.textContent = proposed ? 'Pendiente' : $('#cl-state').value;
            badge.classList.toggle('is-inactive', inactive);
        });
        $('.cl-select').classList.toggle('is-inactive', inactive);
    }
    function fillMetrics(root, category) {
        const set = (key, text) => root.querySelectorAll(`[data-cl-metric="${key}"]`).forEach(el => { el.textContent = text; });
        set('products', integer(category.products_count));
        if (Object.prototype.hasOwnProperty.call(category, 'inventory_value')) set('value', valuationText(category));
        if (Object.prototype.hasOwnProperty.call(category, 'inventory_share')) set('share',
            (category.totals_by_currency || []).map(item => `${item.currency}: ${item.share}%`).join(' · ') || '—');
    }
    function fillForm(edit) {
        content.replaceChildren(clone('cl-form-template'));
        content.querySelectorAll('[data-cl-new-only]').forEach(el => { el.hidden = edit; });
        content.querySelectorAll('[data-cl-edit-only]').forEach(el => { el.hidden = !edit; });
        $('#cl-name').maxLength = 100;
        $('#cl-description').maxLength = 255;
        $('#cl-state').closest('label').hidden = !edit || current.approval_status !== 'approved';
        $('#cl-state').disabled = !edit || current.approval_status !== 'approved';
        if (edit) {
            $('#cl-name').value = current.name;
            $('#cl-description').value = current.description || '';
            $('#cl-state').value = current.is_active ? 'Activa' : 'Inactiva';
            $('[data-cl-selected-name]').textContent = current.name;
            const metrics = clone('cl-metrics-live');
            fillMetrics(metrics, current);
            $('[data-cl-edit-metrics]').replaceChildren(metrics);
            $('[data-cl-edit-metrics]').hidden = false;
            $('[data-cl-action=save]').lastChild.textContent = 'Enviar propuesta de modificación';
            selectColor(HEX[tone(current.color)]);
            selectIcon(current.icon);
        } else {
            $('#cl-state').value = 'Inactiva';
            $('[data-cl-action=save]').lastChild.textContent = 'Proponer categoría';
            selectColor(HEX[DEFAULT_TONE]);
            selectIcon('tag');
        }
        preview();
    }
    async function fillDetail() {
        content.replaceChildren(clone('cl-detail-template'));
        const category = current;
        content.querySelectorAll('[data-cl-detail="name"]').forEach(el => { el.textContent = category.name; });
        content.querySelectorAll('[data-cl-detail="description"]').forEach(el => { el.textContent = category.description || '—'; });
        $('[data-cl-detail="created"]').textContent = formatDate(category.created_at);
        $('[data-cl-detail="approval"]').textContent = approvalLabel(category);
        $('[data-cl-detail="decided"]').textContent = formatDate(category.approved_at);
        $('[data-cl-detail="reason"]').textContent = category.rejection_reason || '—';
        $('[data-cl-detail="color"]').textContent = COLOR_NAMES[category.color] || '—';
        $('[data-cl-detail="icon"]').textContent = ICON_NAMES[category.icon] || '—';
        $('[data-cl-detail-avatar]').style.setProperty('--cl-color', HEX[tone(category.color)]);
        $('[data-cl-detail-avatar]').replaceChildren(categoryIcon(category.icon));
        content.querySelectorAll('[data-cl-status]').forEach(badge => {
            badge.textContent = category.approval_status === 'approved' ? (category.is_active ? 'Activa' : 'Inactiva') : approvalLabel(category);
            badge.classList.toggle('is-inactive', !category.is_active);
        });
        fillMetrics(content, category);
        const edit = $('[data-cl-action=edit]');
        if (edit) edit.hidden = !canEdit(category);
        fillProposal(category);
        const review = $('[data-cl-review]');
        if (review) review.hidden = !canDecide(category);
        try {
            const recent = await api(`/api/products?category_id=${category.id}&sort=created-desc&page=1&per_page=5`);
            let ranking = null;
            if (canViewCost && category.value_rank_available) ranking = await api('/api/categories?sort=value-desc');
            if (dialog.dataset.state !== 'detail' || current !== category) return;
            if (ranking) {
                const position = ranking.items.findIndex(item => item.id === category.id) + 1;
                const rank = $('[data-cl-metric="rank"]');
                if (rank) rank.textContent = position ? `#${position}` : '—';
                const total = $('[data-cl-metric="total"]');
                if (total) total.textContent = String(ranking.count);
            }
            const list = $('[data-cl-recent]');
            $('[data-cl-recent-empty]').hidden = recent.items.length > 0;
            recent.items.forEach(product => {
                const li = node('li');
                const thumb = node('span', undefined, 'cl-thumb');
                if (product.image_url) {
                    const img = node('img');
                    img.src = product.image_url;
                    img.alt = '';
                    thumb.append(img);
                } else thumb.innerHTML = TAG_ICON;
                const text = node('span', product.name);
                text.append(node('small', product.code));
                li.append(thumb, text, node('span', money(product.sale_price, product.sale_price_currency)));
                list.append(li);
            });
        } catch (error) {
            notify(error.message);
        }
    }
    function fillProposal(category) {
        const proposal = category.pending_change || category.last_change;
        const section = $('[data-cl-proposal]');
        if (!section) return;
        section.hidden = !proposal;
        if (!proposal) return;
        $('[data-cl-proposal-status]').textContent = ({ pending: 'Pendiente', approved: 'Aprobada', rejected: 'Rechazada' })[proposal.status];
        $('[data-cl-proposed-by]').textContent = `${proposal.proposed_by_name || '—'} · ${formatDate(proposal.created_at)}`;
        $('[data-cl-reviewed-by]').textContent = proposal.reviewed_by_user_id
            ? `${proposal.reviewed_by_name || '—'} · ${formatDate(proposal.reviewed_at)}` : 'Sin decisión';
        $('[data-cl-proposal-reason]').textContent = proposal.rejection_reason || '—';
        const names = { name: 'Nombre', description: 'Descripción', color: 'Color', icon: 'Icono', is_active: 'Estado' };
        const display = (field, value) => ({ color: COLOR_NAMES, icon: ICON_NAMES })[field]?.[value]
            || (field === 'is_active' ? (value ? 'Activa' : 'Inactiva') : (value || '—'));
        const rows = $('[data-cl-comparison]');
        Object.entries(proposal.changes).forEach(([field, value]) => {
            const row = node('tr');
            row.append(node('th', names[field]), node('td', display(field, category[field])), node('td', display(field, value)));
            row.firstChild.scope = 'row';
            rows.append(row);
        });
    }
    function open(view, trigger = null, returnToDetail = false) {
        if (view === 'edit' && !canEdit(current)) return;
        if (view === 'new' && !canPropose) return;
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
        dialog.dataset.state = view;
        const titles = { new: 'Nueva categoría', detail: 'Detalle de la categoría', edit: 'Proponer modificación de categoría', export: 'Exportar listado de categorías' };
        const subtitles = { new: 'Propone una categoría para su revisión por el administrador.', edit: 'Los valores operativos se conservan hasta que el Administrador apruebe la propuesta.', export: 'Selecciona el formato y las opciones para exportar el listado de categorías.' };
        document.getElementById('cl-title').textContent = titles[view];
        document.getElementById('cl-subtitle').textContent = subtitles[view] || '';
        document.getElementById('cl-subtitle').hidden = view === 'detail';
        if (view === 'detail') fillDetail();
        else if (view === 'export') content.replaceChildren(clone('cl-export-template'));
        else fillForm(view === 'edit');
        if (!dialog.open) dialog.showModal();
        document.getElementById('cl-title').focus();
    }
    async function openLayer(view, category, trigger) {
        try {
            current = await api(`/api/categories/${category.id}`);
            open(view, trigger);
        } catch (error) { notify(error.message); }
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

    /* ---- Escrituras reales ---- */
    async function save() {
        if (saving || !(dialog.dataset.state === 'edit' ? canEdit(current) : canPropose) || !$('form').reportValidity()) return;
        const edit = dialog.dataset.state === 'edit';
        const payload = {
            name: $('#cl-name').value.trim(),
            description: $('#cl-description').value.trim(),
            color: selectedTone(),
            icon: $('[data-cl-icon][aria-pressed=true]')?.dataset.clIcon || 'tag',
            ...(edit && current.approval_status === 'approved' ? { is_active: $('#cl-state').value === 'Activa' } : {})
        };
        saving = true;
        const button = $('[data-cl-action=save]');
        button.disabled = true;
        try {
            const saved = edit
                ? await api(`/api/categories/${current.id}`, { method: 'PUT', body: JSON.stringify(payload) })
                : await api('/api/categories', { method: 'POST', body: JSON.stringify(payload) });
            close();
            notify(edit && saved.pending_change ? 'Modificación propuesta; la categoría operativa conserva sus valores.' : `Categoría '${saved.name}' propuesta; pendiente de aprobación.`);
            reload();
        } catch (error) {
            formError(error.message);
        } finally {
            saving = false;
            button.disabled = false;
        }
    }
    async function mutate(action) {
        if (!current || saving) return;
        const category = current;
        closeMenu(true);
        saving = true;
        try {
            if (action === 'approve' || action === 'reject') {
                if (!canDecide(category)) return;
                const input = $('[data-cl-rejection-reason]');
                const reason = action === 'reject' ? (input ? input.value.trim() : window.prompt('Motivo del rechazo (hasta 1000 caracteres):')) : null;
                if (action === 'reject' && reason === null) return;
                if (action === 'reject' && !reason) throw new Error('Indique un motivo de rechazo.');
                current = await api(`/api/categories/${category.id}/${action}`, { method: 'POST',
                    body: JSON.stringify({ reason, ...(category.pending_change ? { proposal_id: category.pending_change.id } : {}) }) });
                if (dialog.open && dialog.dataset.state === 'detail') await fillDetail();
                notify(category.pending_change
                    ? (action === 'approve' ? 'Modificación aprobada y aplicada.' : 'Modificación rechazada; categoría operativa conservada.')
                    : (action === 'approve' ? 'Categoría aprobada y activa.' : 'Categoría rechazada e inactiva.'));
            } else if (action === 'duplicate' && canPropose) {
                const copy = await api(`/api/categories/${category.id}/duplicate`, { method: 'POST' });
                notify(`Copia '${copy.name}' propuesta; pendiente de aprobación.`);
            }
            reload();
        } catch (error) {
            const message = $('[data-cl-review-feedback]');
            if (message) { message.textContent = error.message; message.hidden = false; }
            notify(error.message);
        } finally {
            saving = false;
        }
    }
    /* ---- Exportación real: mismos filtros del listado, todas las páginas ---- */
    async function exportCategories() {
        if (saving) return;
        const params = listQuery();
        params.delete('page');
        params.delete('per_page');
        params.set('format', $('[name="cl-format"]:checked').value);
        content.querySelectorAll('.cl-options input[type=checkbox]').forEach(input => params.set(input.name, input.checked ? '1' : '0'));
        saving = true;
        const button = $('[data-cl-action=export]');
        button.disabled = true;
        try {
            const response = await fetch(`/api/categories/export?${params}`, { credentials: 'same-origin' });
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.error || 'No se pudo exportar el listado de categorías.');
            }
            const url = URL.createObjectURL(await response.blob());
            const link = node('a');
            link.href = url;
            link.download = response.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/)?.[1] || `categorias.${params.get('format')}`;
            document.body.append(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            close();
            notify('Archivo generado. Descarga iniciada.');
        } catch (error) {
            formError(error.message);
        } finally {
            saving = false;
            button.disabled = false;
        }
    }
    function act(action) {
        const handlers = {
            detail: () => open('detail'),
            edit: () => open('edit', null, dialog.open && dialog.dataset.state === 'detail'),
            save,
            export: exportCategories
        };
        if (handlers[action]) handlers[action]();
        else mutate(action);
    }
    function showMenu(button, category) {
        const same = menuOpener === button && !menu.hidden;
        closeMenu();
        if (same) return;
        close();
        current = category;
        menuOpener = button;
        const visible = { detail: true, edit: canEdit(category), duplicate: canPropose,
            approve: canDecide(category), reject: canDecide(category) };
        menu.querySelectorAll(':scope > [data-cl-action]').forEach(item => { item.hidden = !visible[item.dataset.clAction]; });
        menu.hidden = false;
        button.setAttribute('aria-expanded', 'true');
        const rect = button.getBoundingClientRect();
        const left = Math.max(8, Math.min(rect.left, innerWidth - menu.offsetWidth - 12));
        const top = rect.bottom + menu.offsetHeight + 8 > innerHeight ? rect.top - menu.offsetHeight - 4 : rect.bottom + 2;
        menu.style.left = `${left}px`;
        menu.style.top = `${Math.max(8, top)}px`;
        menu.querySelector('button').focus();
    }

    /* ---- Eventos ---- */
    dialog.addEventListener('click', event => {
        if (event.target.closest('[data-cl-close]')) close();
        else if (event.target.closest('[data-cl-cancel]')) cancel();
        else if (event.target.closest('[data-cl-color]')) selectColor(event.target.closest('[data-cl-color]').dataset.clColor);
        else if (event.target.closest('[data-cl-icon]')) selectIcon(event.target.closest('[data-cl-icon]').dataset.clIcon);
        else if (event.target.closest('[data-cl-action]')) act(event.target.closest('[data-cl-action]').dataset.clAction);
    });
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    dialog.addEventListener('submit', event => { event.preventDefault(); save(); });
    dialog.addEventListener('keydown', event => {
        if (event.key !== 'Tab') return;
        const controls = [...dialog.querySelectorAll('button, input, select, textarea, [tabindex="0"]')]
            .filter(el => !el.disabled && el.getClientRects().length);
        const first = controls[0], last = controls[controls.length - 1];
        const atStart = document.activeElement === first || document.activeElement.id === 'cl-title';
        if (event.shiftKey && atStart) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    });
    content.addEventListener('input', preview);
    content.addEventListener('change', preview);
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
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const buttons = [...menu.children].filter(el => el.matches('button') && !el.hidden);
        const index = buttons.indexOf(document.activeElement);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
        buttons[next].focus();
    });
    window.addEventListener('resize', () => closeMenu());
    document.addEventListener('scroll', () => closeMenu(), true);

    document.querySelectorAll('.iv-cat-page .iv-actions button').forEach(button => {
        const label = button.textContent.trim();
        if (label === 'Nueva categoría') {
            button.hidden = !canPropose;
            button.setAttribute('aria-haspopup', 'dialog');
            button.addEventListener('click', () => open('new', button));
        } else if (label === 'Exportar') {
            button.setAttribute('aria-haspopup', 'dialog');
            button.addEventListener('click', () => open('export', button));
        }
    });
    els.search.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => { state.search = els.search.value.trim(); state.page = 1; reload(); }, 300);
    });
    els.status.addEventListener('change', () => { state.status = els.status.value; state.page = 1; reload(); });
    els.approval.addEventListener('change', () => { state.approvalStatus = els.approval.value; state.page = 1; reload(); });
    els.sort.addEventListener('change', () => { state.sort = els.sort.value; state.page = 1; reload(); });
    els.perPage.addEventListener('change', () => { state.perPage = Number(els.perPage.value); state.page = 1; reload(); });
    els.clear.addEventListener('click', () => {
        Object.assign(state, { search: '', status: 'all', approvalStatus: '', sort: 'name-asc', page: 1 });
        els.search.value = '';
        els.status.value = 'all';
        els.approval.value = '';
        els.sort.value = 'name-asc';
        reload();
    });
    const initial = new URLSearchParams(location.search);
    if (['pending', 'approved', 'rejected'].includes(initial.get('approval_status'))) {
        state.approvalStatus = initial.get('approval_status');
        els.approval.value = state.approvalStatus;
    }
    if (['pending', 'approved', 'rejected'].includes(initial.get('status')) && !state.approvalStatus) {
        state.approvalStatus = initial.get('status');
        els.approval.value = state.approvalStatus;
    } else if (['all', 'active', 'inactive'].includes(initial.get('status'))) {
        state.status = initial.get('status');
        els.status.value = state.status;
    }
    reload();
})();
