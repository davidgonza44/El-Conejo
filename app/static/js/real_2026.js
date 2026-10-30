/* A separate historical source; no operational endpoints or stock mutations. */
(() => {
    'use strict';
    const root = document.getElementById('real-history');
    if (!root || root.dataset.available !== 'true') return;
    const $ = (id) => document.getElementById(`rh-${id}`);
    const base = '/api/historical-2026/imports';
    const state = {batch: null, product: null, token: null, page: 1, pages: 1, searchRequest: 0,
        productRequest: 0, batchRequest: 0, eligible: false};
    const labels = {preview: 'Vista previa · pendiente de confirmación', confirmed: 'Versión confirmada vigente',
        superseded: 'Versión sustituida · solo auditoría', reverted: 'Versión revertida · solo auditoría'};
    const numbers = new Intl.NumberFormat('es-VE', {maximumFractionDigits: 2});
    const dates = new Intl.DateTimeFormat('es-VE', {day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC'});
    const cohortLabels = {STRICT: 'Estricta', SENSITIVITY: 'Sensibilidad', QUARANTINE: 'En revisión'};
    const chartDates = new Intl.DateTimeFormat('es-VE', {day: 'numeric', month: 'short', timeZone: 'UTC'});
    const chartCache = new Map();
    const chartObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver((entries) => {
        for (const entry of entries) {
            const cached = chartCache.get(entry.target);
            const width = Math.round(entry.target.getBoundingClientRect().width);
            if (cached && width > 0 && width !== cached.width) renderChart(entry.target, cached);
        }
    });

    function number(value) {
        return value === null || value === undefined || value === '' || !Number.isFinite(Number(value))
            ? 'No disponible' : numbers.format(Number(value));
    }

    function date(value) {
        if (!value || !/^\d{4}-\d{2}-\d{2}/.test(value)) return 'No disponible';
        const parsed = new Date(`${value.slice(0, 10)}T00:00:00Z`);
        return Number.isNaN(parsed.getTime()) ? 'No disponible' : dates.format(parsed);
    }

    function cohort(node, value) {
        const kind = {STRICT: 'strict', SENSITIVITY: 'sensitivity', QUARANTINE: 'quarantine'}[value];
        node.className = `rh-cohort${kind ? ` is-${kind}` : ''}`;
        node.textContent = cohortLabels[value] || value || 'Sin clasificar';
        node.title = value || 'Sin cohorte informada';
    }

    function element(tag, value, parent) {
        const node = document.createElement(tag);
        if (value !== undefined) node.textContent = value;
        if (parent) parent.append(node);
        return node;
    }

    function feedback(message, error = false) {
        $('feedback').textContent = message;
        $('feedback').className = message ? (error ? 'rh-error' : 'rh-success') : '';
        $('feedback').setAttribute('role', error ? 'alert' : 'status');
        if (error) $('feedback').focus();
    }

    async function api(path = '', options = {}) {
        const response = await fetch(base + path, {
            credentials: 'same-origin', ...options,
            headers: {'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content, ...options.headers}
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'No se pudo completar la consulta.');
        return result;
    }

    function facts(target, values) {
        target.replaceChildren();
        for (const [label, value, exact] of values) {
            const pair = element('div', undefined, target);
            element('dt', label, pair);
            const content = element('dd', value === null || value === undefined ? 'No disponible' : String(value), pair);
            if (exact !== null && exact !== undefined) content.title = String(exact);
        }
    }

    async function busy(button, action) {
        if (button.disabled) return;
        button.disabled = true;
        root.setAttribute('aria-busy', 'true');
        try { await action(); } catch (error) { feedback(error.message, true); }
        finally {
            button.disabled = button === $('analyze') && !state.eligible;
            root.removeAttribute('aria-busy');
        }
    }

    function svgNode(tag, attributes, content, parent) {
        const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
        for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
        if (content !== undefined) node.textContent = content;
        if (parent) parent.append(node);
        return node;
    }

    function chartGeometry(width, maximum) {
        const height = width < 480 ? 240 : 260;
        const labels = Array.from({length: 5}, (_, index) => number(maximum * index / 4));
        const left = Math.min(80, Math.max(52, Math.max(...labels.map((label) => label.length)) * 7 + 12));
        return {width, height, left, right: width - 16, top: 20, bottom: height - 42, font: width < 480 ? 11 : 12};
    }

    function chartText(svg, layout, attributes, content) {
        const label = svgNode('text', {...attributes, class: 'rh-chart-label'}, content, svg);
        label.style.fontSize = `${layout.font}px`;
        return label;
    }

    function chartAxes(svg, layout, maximum, points, future) {
        for (let step = 0; step <= 4; step++) {
            const y = layout.bottom - step * (layout.bottom - layout.top) / 4;
            svgNode('line', {x1: layout.left, x2: layout.right, y1: y, y2: y, stroke: '#e2e4df', class: 'rh-chart-grid'}, undefined, svg);
            const label = chartText(svg, layout, {x: layout.left - 10, y: y + 4, 'text-anchor': 'end'}, number(maximum * step / 4));
            svgNode('title', {}, String(maximum * step / 4), label);
        }
        const indexes = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
        for (const index of indexes) {
            const x = layout.left + index * (layout.right - layout.left) / Math.max(1, points.length - 1);
            const content = future ? `Período ${points[index].period}` : chartDates.format(new Date(`${points[index].date}T00:00:00Z`));
            const label = chartText(svg, layout, {x, y: layout.bottom + 25,
                'text-anchor': index === 0 ? 'start' : index === points.length - 1 ? 'end' : 'middle'}, content);
            svgNode('title', {}, future ? `Período ${points[index].period}` : points[index].date, label);
        }
    }

    function renderChart(target, cached) {
        const {points, title, future} = cached;
        cached.width = Math.round(target.getBoundingClientRect().width);
        target.replaceChildren();
        if (!points.length) { target.textContent = 'Sin observaciones disponibles para el gráfico.'; return; }
        const maximum = Math.max(1, ...points.map((point) => Number(point.quantity)));
        const layout = chartGeometry(Math.max(320, cached.width), maximum);
        const svg = svgNode('svg', {viewBox: `0 0 ${layout.width} ${layout.height}`, role: 'img', 'aria-label': title});
        svg.style.height = `${layout.height}px`;
        svgNode('title', {}, title, svg);
        chartAxes(svg, layout, maximum, points, future);
        const coordinates = points.map((point, index) =>
            `${layout.left + index * (layout.right - layout.left) / Math.max(1, points.length - 1)},` +
            `${layout.bottom - Number(point.quantity) / maximum * (layout.bottom - layout.top)}`);
        const color = future ? '#16a34a' : '#e07b10';
        svgNode('polygon', {points: `${layout.left},${layout.bottom} ${coordinates.join(' ')} ${points.length > 1 ? layout.right : layout.left},${layout.bottom}`,
            fill: future ? '#dcfce7' : '#fcefe2', opacity: '.65', class: 'rh-chart-area'}, undefined, svg);
        svgNode('polyline', {points: coordinates.join(' '), fill: 'none', stroke: color, 'stroke-width': 2.5,
            'stroke-linejoin': 'round', 'stroke-linecap': 'round', class: 'rh-chart-line'}, undefined, svg);
        if (points.length <= 31) {
            points.forEach((point, index) => {
                const [cx, cy] = coordinates[index].split(',');
                const dot = svgNode('circle', {cx, cy, r: 3.5, fill: color, stroke: '#fff', 'stroke-width': 1.5}, undefined, svg);
                svgNode('title', {}, `${future ? `Período ${point.period}` : point.date}: ${point.quantity}`, dot);
            });
        }
        target.append(svg);
    }

    function chart(id, points, title, future = false) {
        const target = $(id);
        const cached = {points, title, future, width: 0};
        chartCache.set(target, cached);
        chartObserver?.observe(target);
        renderChart(target, cached);
    }

    function batchPresentation(batch) {
        const summary = batch.summary || {};
        const values = [['kpi-products', summary.products ?? summary.total_products],
            ['kpi-observations', summary.total_points], ['kpi-periods', batch.calendar_days]];
        for (const [id, value] of values) {
            if (!$(id)) continue;
            $(id).textContent = number(value);
            $(id).title = value === null || value === undefined ? 'No disponible' : String(value);
        }
        if ($('kpi-coverage')) {
            $('kpi-coverage').textContent = batch.coverage_start && batch.coverage_end
                ? `${date(batch.coverage_start)} — ${date(batch.coverage_end)}` : 'No disponible';
            $('kpi-coverage').title = `${batch.coverage_start || ''} — ${batch.coverage_end || ''}`;
        }
        if ($('kpis')) $('kpis').hidden = false;
        if ($('batch-description')) $('batch-description').textContent = `Registros del ${date(batch.coverage_start)} al ${date(batch.coverage_end)}.`;
        if ($('selection-empty')) $('selection-empty').hidden = batch.status === 'preview';
    }

    function showBatch(batch, token = null) {
        state.batch = batch; state.token = token; state.product = null;
        state.eligible = false;
        ++state.searchRequest; ++state.productRequest;
        $('products').replaceChildren(); $('product-count').textContent = '';
        $('previous').disabled = true; $('next').disabled = true;
        if ($('analyze')) $('analyze').disabled = true;
        $('batch-detail').hidden = false; $('empty').hidden = true;
        $('batch-state').textContent = labels[batch.status] || batch.status;
        $('batch-state').className = `rh-badge is-${batch.status}`;
        if ($('trace-status')) $('trace-status').textContent = batch.provenance?.missing_originals?.length
            ? 'Evidencia parcial · ver detalle' : 'Fuente, cobertura y archivos';
        batchPresentation(batch);
        facts($('provenance'), [['Fuente', 'Histórico real 2026'], ['Cobertura confirmable', `${batch.coverage_start} a ${batch.coverage_end}`],
            ['Calendario', `${batch.calendar_days} períodos operativos`], ['Fecha de carga (UTC)', batch.created_at],
            ['Fecha de confirmación (UTC)', batch.confirmed_at], ['Identificador del lote', batch.id]]);
        const summaryLabels = {products: 'Productos', total_products: 'Productos', total_points: 'Observaciones diarias',
            total_quantity: 'Cantidad observada total', cohorts: 'Cohortes', cohort_counts: 'Productos por cohorte',
            calendar_days: 'Períodos operativos', included_products: 'Productos con serie', excluded_products: 'Productos excluidos',
            services: 'Servicios', service_products: 'Servicios', positive_points: 'Observaciones positivas', zero_points: 'Ceros observados'};
        facts($('summary'), Object.entries(batch.summary || {}).filter(([key]) => key in summaryLabels).map(([key, value]) => [summaryLabels[key],
            typeof value === 'object' ? JSON.stringify(value) : value]));
        const provenance = batch.provenance || {};
        const provided = provenance.provided_originals || [];
        $('source-warning').textContent = `${provenance.reference_match ? 'Paquete verificado contra la preparación del Tomo II.' : 'Corrección posterior: no equivale al paquete original del Tomo II.'} ` +
            `Originales aportados y comprobados: ${provided.length ? provided.join(', ') : 'ninguno'}. ` +
            `${provenance.raw_traceability_note || ''} ` +
            `${provenance.receipt_manifest_verified ? 'Manifiesto SHA-256 de recepción comprobado. ' : ''}` +
            `${provenance.missing_originals?.length ? `Evidencia original pendiente: ${provenance.missing_originals.join(', ')}.` : ''}`;
        $('hashes').replaceChildren();
        for (const [name, hash] of Object.entries(batch.source_hashes || {})) element('li', `${name}: ${hash}`, $('hashes'));
        if ($('confirm')) $('confirm').hidden = batch.status !== 'preview' || !token;
        if ($('revert-panel')) $('revert-panel').hidden = batch.status !== 'confirmed';
        $('catalog').hidden = batch.status === 'preview';
        $('product-detail').hidden = true; $('analysis').hidden = true;
        const params = new URLSearchParams({batch: batch.id});
        window.history.replaceState(null, '', `${window.location.pathname}?${params}`);
    }

    async function batches(selected = null) {
        const result = await api();
        const rows = Array.isArray(result) ? result : result.items;
        $('batches').replaceChildren();
        element('option', 'Selecciona una versión', $('batches')).value = '';
        if ($('parent')) {
            $('parent').replaceChildren();
            element('option', 'Primera versión del histórico', $('parent')).value = '';
        }
        for (const batch of rows) {
            const title = `${labels[batch.status]} · corte ${batch.coverage_end} · ${batch.id.slice(0, 8)}`;
            element('option', title, $('batches')).value = batch.id;
            if ($('parent') && batch.status === 'confirmed') element('option', `Corregir versión ${batch.id.slice(0, 8)}`, $('parent')).value = batch.id;
        }
        $('empty').hidden = rows.length > 0;
        const chosen = selected || rows.find((row) => row.status === 'confirmed')?.id;
        if (chosen && rows.some((row) => row.id === chosen)) {
            $('batches').value = chosen;
            return rows.find((row) => row.id === chosen);
        }
        return null;
    }

    function productRow(product) {
        const row = element('tr', undefined, $('products'));
        row.dataset.productId = String(product.id);
        row.classList.toggle('is-selected', product.id === state.product?.id);
        element('td', product.code, row).className = 'rh-code';
        element('td', product.name, row).className = 'rh-product-name';
        element('td', product.unit, row);
        cohort(element('span', undefined, element('td', undefined, row)), product.cohort);
        const button = element('button', product.included ? 'Ver historial' : 'Ver exclusión', element('td', undefined, row));
        button.type = 'button';
        button.setAttribute('aria-label', `Consultar ${product.code} ${product.name}`);
        button.addEventListener('click', () => busy(button, () => history(product.id)));
    }

    async function products(page = 1) {
        state.page = page;
        const requestId = ++state.searchRequest;
        const batchId = state.batch.id;
        const params = new URLSearchParams({search: $('search').value, page});
        const result = await api(`/${batchId}/products?${params}`);
        if (requestId !== state.searchRequest || batchId !== state.batch.id) return;
        state.pages = result.pages;
        $('products').replaceChildren();
        for (const product of result.items) productRow(product);
        if (!result.items.length) {
            const cell = element('td', 'No hay productos que coincidan con esta búsqueda.', element('tr', undefined, $('products')));
            cell.colSpan = 5; cell.className = 'rh-table-empty';
        }
        $('product-count').textContent = `${number(result.total)} ${result.total === 1 ? 'producto' : 'productos'} · ${number(result.items.length)} en esta página`;
        $('pagination').textContent = `${number(result.page)} de ${number(Math.max(1, result.pages))}`;
        $('pagination').setAttribute('aria-label', `Página ${result.page} de ${Math.max(1, result.pages)}`);
        $('previous').disabled = result.page <= 1;
        $('next').disabled = result.page >= result.pages;
    }

    function showHistory(result) {
        const product = result.product;
        state.product = product;
        $('product-detail').hidden = false; $('analysis').hidden = true;
        if ($('selection-empty')) $('selection-empty').hidden = true;
        for (const row of $('products').children) row.classList.toggle('is-selected', row.dataset.productId === String(product.id));
        $('product-title').textContent = `${product.code} · ${product.name}`;
        $('product-metadata').textContent = `Unidad original: ${product.unit || 'No disponible'} · Histórico real 2026`;
        $('product-metadata').title = `Cohorte: ${product.cohort} · Estado de la unidad: ${product.unit_status}`;
        if ($('product-cohort')) cohort($('product-cohort'), product.cohort);
        $('coverage').textContent = `Del ${date(result.coverage_start)} al ${date(result.coverage_end)}. ` +
            `Último registro: ${result.summary.last_date ? date(result.summary.last_date) : 'sin serie habilitada'}.`;
        facts($('history-summary'), [['Total observado', number(result.summary.total), result.summary.total],
            ['Períodos observados', number(result.summary.count), result.summary.count],
            ['Con demanda', number(result.summary.positive_count), result.summary.positive_count],
            ['Sin demanda', number(result.summary.zero_count), result.summary.zero_count]]);
        historyAvailability(result);
        chart('history-chart', result.points, `Demanda observada de ${product.code}, ${result.coverage_start} a ${result.coverage_end}`);
        $('observations').replaceChildren();
        for (const point of result.points) {
            const row = element('tr', undefined, $('observations'));
            element('td', point.date, row); element('td', point.quantity, row);
        }
        state.eligible = result.sufficiency.eligible && !result.batch.archived;
        if ($('analyze')) $('analyze').disabled = !state.eligible;
        window.history.replaceState(null, '', `${window.location.pathname}?${new URLSearchParams({batch: state.batch.id, product: product.id})}`);
    }

    function historyAvailability(result) {
        const available = result.sufficiency.eligible && !result.batch.archived;
        const target = $('sufficiency');
        target.className = `rh-availability ${available ? 'is-ready' : 'is-unavailable'}`;
        if (result.batch.archived) target.textContent = 'Versión archivada · disponible solo para consulta.';
        else target.textContent = result.sufficiency.eligible ? 'Historial disponible para generar un pronóstico.' :
            `Pronóstico no disponible: ${result.sufficiency.reason || result.product.reason || 'historial insuficiente'}`;
        if (result.product.cohort === 'SENSITIVITY') target.textContent += ' Sensibilidad: cantidades en su unidad original.';
        target.title = `Mínimo: ${result.sufficiency.min_observations ?? 60} observaciones y ${result.sufficiency.min_positive ?? 12} con demanda positiva. Se conservan las unidades originales.`;
    }

    async function history(productId) {
        const requestId = ++state.productRequest;
        const batchId = state.batch.id;
        const result = await api(`/${batchId}/products/${productId}`);
        if (requestId === state.productRequest && batchId === state.batch.id) showHistory(result);
    }

    function showAnalysis(result) {
        const forecast = result.forecast;
        $('analysis').hidden = false;
        const available = forecast.status === 'available';
        $('forecast-status').textContent = available ? 'Pronóstico generado desde el corte histórico.' : forecast.reason;
        $('forecast-status').className = `rh-availability ${available ? 'is-ready' : 'is-unavailable'}`;
        if ($('forecast-summary')) {
            $('forecast-summary').hidden = !available;
            if (available) facts($('forecast-summary'), [['Demanda estimada', number(forecast.total), forecast.total],
                ['Horizonte operativo', `${number(forecast.horizon)} períodos`, forecast.horizon],
                ['Método', forecast.method_label], ['Corte histórico', date(forecast.cutoff || result.coverage_end)]]);
        }
        const span = forecast.future_calendar;
        const calendar = span ? ` (${span.start} a ${span.end}, ${span.calendar_days} días calendario) · ${span.rule}` : '';
        $('forecast-metadata').textContent = `Fuente: Histórico real 2026 · Corte: ${forecast.cutoff || result.coverage_end} · ${forecast.method_label} · ` +
            `Horizonte: ${forecast.horizon} períodos operativos${calendar} · Cálculo (UTC): ${result.analysis_generated_at}.`;
        chart('forecast-chart', forecast.points, `Pronóstico desde el corte de ${forecast.cutoff}`, true);
        $('forecast-points').replaceChildren();
        for (const point of forecast.points) {
            const row = element('tr', undefined, $('forecast-points'));
            element('td', point.period, row);
            element('td', date(point.date), row);
            const quantity = element('td', point.quantity, row);
            quantity.title = String(point.quantity);
        }
    }

    $('upload')?.addEventListener('submit', (event) => {
        event.preventDefault();
        busy($('preview-button'), async () => {
            feedback('Validando integridad, calendario, unidades y cobertura del paquete…');
            const result = await api('/preview', {method: 'POST', body: new FormData($('upload'))});
            await batches(result.batch.id); showBatch(result.batch, result.confirmation_token);
            if (result.batch.status !== 'preview') await products();
            feedback(result.created ? 'Vista previa preparada. Revisa la procedencia y confirma para habilitar las consultas.' : 'El paquete ya existe. La demanda no se duplicó.');
        });
    });
    $('parent')?.addEventListener('change', () => { $('reason').required = Boolean($('parent').value); });
    $('confirm')?.addEventListener('click', () => busy($('confirm'), async () => {
        const result = await api(`/${state.batch.id}/confirm`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({token: state.token})});
        await batches(result.batch.id); showBatch(result.batch); await products();
        feedback('Histórico confirmado. Ya puedes seleccionar un producto. El inventario operativo permanece intacto.');
    }));
    $('batches').addEventListener('change', async () => {
        if (!$('batches').value) return;
        const requestId = ++state.batchRequest;
        $('catalog').hidden = true; $('product-detail').hidden = true;
        if ($('selection-empty')) $('selection-empty').hidden = false;
        ++state.searchRequest; ++state.productRequest;
        try {
            const batch = await api(`/${$('batches').value}`);
            if (requestId !== state.batchRequest) return;
            showBatch(batch);
            if (state.batch.status !== 'preview') await products();
        }
        catch (error) { feedback(error.message, true); }
    });
    $('revert-form')?.addEventListener('submit', (event) => {
        event.preventDefault();
        busy($('revert'), async () => {
            const result = await api(`/${state.batch.id}/revert`, {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({reason: $('revert-reason').value})});
            await batches(result.batch.id); showBatch(result.batch); await products();
            feedback('Reversión registrada. Se conservaron las versiones y el inventario operativo.');
        });
    });
    $('search-form').addEventListener('submit', (event) => { event.preventDefault(); products().catch((error) => feedback(error.message, true)); });
    $('previous').addEventListener('click', () => products(state.page - 1).catch((error) => feedback(error.message, true)));
    $('next').addEventListener('click', () => products(state.page + 1).catch((error) => feedback(error.message, true)));
    $('analysis-form')?.addEventListener('submit', (event) => {
        event.preventDefault();
        busy($('analyze'), async () => {
            feedback('Calculando con la serie histórica confirmada…');
            const batchId = state.batch.id;
            const productId = state.product.id;
            const params = new URLSearchParams({model: $('model').value, horizon: $('horizon').value});
            const result = await api(`/${batchId}/products/${productId}/analysis?${params}`);
            if (batchId !== state.batch.id || productId !== state.product?.id) return;
            showAnalysis(result);
            feedback('Consulta histórica completada.');
        });
    });
    async function initialize() {
        const params = new URLSearchParams(window.location.search);
        const selected = await batches(params.get('batch'));
        if (!selected) return;
        showBatch(selected);
        if (selected.status !== 'preview') {
            await products();
            if (/^[1-9]\d*$/.test(params.get('product') || '')) await history(Number(params.get('product')));
        }
    }
    initialize().catch((error) => feedback(error.message, true));
})();
