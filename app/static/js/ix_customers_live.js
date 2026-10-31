/* Live Ventas → Clientes: tabla real desde /api/customers con búsqueda, paginación y alta.
   No corre en ?ref=1. */
(function () {
    'use strict';
    const body = document.getElementById('cu-body');
    if (!body || document.documentElement.dataset.referenceMode === '1') return;

    const $ = (id) => document.getElementById(id);
    const els = {
        search: $('cu-search'), newButton: $('cu-new'), feedback: $('cu-feedback'), empty: $('cu-empty'),
        summary: $('cu-summary'), pages: $('cu-pages'), drawer: $('cu-drawer'), form: $('cu-form'),
        drawerFeedback: $('cu-drawer-feedback'), submit: $('cu-submit')
    };
    const canWrite = document.querySelector('.sa-toolbar').dataset.canWrite === '1';
    const canNotes = document.querySelector('.sa-toolbar').dataset.canNotes === '1';
    // ?search= es el destino de la búsqueda global; misma densidad que la referencia.
    const state = { search: (new URLSearchParams(location.search).get('search') || '').trim(), page: 1, perPage: 5 };
    if (state.search) $('cu-search').value = state.search;
    let searchTimer, feedbackTimer, requestId = 0, opener = null;

    async function api(url, options = {}) {
        const headers = { ...(options.headers || {}) };
        if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
        const response = await fetch(url, { credentials: 'same-origin', ...options, headers });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(data.error || (response.status === 403 ? 'No tiene permiso para esta acción.' : 'No se pudieron cargar los clientes.'));
            error.status = response.status;
            throw error;
        }
        return data;
    }
    function node(tag, text, className) {
        const el = document.createElement(tag);
        if (text !== undefined && text !== null) el.textContent = text;
        if (className) el.className = className;
        return el;
    }
    function toast(text, tone) {
        els.feedback.textContent = text;
        els.feedback.className = `sv-feedback is-${tone}`;
        els.feedback.hidden = false;
        clearTimeout(feedbackTimer);
        feedbackTimer = setTimeout(() => { els.feedback.hidden = true; }, 7000);
    }
    function cell(text, className) {
        const td = node('td', undefined, className);
        const span = node('span', text || '—', className === undefined ? 'sa-ref' : undefined);
        if (text) span.title = text;
        td.append(span);
        return td;
    }
    function renderRows(items) {
        body.replaceChildren();
        items.forEach((customer) => {
            const tr = node('tr');
            tr.dataset.customerId = customer.id;
            tr.append(cell(customer.name), cell(customer.tax_id, 'sa-muted'), cell(customer.phone, 'sa-muted'), cell(customer.email, 'sa-muted'));
            const status = node('td');
            status.append(node('span', customer.is_active ? 'Activo' : 'Inactivo', `px-badge sa-badge is-${customer.is_active ? 'green' : 'gray'}`));
            tr.append(status);
            if (canNotes) {
                // Historial del cliente: notas de entrega emitidas con este cliente del directorio.
                const notes = node('td');
                const link = node('a', 'Ver notas', 'sa-link');
                link.href = `/delivery-notes?customer_id=${encodeURIComponent(customer.id)}`;
                link.setAttribute('aria-label', `Ver notas de entrega de ${customer.name}`);
                notes.append(link);
                tr.append(notes);
            }
            body.append(tr);
        });
        els.empty.hidden = items.length > 0;
    }
    function pageButton(text, page, options = {}) {
        const button = node('button', text, `px-pg${options.active ? ' is-active' : ''}`);
        button.type = 'button';
        if (options.label) { button.setAttribute('aria-label', options.label); button.innerHTML = `<i class="ti ${options.icon}" aria-hidden="true"></i>`; }
        if (options.active) button.setAttribute('aria-current', 'page');
        button.disabled = options.disabled === true;
        button.addEventListener('click', () => { state.page = page; load(); });
        return button;
    }
    function renderPages(data) {
        els.pages.replaceChildren();
        const pages = Math.max(data.pages, 1);
        els.pages.append(pageButton('', Math.max(1, data.page - 1), { label: 'Página anterior', icon: 'ti-chevron-left', disabled: data.page <= 1 }));
        const visible = new Set([1, pages, data.page - 1, data.page, data.page + 1].filter((p) => p >= 1 && p <= pages));
        let previous = 0;
        [...visible].sort((a, b) => a - b).forEach((p) => {
            if (p - previous > 1) { const gap = node('span', '...', 'px-pg is-gap'); gap.setAttribute('aria-hidden', 'true'); els.pages.append(gap); }
            els.pages.append(pageButton(String(p), p, { active: p === data.page }));
            previous = p;
        });
        els.pages.append(pageButton('', Math.min(pages, data.page + 1), { label: 'Página siguiente', icon: 'ti-chevron-right', disabled: data.page >= pages }));
        const first = data.total ? (data.page - 1) * data.per_page + 1 : 0;
        els.summary.textContent = data.total ? `Mostrando ${first} a ${first + data.count - 1} de ${data.total} clientes` : 'Sin clientes registrados';
    }
    async function load() {
        const current = ++requestId;
        body.setAttribute('aria-busy', 'true');
        const params = new URLSearchParams({ page: String(state.page), per_page: String(state.perPage) });
        if (state.search) params.set('search', state.search);
        try {
            const data = await api(`/api/customers?${params}`);
            if (current !== requestId) return;
            renderRows(data.items);
            renderPages(data);
        } catch (error) {
            if (current !== requestId) return;
            body.replaceChildren();
            els.summary.textContent = error.message;
            els.pages.replaceChildren();
        } finally {
            if (current === requestId) body.setAttribute('aria-busy', 'false');
        }
    }
    els.search.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => { state.search = els.search.value.trim(); state.page = 1; load(); }, 250);
    });

    /* ---------- Nuevo cliente ---------- */
    function openDrawer() {
        if (!canWrite) return;
        opener = document.activeElement;
        els.form.reset();
        els.drawerFeedback.hidden = true;
        els.drawer.showModal();
        $('cu-name').focus();
    }
    function closeDrawer() {
        els.drawer.close();
        if (opener) opener.focus();
    }
    els.newButton.addEventListener('click', openDrawer);
    $('cu-close').addEventListener('click', closeDrawer);
    $('cu-cancel').addEventListener('click', closeDrawer);
    els.drawer.addEventListener('cancel', (event) => { event.preventDefault(); closeDrawer(); });
    els.form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const name = $('cu-name').value.trim();
        if (!name) {
            els.drawerFeedback.textContent = 'El nombre es obligatorio.';
            els.drawerFeedback.hidden = false;
            $('cu-name').focus();
            return;
        }
        els.submit.disabled = true;
        try {
            const customer = await api('/api/customers', {
                method: 'POST',
                body: JSON.stringify({
                    name, tax_id: $('cu-tax-id').value.trim(), phone: $('cu-phone').value.trim(),
                    email: $('cu-email').value.trim(), is_active: $('cu-active').value === 'true'
                })
            });
            closeDrawer();
            toast(`Cliente '${customer.name}' creado correctamente.`, 'success');
            state.search = ''; els.search.value = ''; state.page = 1;
            load();
        } catch (error) {
            els.drawerFeedback.textContent = error.message;
            els.drawerFeedback.hidden = false;
        } finally {
            els.submit.disabled = false;
        }
    });
    load();
})();
