/* Live Gestión de Productos: misma UI ix, datos y escrituras reales. No corre en ?ref=1. */
(function () {
    'use strict';
    if (new URLSearchParams(location.search).get('ref') === '1') return;

    const canWrite = window.CAN_WRITE === true;
    const canApprove = window.CAN_APPROVE_PRODUCTS === true;
    const canViewCost = window.CAN_VIEW_COST === true;
    const canReadSuppliers = window.CAN_READ_SUPPLIERS === true;
    const STATUS = {
        disponible: { label: 'Disponible', tone: 'green' },
        bajo_stock: { label: 'Bajo stock', tone: 'orange' },
        agotado: { label: 'Agotado', tone: 'red' },
        inactivo: { label: 'Inactivo', tone: 'gray' }
    };
    const UNITS = ['Unidad', 'Caja', 'Metro', 'Kilogramo', 'unidad'];
    const state = {
        search: '', categoryId: '', supplierId: '', status: '', approvalStatus: '',
        priceMin: '', priceMax: '', priceCurrency: '', stockMin: '', stockMax: '',
        dateMin: '', dateMax: '', sort: 'name-asc', page: 1, perPage: 8
    };
    const els = {
        search: document.getElementById('ix-prod-search'),
        category: document.getElementById('ix-filter-category'),
        supplier: document.getElementById('ix-filter-supplier'),
        status: document.getElementById('ix-filter-status'),
        approval: document.getElementById('ix-filter-approval'),
        clear: document.getElementById('ix-clear-filters'),
        body: document.getElementById('ix-catalog-body'),
        wrap: document.getElementById('ix-catalog-wrap'),
        summary: document.getElementById('ix-page-summary'),
        pages: document.getElementById('ix-pages'),
        perPageText: document.getElementById('ix-per-page-text'),
        catRows: document.getElementById('ix-cat-rows'),
        alerts: document.getElementById('ix-alert-list'),
        recent: document.getElementById('ix-recent'),
        drawer: document.getElementById('np-drawer'),
        form: document.getElementById('np-form'),
        dialog: document.getElementById('pl-dialog')
    };
    const $pl = (id) => document.getElementById(`pl-${id}`);
    let categories = [];
    let searchTimer;
    let listRequestId = 0;
    let feedbackTimer;
    let layerFocus;
    let oldOverflow = '';
    let oldPadding = '';
    let selectedId = null;
    let editProduct = null;
    let exportPending = false;
    const imageEditor = window.createProductImageEditor({ request: api, changed: () => reload(), canWrite });
    const proposalReview = window.createProductProposalReview({
        request: api, canApprove,
        categoryName: id => categories.find(item => item.id === id)?.name,
        reviewed: product => { fillDetail(product); reload(); }
    });

    function toolbarButton(label) {
        return [...document.querySelectorAll('.iv-prod-toolbar button')]
            .find((button) => button.textContent.trim() === label);
    }
    async function api(url, options = {}) {
        const headers = { ...(options.headers || {}) };
        if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) {
            headers['Content-Type'] = 'application/json';
        }
        const response = await fetch(url, { credentials: 'same-origin', ...options, headers });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(data.error || messageForStatus(response.status));
            error.status = response.status;
            throw error;
        }
        return data;
    }
    function messageForStatus(status) {
        if (status === 400) return 'Datos inválidos.';
        if (status === 403) return 'No tiene permiso para esta acción.';
        if (status === 404) return 'El producto no existe.';
        if (status === 409) return 'Conflicto con el estado actual.';
        return 'No se pudieron cargar los productos';
    }
    function money(value, currency) {
        if (value == null || value === '' || !Number.isFinite(Number(value))) return '—';
        const symbol = {USD: '$', VES: 'Bs.'}[currency] || 'Moneda no registrada ·';
        return `${symbol} ${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }
    function formatWhen(iso) {
        if (!iso) return '—';
        const date = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}Z`);
        if (Number.isNaN(date.getTime())) return '—';
        const parts = (value) => Object.fromEntries(new Intl.DateTimeFormat('en', {
            timeZone: 'America/Caracas', year: 'numeric', month: 'numeric', day: 'numeric'
        }).formatToParts(value).map((part) => [part.type, part.value]));
        const start = (value) => {
            const day = parts(value);
            return Date.UTC(Number(day.year), Number(day.month) - 1, Number(day.day));
        };
        const days = Math.round((start(new Date()) - start(date)) / 86400000);
        const time = date.toLocaleTimeString('es-VE', { timeZone: 'America/Caracas', hour: '2-digit', minute: '2-digit', hour12: true })
            .replace(/\u00a0/g, ' ')
            .replace('a. m.', 'a.m.')
            .replace('p. m.', 'p.m.');
        if (days === 0) return `Hoy, ${time}`;
        if (days === 1) return `Ayer, ${time}`;
        const months = ['ene.', 'feb.', 'mar.', 'abr.', 'may.', 'jun.', 'jul.', 'ago.', 'sept.', 'oct.', 'nov.', 'dic.'];
        const day = parts(date);
        return `${day.day.padStart(2, '0')} ${months[Number(day.month) - 1]} ${day.year}, ${time}`;
    }
    function node(tag, text, className) {
        const el = document.createElement(tag);
        if (text !== undefined && text !== null) el.textContent = text;
        if (className) el.className = className;
        return el;
    }
    function toast(target, text) {
        target.textContent = text;
        target.hidden = false;
        clearTimeout(feedbackTimer);
        feedbackTimer = setTimeout(() => { target.hidden = true; }, 7000);
    }
    function fillSelect(select, items, placeholder) {
        if (!select) return;
        const current = select.value;
        select.replaceChildren();
        if (placeholder !== undefined) select.append(new Option(placeholder, ''));
        items.forEach((item) => select.append(new Option(item.label, item.value)));
        if ([...select.options].some((option) => option.value === current)) select.value = current;
    }
    async function loadSupplierFilter() {
        if (!canReadSuppliers || !els.supplier) return;
        const selects = [els.supplier, $pl('filter-supplier'), $pl('advanced-supplier')].filter(Boolean);
        try {
            const options = [];
            let page = 1;
            let pages;
            do {
                const data = await api(`/api/suppliers?page=${page}&per_page=24&sort=name_asc`);
                if (!data.available) throw new Error('No se pudieron cargar los proveedores.');
                options.push(...data.items.map((item) => ({ value: String(item.id), label: item.name })));
                pages = data.pagination.pages;
                page += 1;
            } while (page <= pages);
            selects.forEach((select) => {
                fillSelect(select, options, 'Todos los proveedores');
                select.disabled = false;
            });
        } catch (error) {
            selects.forEach((select) => {
                fillSelect(select, [], 'Error al cargar proveedores');
                select.title = error.message;
            });
        } finally {
            els.supplier.setAttribute('aria-busy', 'false');
        }
    }
    function listQuery(overrides = {}) {
        const params = new URLSearchParams();
        const merged = { ...state, ...overrides };
        if (merged.status) params.set('status', merged.status);
        else params.set('include_inactive', '1');
        if (merged.approvalStatus) params.set('approval_status', merged.approvalStatus);
        if (merged.search) params.set('search', merged.search);
        if (merged.categoryId) params.set('category_id', merged.categoryId);
        if (merged.supplierId) params.set('supplier_id', merged.supplierId);
        if (merged.priceCurrency) params.set('sale_price_currency', merged.priceCurrency);
        if (merged.priceMin !== '' && merged.priceMin != null) params.set('price_min', merged.priceMin);
        if (merged.priceMax !== '' && merged.priceMax != null) params.set('price_max', merged.priceMax);
        if (merged.stockMin !== '' && merged.stockMin != null) params.set('stock_min', merged.stockMin);
        if (merged.stockMax !== '' && merged.stockMax != null) params.set('stock_max', merged.stockMax);
        if (merged.dateMin) params.set('updated_from', merged.dateMin);
        if (merged.dateMax) params.set('updated_to', merged.dateMax);
        params.set('sort', merged.sort || 'name-asc');
        params.set('page', String(merged.page || 1));
        params.set('per_page', String(merged.perPage || 8));
        return params;
    }

    function thumb(url, name, className) {
        const wrap = node('span', undefined, `ix-thumb ${className || 'iv-catalog-thumb'}`);
        const placeholder = node('span', undefined, 'iv-image-placeholder');
        placeholder.setAttribute('role', 'img');
        placeholder.setAttribute('aria-label', 'Sin imagen del producto');
        placeholder.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m7.5 4.27 9 5.15M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5M12 22V12"/></svg>';
        wrap.append(placeholder);
        if (url) {
            const img = node('img');
            img.alt = name || '';
            img.addEventListener('load', () => { placeholder.hidden = true; });
            img.addEventListener('error', () => { img.remove(); placeholder.hidden = false; });
            img.src = url;
            wrap.append(img);
        }
        return wrap;
    }
    function alertIcon(tone) {
        const icon = node('span', undefined, `iv-alert-icon is-${tone}`);
        icon.setAttribute('aria-hidden', 'true');
        icon.innerHTML = tone === 'orange'
            ? '<svg viewBox="0 0 24 24" fill="currentColor" class="ix-svg" aria-hidden="true"><path d="M10.3 3.6 2.2 17.9A2 2 0 0 0 3.9 21h16.2a2 2 0 0 0 1.7-3.1L13.7 3.6a2 2 0 0 0-3.4 0z"/><path d="M12 9v5" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="17.2" r="1.1" fill="#fff"/></svg>'
            : '<svg viewBox="0 0 24 24" fill="currentColor" class="ix-svg" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="m8.6 8.6 6.8 6.8M15.4 8.6l-6.8 6.8" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/></svg>';
        return icon;
    }
    function statusBadge(status) {
        const meta = STATUS[status] || STATUS.inactivo;
        return node('span', meta.label, `px-badge iv-badge-dot is-${meta.tone}`);
    }
    function rowActions(product) {
        const wrap = node('div', undefined, 'iv-row-actions');
        const eye = node('button', undefined, 'iv-icon-btn');
        eye.type = 'button';
        eye.setAttribute('aria-label', 'Ver detalle');
        eye.innerHTML = '<i class="ti ti-eye" aria-hidden="true"></i>';
        eye.addEventListener('click', () => openLayer('detail', product.id));
        wrap.append(eye);
        if (window.canProposeProductChange(product, canWrite)) {
            const edit = node('button', undefined, 'iv-icon-btn');
            edit.type = 'button';
            edit.setAttribute('aria-label', 'Proponer modificación');
            edit.innerHTML = '<i class="ti ti-pencil" aria-hidden="true"></i>';
            edit.addEventListener('click', () => openLayer('edit', product.id));
            const more = node('button', undefined, 'iv-icon-btn');
            more.type = 'button';
            more.setAttribute('aria-label', 'Más acciones');
            more.innerHTML = '<i class="ti ti-dots-vertical" aria-hidden="true"></i>';
            more.addEventListener('click', () => toggleActive(product));
            wrap.append(edit, more);
        }
        return wrap;
    }

    function hasCatalogFilters() {
        return ['search', 'categoryId', 'supplierId', 'status', 'approvalStatus', 'priceMin', 'priceMax', 'priceCurrency',
            'stockMin', 'stockMax', 'dateMin', 'dateMax'].some((key) => state[key] !== '');
    }
    function renderRows(items, total) {
        els.body.replaceChildren();
        if (!items.length) {
            const emptyCatalog = total === 0 && !hasCatalogFilters();
            const cell = node('td', emptyCatalog
                ? 'No hay productos registrados'
                : 'No se encontraron productos con estos filtros.');
            cell.colSpan = 9;
            cell.className = 'px-cell-muted';
            const row = node('tr');
            row.append(cell);
            if (emptyCatalog && canWrite) {
                const cta = node('button', 'Nuevo producto', 'px-btn is-primary');
                cta.type = 'button';
                cta.addEventListener('click', openCreate);
                cell.append(document.createTextNode('. '));
                cell.append(cta);
            }
            els.body.append(row);
            return;
        }
        items.forEach((product) => {
            const meta = STATUS[product.stock_status] || STATUS.disponible;
            const row = node('tr');
            const nameCell = node('td');
            const text = node('span', undefined, 'px-product-text');
            text.append(node('span', product.name, 'px-product-name'));
            text.children[0].title = product.name;
            if (product.brand && product.brand !== product.code && product.brand !== product.name) {
                text.append(node('span', product.brand, 'px-product-sku'));
            }
            nameCell.append(text);
            const stock = node('strong', String(product.current_stock), `iv-num is-${meta.tone}`);
            [
                thumb(product.image_url, product.name),
                node('td', product.code, 'iv-code'),
                nameCell,
                node('td', product.category || '—', 'px-cell-muted'),
                node('td', money(product.sale_price, product.sale_price_currency), 'iv-price'),
                (() => { const td = node('td'); td.append(stock); return td; })(),
                (() => {
                    const td = node('td');
                    td.append(statusBadge(product.stock_status));
                    if (product.has_pending_change || product.approval_status === 'pending') {
                        td.append(node('span', product.has_pending_change ? 'Cambio pendiente' : 'Alta pendiente', 'product-approval-badge'));
                    } else {
                        td.append(node('span', product.approval_status === 'rejected' ? 'Rechazado' : 'Aprobado', `product-approval-badge is-${product.approval_status}`));
                    }
                    return td;
                })(),
                node('td', formatWhen(product.updated_at), 'px-cell-muted'),
                (() => { const td = node('td'); td.append(rowActions(product)); return td; })()
            ].forEach((cell, index) => {
                if (index === 1 || index === 3) cell.title = cell.textContent;
                row.append(index === 0 ? (() => { const td = node('td'); td.append(cell); return td; })() : cell);
            });
            els.body.append(row);
        });
    }
    function renderPager(pagination) {
        const pg = pagination || { total: 0, page: 1, per_page: state.perPage, pages: 0, has_next: false, has_prev: false };
        const start = pg.total ? (pg.page - 1) * pg.per_page + 1 : 0;
        const end = Math.min(pg.page * pg.per_page, pg.total);
        els.summary.textContent = `Mostrando ${start} a ${end} de ${pg.total} productos${hasCatalogFilters() ? " · según filtros" : " · activos e inactivos"}`;
        els.perPageText.textContent = `${pg.per_page} por página`;
        els.pages.replaceChildren();
        const prev = node('button', undefined, 'px-pg');
        prev.type = 'button';
        prev.setAttribute('aria-label', 'Página anterior');
        prev.innerHTML = '<i class="ti ti-chevron-left" aria-hidden="true"></i>';
        prev.disabled = !pg.has_prev;
        prev.addEventListener('click', () => { state.page = pg.page - 1; reload(); });
        els.pages.append(prev);
        for (let n = 1; n <= (pg.pages || 0); n += 1) {
            const btn = node('button', String(n), `px-pg${n === pg.page ? ' is-active' : ''}`);
            btn.type = 'button';
            if (n === pg.page) btn.setAttribute('aria-current', 'page');
            btn.addEventListener('click', () => { state.page = n; reload(); });
            els.pages.append(btn);
        }
        const next = node('button', undefined, 'px-pg');
        next.type = 'button';
        next.setAttribute('aria-label', 'Página siguiente');
        next.innerHTML = '<i class="ti ti-chevron-right" aria-hidden="true"></i>';
        next.disabled = !pg.has_next;
        next.addEventListener('click', () => { state.page = pg.page + 1; reload(); });
        els.pages.append(next);
    }
    // KPI cards: state at the close of the selected period (today = live). Catalog/aside stay current.
    const KPI_CARDS = { total_products: 'total', low_stock: 'low', out_of_stock: 'out', active_categories: 'cats' };
    const historyBar = document.getElementById('ix-product-history');
    let historyRequest = 0;
    function shortDate(iso, withYear = true) {
        const options = withYear ? { day: '2-digit', month: 'short', year: 'numeric' } : { day: '2-digit', month: 'short' };
        return new Date(`${iso}T12:00:00`).toLocaleDateString('es', options)
            .replaceAll(' de ', ' ').replace(/sept\.?/, 'sep.').replace(/\b(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\b\.?/g, '$1.');
    }
    // Same shape as the reference card: "vs. 01 abr. - 30 abr. 2024" (year once when both dates share it).
    function rangeLabel(start, end) {
        return `vs. ${shortDate(start, start.slice(0, 4) !== end.slice(0, 4))} - ${shortDate(end)}`;
    }
    function renderKpi(key, kpi, sub) {
        const id = KPI_CARDS[key];
        const trend = document.getElementById(`ix-kpi-${id}-trend`);
        document.getElementById(`ix-kpi-${id}`).textContent = kpi.value === null ? '—' : String(kpi.value);
        document.getElementById(`ix-kpi-${id}-sub`).textContent = sub;
        const card = trend.closest('article');
        const accessibleLabel = `${card.querySelector('.ix-kpi-label').textContent}: ${kpi.value == null ? 'Sin cierre disponible' : kpi.value}. ${sub}`;
        card.setAttribute('aria-label', accessibleLabel);
        card.title = accessibleLabel;
        trend.hidden = kpi.delta === null;
        if (kpi.delta === null) return;
        const arrow = kpi.delta > 0 ? 'up' : (kpi.delta < 0 ? 'down' : null);
        let text = String(Math.abs(kpi.delta));
        if (key === 'total_products') {
            text = kpi.change_percent !== null ? `${Math.abs(kpi.change_percent).toFixed(1)}%` : (kpi.delta > 0 ? 'Nuevo' : '—');
        } else if (kpi.change_percent === null && kpi.delta > 0) {
            text = `${text} · Nuevo`;
        }
        trend.className = `ix-trend is-${{ positive: 'green', negative: 'red' }[kpi.sentiment] || 'gray'}`;
        trend.replaceChildren(node('i', undefined, `ti ti-${arrow ? `arrow-${arrow}` : 'minus'}`), document.createTextNode(text));
        trend.firstChild.setAttribute('aria-hidden', 'true');
    }
    function renderHistory(data) {
        const info = document.getElementById('ix-history-info');
        const period = data.period.end === data.today ? 'Estado actual · sin filtros' : `Cierre del ${shortDate(data.period.end)}`;
        let sub = data.period.available ? period : 'Sin cierre para esta fecha';
        let note = data.period.available
            ? 'Los indicadores cuentan productos activos, sin filtros del catálogo.'
            : 'No hay un cierre guardado para esta fecha. El catálogo muestra datos actuales.';
        if (data.period.available && data.comparison.available) {
            sub = `${period} · ${rangeLabel(data.comparison.start, data.comparison.end)}`;
        } else if (data.comparison.type !== 'none') {
            note += ' Comparación histórica no disponible: ' + (data.history.available_from
                ? `el historial comienza el ${shortDate(data.history.available_from)}.`
                : 'aún no hay cierres diarios guardados.');
        }
        info.title = note;
        info.setAttribute('aria-label', note);
        document.getElementById('ix-history-note').textContent = note;
        Object.keys(KPI_CARDS).forEach((key) => renderKpi(key, data.kpis[key], sub));
    }
    async function loadHistory() {
        if (!historyBar || !historyBar.dataset.start) return;
        const request = ++historyRequest;
        const section = document.querySelector('.iv-kpis');
        section.setAttribute('aria-busy', 'true');
        const params = new URLSearchParams({ start: historyBar.dataset.start, end: historyBar.dataset.end, compare: historyBar.dataset.comparison || 'previous' });
        try {
            const data = await api(`/api/products/history/summary?${params}`);
            if (request === historyRequest) renderHistory(data);
        } catch (error) {
            if (request !== historyRequest) return;
            document.getElementById('ix-history-note').textContent = error.message || 'No se pudo cargar el histórico';
            Object.keys(KPI_CARDS).forEach((key) => renderKpi(key, { value: null, delta: null }, 'Indicador no disponible'));
        } finally {
            if (request === historyRequest) section.setAttribute('aria-busy', 'false');
        }
    }
    document.addEventListener('ix-product-history-change', loadHistory);
    function renderSummary(summary) {
        els.catRows.replaceChildren();
        summary.category_distribution.forEach((row) => {
            const li = node('li');
            li.append(
                node('span', row.name, 'iv-cat-row-name'),
                node('span', String(row.count), 'iv-cat-row-num'),
                node('span', `${row.percent}%`, 'iv-cat-row-num')
            );
            const bar = node('span', undefined, 'px-bar iv-bar iv-cat-bar');
            const fill = node('span');
            fill.style.width = `${row.percent}%`;
            bar.append(fill);
            li.append(bar);
            els.catRows.append(li);
        });
        els.alerts.replaceChildren();
        if (!summary.inventory_alerts.length) {
            els.alerts.append(node('li', 'No hay alertas de inventario.', 'px-cell-muted'));
        }
        summary.inventory_alerts.forEach((product) => {
            const meta = STATUS[product.stock_status] || STATUS.bajo_stock;
            const li = node('li');
            const text = node('span', undefined, 'ix-row-text');
            text.append(node('span', product.name, 'ix-row-title'), node('span', `Stock actual: ${product.current_stock} unidades`, 'ix-row-desc'));
            li.append(alertIcon(meta.tone), thumb(product.image_url, product.name, 'iv-alert-thumb'), text, statusBadge(product.stock_status));
            els.alerts.append(li);
        });
        els.recent.replaceChildren();
        const recent = summary.recent_products[0];
        if (!recent) {
            els.recent.append(node('span', 'No hay productos recientes.', 'px-cell-muted'));
            return;
        }
        const text = node('span', undefined, 'ix-row-text');
        text.append(node('span', recent.name, 'ix-row-title'), node('span', `Código: ${recent.code}`, 'ix-row-desc'));
        const side = node('span', undefined, 'ix-row-side');
        side.append(node('span', `Stock: ${recent.current_stock}`, 'iv-recent-stock'), node('span', formatWhen(recent.created_at), 'ix-row-time'));
        els.recent.append(thumb(recent.image_url, recent.name, 'iv-recent-thumb'), text, side);
    }

    async function reload() {
        history.replaceState(history.state, '', window.productFilters.url(state, location.href));
        const requestId = ++listRequestId;
        els.wrap.setAttribute('aria-busy', 'true');
        try {
            const [list, summary] = await Promise.all([
                api(`/api/products?${listQuery()}`),
                api('/api/products/summary')
            ]);
            if (requestId !== listRequestId) return;
            renderRows(list.items, list.count);
            renderPager(list.pagination);
            renderSummary(summary);
            loadHistory();
        } catch (error) {
            if (requestId !== listRequestId) return;
            els.body.replaceChildren();
            const cell = node('td', error.message || 'No se pudieron cargar los productos');
            cell.colSpan = 9;
            const retry = node('button', 'Reintentar', 'px-btn');
            retry.type = 'button';
            retry.addEventListener('click', reload);
            cell.append(document.createTextNode(' '));
            cell.append(retry);
            const row = node('tr');
            row.append(cell);
            els.body.append(row);
            els.summary.textContent = '';
        } finally {
            if (requestId === listRequestId) els.wrap.setAttribute('aria-busy', 'false');
        }
    }

    function resetFilters() {
        clearTimeout(searchTimer);
        state.search = '';
        state.categoryId = '';
        state.supplierId = '';
        state.status = '';
        state.approvalStatus = '';
        state.priceMin = state.priceMax = state.stockMin = state.stockMax = '';
        state.priceCurrency = '';
        state.dateMin = state.dateMax = '';
        state.sort = 'name-asc';
        state.page = 1;
        state.perPage = 8;
        if (els.search) els.search.value = '';
        if (els.category) els.category.value = '';
        if (els.supplier) els.supplier.value = '';
        if (els.status) els.status.value = '';
        if (els.approval) els.approval.value = '';
        const advanced = $pl('advanced');
        if (advanced) advanced.reset();
        reload();
    }

    function lockBody() {
        oldOverflow = document.body.style.overflow;
        oldPadding = document.body.style.paddingRight;
        const gutter = innerWidth - document.documentElement.clientWidth;
        if (gutter) document.body.style.paddingRight = `${parseFloat(getComputedStyle(document.body).paddingRight) + gutter}px`;
        document.body.style.overflow = 'hidden';
    }
    function unlockBody() {
        document.body.style.overflow = oldOverflow;
        document.body.style.paddingRight = oldPadding;
    }

    function derivedStatus() {
        const stock = Number(document.getElementById('np-stock')?.value || 0);
        const minimum = Number(document.getElementById('np-minimum')?.value || 0);
        if (stock === 0) return 'Agotado';
        if (stock <= minimum) return 'Bajo stock';
        return 'Disponible';
    }
    function syncCreateStatus() {
        const select = document.getElementById('np-status');
        if (!select) return;
        select.value = derivedStatus();
        select.parentElement.dataset.status = select.value;
    }

    let stagedSuppliers = [];
    let pendingProductId = null;
    function supplierFields(container) {
        if (!container || container.dataset.ready) return container;
        container.dataset.ready = '1';
        const pick = node('select');
        pick.append(new Option('Seleccione un proveedor', ''));
        const cost = node('input');
        cost.type = 'number'; cost.min = '0'; cost.step = '0.01'; cost.placeholder = '0.00';
        const currency = node('select');
        currency.append(new Option('Seleccione una moneda', ''), new Option('USD ($)', 'USD'), new Option('VES (Bs.)', 'VES'));
        const code = node('input'); code.maxLength = 50;
        const lead = node('input'); lead.type = 'number'; lead.min = '0'; lead.step = '1';
        const moq = node('input'); moq.type = 'number'; moq.min = '1'; moq.step = '1';
        const preferred = node('input'); preferred.type = 'checkbox';
        const button = node('button', 'Agregar proveedor');
        button.type = 'button'; button.className = 'ps-wide';
        const labeled = (title, control) => {
            const label = node('label', title);
            label.append(control);
            return label;
        };
        const prefer = node('label', ' Preferido');
        prefer.prepend(preferred);
        prefer.className = 'ps-wide';
        container.append(labeled('Proveedor', pick));
        if (canViewCost) container.append(labeled('Costo del proveedor', cost), labeled('Moneda del costo del proveedor', currency));
        container.append(labeled('Código del proveedor', code),
            labeled('Lead time (días, informativo)', lead), labeled('Cantidad mínima del proveedor (informativa)', moq), prefer, button);
        container._controls = { pick, cost, currency, code, lead, moq, preferred, button };
        return container;
    }
    function fillSupplierPick(pick, suppliers, used) {
        const current = pick.value;
        pick.replaceChildren(new Option('Seleccione un proveedor', ''));
        suppliers.filter((row) => !used.has(row.id)).forEach((row) => pick.append(new Option(row.name, String(row.id))));
        if ([...pick.options].some((option) => option.value === current)) pick.value = current;
    }
    function supplierPayload(controls) {
        if (!canViewCost || !controls.pick.value || controls.cost.value === '') throw new Error('Indique proveedor y costo del proveedor.');
        if (!['USD', 'VES'].includes(controls.currency.value)) throw new Error('Seleccione USD o VES para el costo del proveedor.');
        return {
            supplier_id: Number(controls.pick.value),
            purchase_price: controls.cost.value,
            purchase_price_currency: controls.currency.value,
            supplier_product_code: controls.code.value.trim(),
            lead_time_days: controls.lead.value === '' ? null : Number(controls.lead.value),
            minimum_order_quantity: controls.moq.value === '' ? null : Number(controls.moq.value),
            is_preferred: controls.preferred.checked
        };
    }
    function supplierCard(row) {
        const box = node('article', undefined, 'ps-row');
        const header = node('header', undefined, 'ps-card-header');
        header.append(node('h4', row.supplier_name || row.name || 'Proveedor'));
        if (row.operational_preferred || row.is_preferred) {
            header.append(node('span', row.operational_preferred ? 'Preferido' : 'Preferencia inactiva',
                `ps-preferred${row.operational_preferred ? '' : ' is-inactive'}`));
        }
        const list = node('dl', undefined, 'ps-details');
        const fields = [
            ['Código del proveedor', row.supplier_product_code || 'No registrado'],
            ['Entrega estimada', row.lead_time_days == null ? 'No registrada' : `${row.lead_time_days} días`],
            ['Cantidad mínima de pedido', row.minimum_order_quantity == null ? 'No registrada' : String(row.minimum_order_quantity)]
        ];
        if (canViewCost && Object.prototype.hasOwnProperty.call(row, 'purchase_price')) {
            fields.splice(1, 0, ['Costo referencial del proveedor', money(row.purchase_price, row.purchase_price_currency)]);
        }
        fields.forEach(([label, value]) => {
            const field = node('div');
            field.append(node('dt', label), node('dd', value));
            list.append(field);
        });
        box.append(header, list);
        if (row.is_active === false) box.append(node('p', 'Asociación inactiva', 'ps-note'));
        if (row.supplier_active === false) box.append(node('p', 'Proveedor inactivo', 'ps-note'));
        return box;
    }
    function renderSupplierRows(list, rows, actions) {
        list.replaceChildren();
        if (!rows.length) {
            list.append(node('p', 'Este producto no tiene proveedores.', 'ps-note'));
            return;
        }
        rows.forEach((row) => {
            const box = supplierCard(row);
            if (actions && row.is_active !== false && row.supplier_active !== false && !row.is_preferred) {
                const prefer = node('button', 'Marcar preferido');
                prefer.type = 'button';
                prefer.addEventListener('click', () => actions.prefer(row));
                box.append(prefer);
            }
            if (actions) {
                if (actions.edit && canViewCost) {
                    const edit = node('button', 'Editar condiciones');
                    edit.type = 'button';
                    edit.addEventListener('click', () => actions.edit(row));
                    box.append(edit);
                }
                const toggle = node('button', actions.draft ? 'Quitar' : (row.is_active === false ? 'Activar asociación' : 'Desactivar asociación'));
                toggle.type = 'button';
                toggle.addEventListener('click', () => actions.toggle(row));
                box.append(toggle);
            }
            list.append(box);
        });
    }
    async function mountCreateSuppliers() {
        const list = document.getElementById('np-supplier-list');
        const form = supplierFields(document.getElementById('np-supplier-form'));
        if (!list || !form) return;
        const data = await api('/api/products/supplier-options');
        const controls = form._controls;
        const draw = () => {
            const rows = stagedSuppliers.map((row) => ({
                ...row,
                supplier_name: (data.suppliers.find((item) => item.id === row.supplier_id) || {}).name,
                supplier_active: true,
                is_active: true,
                operational_preferred: row.is_preferred
            }));
            renderSupplierRows(list, rows, {
                draft: true,
                prefer: (row) => { stagedSuppliers.forEach((item) => { item.is_preferred = item.supplier_id === row.supplier_id; }); draw(); },
                toggle: (row) => { stagedSuppliers = stagedSuppliers.filter((item) => item.supplier_id !== row.supplier_id); draw(); }
            });
            fillSupplierPick(controls.pick, data.suppliers, new Set(stagedSuppliers.map((row) => row.supplier_id)));
        };
        controls.button.onclick = () => {
            try {
                const payload = supplierPayload(controls);
                if (stagedSuppliers.some((row) => row.supplier_id === payload.supplier_id)) throw new Error('Ese proveedor ya está en la lista.');
                if (payload.is_preferred) stagedSuppliers.forEach((row) => { row.is_preferred = false; });
                stagedSuppliers.push(payload);
                controls.cost.value = ''; controls.currency.value = ''; controls.code.value = ''; controls.lead.value = ''; controls.moq.value = ''; controls.preferred.checked = false;
                draw();
            } catch (error) { toast(document.getElementById('np-feedback'), error.message); }
        };
        draw();
    }
    async function loadProductSuppliers(productId, editable) {
        if (!canReadSuppliers) return;
        const section = document.getElementById('pl-suppliers');
        const list = document.getElementById('pl-supplier-list');
        const form = supplierFields(document.getElementById('pl-supplier-form'));
        if (!section || !list) return;
        section.hidden = false;
        if (form) form.hidden = !editable || !canWrite;
        let data;
        try {
            data = await api(`/api/products/${productId}/suppliers`);
        } catch (error) {
            list.replaceChildren(node('p', error.message, 'ps-note'));
            return;
        }
        const refresh = () => loadProductSuppliers(productId, editable);
        const fail = (error) => toast($pl('feedback'), error.message);
        const controls = form?._controls;
        if (controls) {
            controls.pick.disabled = false;
            controls.button.textContent = 'Agregar proveedor';
        }
        renderSupplierRows(list, data.items, editable && canWrite ? {
            edit: (row) => {
                controls.pick.append(new Option(row.supplier_name, String(row.supplier_id)));
                controls.pick.value = String(row.supplier_id);
                controls.pick.disabled = true;
                controls.cost.value = row.purchase_price;
                controls.currency.value = row.purchase_price_currency || '';
                controls.code.value = row.supplier_product_code || '';
                controls.lead.value = row.lead_time_days ?? '';
                controls.moq.value = row.minimum_order_quantity ?? '';
                controls.preferred.checked = row.is_preferred;
                controls.button.textContent = 'Guardar condiciones';
                controls.button.onclick = async () => {
                    try {
                        const payload = supplierPayload(controls);
                        delete payload.supplier_id;
                        await api(`/api/products/${productId}/suppliers/${row.id}`, {method: 'PATCH', body: JSON.stringify(payload)});
                        await refresh();
                    } catch (error) { fail(error); }
                };
                controls.cost.focus();
            },
            prefer: (row) => api(`/api/products/${productId}/suppliers/${row.id}`, { method: 'PATCH', body: JSON.stringify({ is_preferred: true }) }).then(refresh).catch(fail),
            toggle: (row) => api(`/api/products/${productId}/suppliers/${row.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: row.is_active === false }) }).then(refresh).catch(fail)
        } : null);
        if (!(editable && canWrite) || !form) return;
        fillSupplierPick(controls.pick, data.suppliers, new Set(data.items.map((row) => row.supplier_id)));
        controls.button.onclick = async () => {
            try {
                await api(`/api/products/${productId}/suppliers`, { method: 'POST', body: JSON.stringify(supplierPayload(controls)) });
                controls.cost.value = ''; controls.currency.value = ''; controls.code.value = ''; controls.lead.value = ''; controls.moq.value = ''; controls.preferred.checked = false;
                await refresh();
            } catch (error) { toast($pl('feedback'), error.message); }
        };
    }

    function openCreate() {
        if (!canWrite || !els.drawer) return;
        document.dispatchEvent(new CustomEvent('product:before-layer-open', { detail: 'np' }));
        document.querySelectorAll('dialog[open]').forEach((layer) => layer.close());
        els.form.reset();
        document.getElementById('np-feedback').hidden = true;
        document.getElementById('np-counter').textContent = '0/500';
        stagedSuppliers = [];
        pendingProductId = null;
        mountCreateSuppliers().catch((error) => toast(document.getElementById('np-feedback'), error.message));
        ['np-supplier'].forEach((id) => {
            const field = document.getElementById(id);
            if (!field) return;
            field.required = false;
            field.disabled = true;
            field.removeAttribute('aria-invalid');
        });
        const status = document.getElementById('np-status');
        status.disabled = true;
        syncCreateStatus();
        lockBody();
        els.drawer.showModal();
    }
    async function saveCreate() {
        const required = ['np-name', 'np-sku', 'np-category', 'np-unit'];
        let invalid = false;
        required.forEach((id) => {
            const field = document.getElementById(id);
            const bad = !field.value.trim();
            field.setAttribute('aria-invalid', String(bad));
            const error = document.getElementById(`${id}-error`);
            if (error) { error.textContent = 'Completa este campo.'; error.hidden = !bad; }
            if (bad) invalid = true;
        });
        if (invalid) return;
        const payload = {
            name: document.getElementById('np-name').value.trim(),
            code: document.getElementById('np-sku').value.trim(),
            category_id: Number(document.getElementById('np-category').value),
            unit: document.getElementById('np-unit').value.trim(),
            sale_price: document.getElementById('np-price').value || 0,
            sale_price_currency: document.getElementById('np-price-currency').value,
            current_stock: document.getElementById('np-stock').value || 0,
            minimum_stock: document.getElementById('np-minimum').value || 0,
            description: document.getElementById('np-description').value.trim(),
            brand: document.getElementById('np-brand').value.trim(),
            is_active: true
        };
        const costInput = document.getElementById('np-cost');
        if (costInput) {
            payload.purchase_price = costInput.value || 0;
            payload.purchase_price_currency = document.getElementById('np-cost-currency').value;
        }
        try {
            if (!pendingProductId) {
                const created = await api('/api/products', { method: 'POST', body: JSON.stringify(payload) });
                pendingProductId = created.id;
            }
            while (stagedSuppliers.length) {
                await api(`/api/products/${pendingProductId}/suppliers`, { method: 'POST', body: JSON.stringify(stagedSuppliers[0]) });
                stagedSuppliers.shift();
            }
            const created = { id: pendingProductId };
            pendingProductId = null;
            const file = document.getElementById('np-image').files[0];
            if (file) {
                if (file.size > 2 * 1024 * 1024) throw new Error('La imagen no puede superar 2 MB.');
                const form = new FormData();
                form.append('image', file);
                await api(`/api/products/${created.id}/image`, { method: 'POST', body: form });
            }
            els.drawer.close();
            toast(document.getElementById('np-feedback'), 'Propuesta de producto enviada a revisión del Administrador.');
            reload();
        } catch (error) {
            toast(document.getElementById('np-feedback'), error.message);
        }
    }

    function fillDetail(product) {
        proposalReview.render(product);
        $pl('action').hidden = !window.canProposeProductChange(product, canWrite);
        $pl('action').textContent = 'Proponer modificación';
        $pl('image').hidden = !product.image_url;
        if (product.image_url) $pl('image').src = product.image_url;
        else $pl('image').removeAttribute('src');
        $pl('image').alt = product.name;
        $pl('name').textContent = product.name;
        $pl('brand').textContent = product.brand || '';
        $pl('brand').hidden = !product.brand;
        $pl('code').textContent = `Código: ${product.code}`;
        const meta = STATUS[product.stock_status];
        $pl('status').textContent = meta.label;
        $pl('status').dataset.status = meta.label;
        $pl('detail').replaceChildren();
        const groups = [
            ['Información general', [
                ['Categoría', product.category || '—'],
                ['Unidad', product.unit || '—'],
                ['Marca', product.brand || '—'],
                ['Estado del catálogo', product.is_active ? 'Activo' : 'Inactivo'],
                ['Última actualización', formatWhen(product.updated_at)]
            ]],
            ['Precios', [['Precio de venta', money(product.sale_price, product.sale_price_currency)]].concat(
                canViewCost && Object.prototype.hasOwnProperty.call(product, 'purchase_price')
                    ? [['Costo de referencia', money(product.purchase_price, product.purchase_price_currency)]] : [])],
            ['Inventario', [['Stock actual', String(product.current_stock)], ['Stock mínimo', String(product.minimum_stock)]]]
        ];
        groups.forEach(([title, values]) => {
            const section = node('section', undefined, 'pl-section');
            const list = node('dl');
            values.forEach(([label, value]) => {
                list.append(node('dt', label), node('dd', value));
            });
            section.append(node('h3', title), list);
            $pl('detail').append(section);
        });
        const description = node('section', undefined, 'pl-section');
        description.append(node('h3', 'Descripción'), node('p', product.description || '—'));
        $pl('detail').append(description);
    }
    function fillEdit(product) {
        editProduct = product;
        imageEditor.reset(product);
        $pl('field-name').value = product.name;
        $pl('field-code').value = product.code;
        const select = $pl('field-category');
        // Si la categoría dejó de estar aprobada/activa, la edición exige seleccionar otra.
        select.value = String(product.category_id);
        $pl('field-unit').value = product.unit;
        if (![...$pl('field-unit').options].some((option) => option.value === product.unit)) {
            $pl('field-unit').append(new Option(product.unit, product.unit));
            $pl('field-unit').value = product.unit;
        }
        $pl('field-price').value = Number(product.sale_price).toFixed(2);
        $pl('field-price-currency').value = product.sale_price_currency || '';
        if ($pl('field-cost') && Object.prototype.hasOwnProperty.call(product, 'purchase_price')) {
            $pl('field-cost').value = Number(product.purchase_price).toFixed(2);
            $pl('field-cost-currency').value = product.purchase_price_currency || '';
        }
        $pl('field-stock').value = product.current_stock;
        $pl('field-stock').readOnly = true;
        $pl('field-minimum').value = product.minimum_stock;
        fillSelect($pl('field-status'), [{value: 'true', label: 'Activo'}, {value: 'false', label: 'Inactivo'}]);
        $pl('field-status').value = String(product.is_active);
        $pl('field-status').disabled = false;
        $pl('field-description').value = product.description || '';
        ['supplier'].forEach((key) => {
            const field = $pl(`field-${key}`);
            if (!field) return;
            field.required = false;
            field.disabled = true;
            field.value = '';
        });
        const brand = $pl('field-brand');
        if (brand) {
            brand.disabled = false;
            brand.required = false;
            brand.value = product.brand || '';
        }
        let note = document.getElementById('pl-stock-note');
        if (!note) {
            note = node('small', 'El stock se modifica desde Movimientos de Inventario.', 'pl-stock-note');
            note.id = 'pl-stock-note';
            $pl('field-stock').closest('label').append(note);
        }
    }
    async function openLayer(view, productId) {
        if (view === 'edit' && !canWrite) return;
        document.dispatchEvent(new CustomEvent('product:before-layer-open', { detail: 'pl' }));
        document.querySelectorAll('dialog[open]').forEach((layer) => layer.close());
        layerFocus = document.activeElement;
        const titles = {
            detail: 'Detalle del producto', edit: 'Proponer modificación', catalog: 'Catálogo completo de productos',
            export: 'Exportar catálogo', import: 'Importar productos desde CSV', advanced: 'Filtros avanzados'
        };
        els.dialog.dataset.view = view;
        $pl('title').textContent = titles[view];
        $pl('subtitle').hidden = ['detail', 'edit'].includes(view);
        ['detail', 'edit', 'catalog', 'export', 'import', 'advanced'].forEach((id) => { $pl(id).hidden = id !== view; });
        $pl('product').hidden = !['detail', 'edit'].includes(view);
        $pl('cancel').textContent = ['edit', 'export', 'import', 'advanced'].includes(view) ? 'Cancelar' : 'Cerrar';
        $pl('reset-filters').hidden = view !== 'advanced';
        $pl('action').hidden = ['catalog', 'detail'].includes(view);
        $pl('review').hidden = true;
        $pl('action').textContent = {
            detail: 'Proponer modificación', edit: 'Enviar propuesta', export: 'Exportar',
            import: 'Importar productos', advanced: 'Aplicar filtros'
        }[view] || '';
        $pl('feedback').hidden = true;
        const supplierSection = document.getElementById('pl-suppliers');
        if (supplierSection) supplierSection.hidden = !['detail', 'edit'].includes(view);
        if (['detail', 'edit'].includes(view)) {
            try {
                const product = await api(`/api/products/${productId}`);
                selectedId = product.id;
                fillDetail(product);
                if (view === 'edit') {
                    if (!window.canProposeProductChange(product, canWrite)) throw new Error('El producto requiere revisión antes de otra propuesta.');
                    fillEdit(product);
                    $pl('review').hidden = true;
                    $pl('action').textContent = 'Enviar propuesta';
                }
                await loadProductSuppliers(product.id, view === 'edit');
            } catch (error) {
                selectedId = null;
                $pl('action').hidden = true;
                toast($pl('feedback'), error.message);
            }
        }
        if (view === 'catalog') {
            if ($pl('filter-supplier')) $pl('filter-supplier').value = state.supplierId;
            $pl('pages').dataset.page = '1';
            renderCatalogOverlay();
        }
        if (view === 'advanced') {
            if ($pl('advanced-supplier')) $pl('advanced-supplier').value = state.supplierId;
            $pl('advanced').querySelectorAll('input[type=date]').forEach((input) => {
                input.parentElement.classList.toggle('has-value', Boolean(input.value));
            });
        }
        if (view === 'export') {
            $pl('export-error').hidden = true;
            const boxes = [...$pl('export').querySelectorAll('input[type=checkbox]')];
            const supplier = boxes[3];
            if (supplier) { supplier.checked = false; supplier.disabled = true; }
        }
        if (!els.dialog.open) {
            lockBody();
            els.dialog.showModal();
        }
    }
    async function renderCatalogOverlay() {
        const params = listQuery({
            search: $pl('search').value.trim(),
            categoryId: $pl('filter-category').value,
            supplierId: $pl('filter-supplier')?.value || '',
            status: $pl('filter-status').value,
            page: Number($pl('pages').dataset.page || 1),
            perPage: Number($pl('per-page').value)
        });
        try {
            const data = await api(`/api/products?${params}`);
            $pl('rows').replaceChildren();
            if (!data.items.length) {
                const row = node('tr');
                const cell = node('td', 'No se encontraron productos con estos filtros.');
                cell.colSpan = 8;
                row.append(cell);
                $pl('rows').append(row);
            }
            data.items.forEach((product) => {
                const row = node('tr');
                const img = node('img');
                img.src = product.image_url || '';
                img.alt = '';
                const actions = node('span');
                const eye = node('button', undefined, 'pl-row-button');
                eye.type = 'button';
                eye.setAttribute('aria-label', `Ver detalle de ${product.name}`);
                eye.append($pl('eye-icon').content.cloneNode(true));
                eye.addEventListener('click', () => openLayer('detail', product.id));
                actions.append(eye);
                if (window.canProposeProductChange(product, canWrite)) {
                    const edit = node('button', undefined, 'pl-row-button');
                    edit.type = 'button';
                    edit.setAttribute('aria-label', `Proponer modificación de ${product.name}`);
                    edit.append($pl('pencil-icon').content.cloneNode(true));
                    edit.addEventListener('click', () => openLayer('edit', product.id));
                    actions.append(edit);
                }
                [img, product.code, product.name, product.category || '—', money(product.sale_price, product.sale_price_currency),
                    String(product.current_stock), STATUS[product.stock_status]?.label, actions].forEach((value) => {
                    const cell = node('td');
                    cell.append(value instanceof Node ? value : document.createTextNode(value));
                    row.append(cell);
                });
                $pl('rows').append(row);
            });
            const pg = data.pagination || { page: 1, pages: 1, total: data.count, per_page: Number($pl('per-page').value) };
            $pl('pages').dataset.page = String(pg.page);
            $pl('summary').textContent = `Mostrando ${pg.total ? (pg.page - 1) * pg.per_page + 1 : 0} a ${Math.min(pg.page * pg.per_page, pg.total)} de ${pg.total} productos`;
        } catch (error) {
            toast($pl('feedback'), error.message);
        }
    }
    async function saveEdit() {
        if (!canWrite || !selectedId || imageEditor.busy) return;
        for (const [control, price] of [['price', 'sale_price'], ['cost', 'purchase_price']]) {
            const input = $pl(`field-${control}`), currency = $pl(`field-${control}-currency`);
            if (input && currency) currency.required = Number(input.value) !== Number(editProduct[price]);
        }
        if (!$pl('edit').reportValidity()) return;
        const payload = {
            name: $pl('field-name').value.trim(),
            code: $pl('field-code').value.trim(),
            category_id: Number($pl('field-category').value),
            unit: $pl('field-unit').value.trim(),
            sale_price: $pl('field-price').value,
            sale_price_currency: $pl('field-price-currency').value,
            minimum_stock: $pl('field-minimum').value,
            is_active: $pl('field-status').value === 'true',
            description: $pl('field-description').value.trim(),
            brand: $pl('field-brand') ? $pl('field-brand').value.trim() : ''
        };
        if ($pl('field-cost')) {
            payload.purchase_price = $pl('field-cost').value;
            payload.purchase_price_currency = $pl('field-cost-currency').value;
        }
        for (const price of ['sale_price', 'purchase_price']) {
            const currency = `${price}_currency`;
            if (Object.hasOwn(payload, price) && Number(payload[price]) === Number(editProduct[price])
                    && payload[currency] === (editProduct[currency] || '')) {
                delete payload[price]; delete payload[currency];
            }
        }
        imageEditor.setBusy(true);
        imageEditor.message('Enviando propuesta…');
        try {
            if (imageEditor.hasPendingFile) await imageEditor.upload(payload);
            else await api(`/api/products/${selectedId}`, { method: 'PUT', body: JSON.stringify(payload) });
            els.dialog.close();
            toast($pl('feedback'), 'Propuesta enviada. El producto vigente se conserva hasta su aprobación.');
            reload();
        } catch (error) {
            imageEditor.message(error.message || 'No se pudo enviar la propuesta.', true);
        } finally {
            imageEditor.setBusy(false);
        }
    }
    async function toggleActive(product) {
        if (!canWrite) return;
        const deactivate = product.is_active;
        const ok = window.confirm(deactivate
            ? '¿Proponer la desactivación del producto para revisión del Administrador?'
            : '¿Proponer la reactivación del producto para revisión del Administrador?');
        if (!ok) return;
        try {
            if (deactivate) await api(`/api/products/${product.id}`, { method: 'DELETE' });
            else await api(`/api/products/${product.id}`, { method: 'PUT', body: JSON.stringify({ is_active: true }) });
            toast($pl('feedback'), 'Propuesta de estado enviada. El estado vigente se conserva hasta la aprobación.');
            reload();
        } catch (error) {
            toast($pl('feedback') || document.getElementById('np-feedback'), error.message);
        }
    }
    function applyAdvanced() {
        const form = $pl('advanced');
        state.categoryId = form.elements.namedItem('category').value;
        state.supplierId = form.elements.namedItem('supplier')?.value || '';
        state.status = form.elements.namedItem('status').value;
        state.priceMin = form.elements.namedItem('price_min').value;
        state.priceMax = form.elements.namedItem('price_max').value;
        state.priceCurrency = form.elements.namedItem('sale_price_currency')?.value || '';
        state.stockMin = form.elements.namedItem('stock_min').value;
        state.stockMax = form.elements.namedItem('stock_max').value;
        state.dateMin = form.elements.namedItem('date_min').value;
        state.dateMax = form.elements.namedItem('date_max').value;
        state.sort = form.elements.namedItem('sort').value || 'name-asc';
        state.perPage = Number(form.elements.namedItem('size').value || 8);
        state.page = 1;
        if (els.category) els.category.value = state.categoryId;
        if (els.supplier) els.supplier.value = state.supplierId;
        if (els.status) els.status.value = state.status;
        els.dialog.close();
        reload();
    }

    async function exportCatalog() {
        if (exportPending) return;
        exportPending = true;
        const button = $pl('action');
        button.disabled = true;
        button.textContent = 'Exportando…';
        const form = $pl('export');
        $pl('export-error').hidden = true;
        const format = form.querySelector('[name="format"]:checked').value;
        const options = Object.fromEntries([...form.querySelectorAll('input[type=checkbox]')]
            .map((input) => [input.name, input.checked]));
        const params = listQuery();
        params.delete('page');
        params.delete('per_page');
        const filters = Object.fromEntries(params);
        filters.include_inactive = !state.status;
        try {
            const response = await fetch('/api/products/export', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ format, options, filters })
            });
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.error || 'No se pudo exportar el catálogo. Inténtalo de nuevo.');
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = response.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/)?.[1]
                || `catalogo_productos.${format}`;
            document.body.append(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            if (els.dialog.dataset.view === 'export') els.dialog.close();
            toast($pl('feedback'), 'Archivo generado. Descarga iniciada.');
        } catch (error) {
            $pl('export-error').textContent = error.message || 'No se pudo exportar el catálogo.';
            $pl('export-error').hidden = false;
        } finally {
            exportPending = false;
            button.disabled = false;
            if (els.dialog.dataset.view === 'export') button.textContent = 'Exportar';
        }
    }

    function trapLayerFocus(event) {
        if (event.key !== 'Tab' || !['detail', 'edit'].includes(els.dialog.dataset.view)) return;
        const controls = [...els.dialog.querySelectorAll('button, input, select, textarea, a[href], [tabindex]')]
            .filter((control) => !control.disabled && control.tabIndex >= 0 && control.getClientRects().length);
        const first = controls[0];
        const last = controls.at(-1);
        const target = event.shiftKey && document.activeElement === first ? last
            : (!event.shiftKey && document.activeElement === last ? first : null);
        if (!target) return;
        event.preventDefault();
        target.focus();
    }
    function bindLayerChrome() {
        if (!els.dialog) return;
        els.dialog.addEventListener('keydown', trapLayerFocus);
        $pl('edit').addEventListener('submit', event => { event.preventDefault(); saveEdit(); });
        els.dialog.addEventListener('close', () => {
            if (document.querySelector('dialog[open]')) return;
            unlockBody();
            layerFocus?.focus({ preventScroll: true });
        });
        els.dialog.querySelectorAll('[data-pl-close]').forEach((button) => {
            button.addEventListener('click', () => els.dialog.close());
        });
        $pl('action').addEventListener('click', () => {
            const view = els.dialog.dataset.view;
            if (view === 'detail' && canWrite) return openLayer('edit', selectedId);
            if (view === 'edit') return saveEdit();
            if (view === 'advanced') return applyAdvanced();
            if (view === 'export') return exportCatalog();
            if (view === 'import') return toast($pl('feedback'), 'La importación real queda pendiente: el esquema no incluye proveedor.');
        });
        $pl('reset-filters').addEventListener('click', () => { $pl('advanced').reset(); });
        $pl('advanced').addEventListener('input', (event) => {
            if (event.target.type === 'date') event.target.parentElement.classList.toggle('has-value', Boolean(event.target.value));
        });
        $pl('template-download').addEventListener('click', () => {
            $pl('template-feedback').hidden = false;
            $pl('template-feedback').textContent = 'No se generó ni descargó ningún archivo. La importación real no está habilitada.';
        });
        $pl('csv-file').addEventListener('change', (event) => {
            const file = event.target.files[0];
            const feedback = $pl('csv-feedback');
            if (!file) return;
            if (!/\.csv$/i.test(file.name) || file.size > 10 * 1024 * 1024) {
                feedback.textContent = 'Selecciona un solo archivo CSV de hasta 10 MB. No se importará.';
                feedback.classList.add('is-error');
                feedback.hidden = false;
                return;
            }
            feedback.textContent = `${file.name} seleccionado. La importación real no está habilitada (falta proveedor).`;
            feedback.classList.remove('is-error');
            feedback.hidden = false;
        });
        ['search', 'filter-category', 'filter-status', 'filter-supplier', 'per-page'].forEach((id) => {
            $pl(id)?.addEventListener(id === 'search' ? 'input' : 'change', () => {
                $pl('pages').dataset.page = '1';
                if (els.dialog.dataset.view === 'catalog') renderCatalogOverlay();
            });
        });
        const extraSort = [
            ['stock-desc', 'Stock: mayor a menor'],
            ['created-desc', 'Más recientes'],
            ['created-asc', 'Más antiguos'],
            ['updated-desc', 'Última actualización']
        ];
        extraSort.forEach(([value, label]) => {
            if (![...$pl('advanced-sort').options].some((option) => option.value === value)) {
                $pl('advanced-sort').append(new Option(label, value));
            }
        });
        fillSelect($pl('filter-status'), Object.entries(STATUS).map(([value, meta]) => ({ value, label: meta.label })), 'Todos');
        fillSelect($pl('advanced-status'), Object.entries(STATUS).map(([value, meta]) => ({ value, label: meta.label })), 'Todos los estados');
    }

    async function boot() {
        if (!canWrite) {
            const createBtn = toolbarButton('Nuevo producto');
            if (createBtn) createBtn.hidden = true;
        }
        const catLink = document.querySelector('.iv-cat-summary .ix-link-btn');
        if (catLink) {
            catLink.href = '/categories';
            catLink.removeAttribute('aria-disabled');
        }
        const catalogLink = document.querySelector('.iv-catalog .ix-link-btn');
        if (catalogLink) {
            catalogLink.removeAttribute('aria-disabled');
            catalogLink.addEventListener('click', (event) => { event.preventDefault(); openLayer('catalog'); });
        }
        const alertLink = document.querySelector('.iv-prod-alerts .ix-link-btn');
        if (alertLink) {
            alertLink.removeAttribute('aria-disabled');
            alertLink.addEventListener('click', (event) => { event.preventDefault(); openLayer('catalog'); });
        }
        const recentLink = document.querySelector('.iv-recent .ix-link-btn');
        if (recentLink) {
            recentLink.removeAttribute('aria-disabled');
            recentLink.addEventListener('click', (event) => {
                event.preventDefault();
                state.sort = 'created-desc';
                state.page = 1;
                reload();
            });
        }
        toolbarButton('Filtros')?.addEventListener('click', () => openLayer('advanced'));
        toolbarButton('Exportar')?.addEventListener('click', () => openLayer('export'));
        toolbarButton('Importar CSV')?.addEventListener('click', () => openLayer('import'));
        toolbarButton('Nuevo producto')?.addEventListener('click', openCreate);
        els.search?.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
                state.search = els.search.value.trim();
                state.page = 1;
                reload();
            }, 300);
        });
        els.category?.addEventListener('change', () => { state.categoryId = els.category.value; state.page = 1; reload(); });
        els.supplier?.addEventListener('change', () => { state.supplierId = els.supplier.value; state.page = 1; reload(); });
        els.status?.addEventListener('change', () => { state.status = els.status.value; state.page = 1; reload(); });
        els.clear?.addEventListener('click', resetFilters);
        if (els.drawer) {
            els.drawer.querySelectorAll('[data-np-close]').forEach((button) => button.addEventListener('click', () => els.drawer.close()));
            els.drawer.addEventListener('close', () => {
                if (document.querySelector('dialog[open]')) return;
                unlockBody();
                toolbarButton('Nuevo producto')?.focus({ preventScroll: true });
            });
            document.getElementById('np-save')?.addEventListener('click', saveCreate);
            document.getElementById('np-stock')?.addEventListener('input', syncCreateStatus);
            document.getElementById('np-minimum')?.addEventListener('input', syncCreateStatus);
            document.getElementById('np-description')?.addEventListener('input', (event) => {
                document.getElementById('np-counter').textContent = `${event.target.value.length}/500`;
            });
            document.getElementById('np-image')?.addEventListener('change', () => {
                const file = document.getElementById('np-image').files[0];
                const error = document.getElementById('np-image-error');
                if (!file) return;
                if (file.size > 2 * 1024 * 1024) {
                    error.textContent = 'Selecciona PNG, JPG o WEBP de hasta 2 MB.';
                    error.hidden = false;
                    document.getElementById('np-image').value = '';
                }
            });
        }
        bindLayerChrome();
        try {
            const data = await api('/api/categories');
            categories = data.items || [];
            const options = categories.map((item) => ({ value: String(item.id), label: item.name }));
            // Solo las categorías activas admiten productos nuevos; los filtros conservan todas.
            const active = categories.filter((item) => item.is_active === true && item.approval_status === 'approved')
                .map((item) => ({ value: String(item.id), label: item.name }));
            fillSelect(els.category, options, 'Todas');
            fillSelect(document.getElementById('np-category'), active, 'Selecciona una categoría');
            fillSelect($pl('field-category'), active, 'Selecciona una categoría');
            fillSelect($pl('filter-category'), options, 'Todos');
            fillSelect($pl('advanced-category'), options, 'Todas las categorías');
            fillSelect($pl('field-unit'), UNITS.map((unit) => ({ value: unit, label: unit })), 'Selecciona una unidad');
        } catch (error) {
            toast(document.getElementById('np-feedback'), error.message);
        }
        await loadSupplierFilter();
        window.productFilters.restore(state, location.search);
        if (!Object.hasOwn(STATUS, state.status)) state.status = '';
        if (els.search) els.search.value = state.search;
        if (els.category) els.category.value = state.categoryId;
        if (els.supplier) els.supplier.value = state.supplierId;
        if (els.status) els.status.value = state.status;
        if (els.approval) els.approval.value = state.approvalStatus;
        els.approval?.addEventListener('change', () => {
            state.approvalStatus = els.approval.value;
            state.page = 1;
            reload();
        });
        reload();
    }

    boot();
})();
