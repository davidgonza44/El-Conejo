/* Reporte de ventas: gráficos desde la vista servida (real o de referencia), modales de exportación
   y descarga real vía /api/reports/sales/export. En modo referencia (?ref=1) nada llama a la API. */
(() => {
    'use strict';
    const config = JSON.parse(document.getElementById('rs-config').textContent);
    const COLORS = {1: '#e07b10', 2: '#25b65a', 3: '#c98c00', 4: '#9561ef', 5: '#08b1c8', 6: '#cdd1ca', 7: '#e07b10', 8: '#f1cfac'};
    const AXIS = {color: '#626a65', font: {family: 'Arial', size: 10}};
    const GRID = {color: '#f7f8f5'};
    const BORDER = {color: '#e2e4df'};
    const money = value => value >= 1000 ? `${Math.round(value / 100) / 10}K` : value ? String(value) : '0';
    const summary = config.tab === 'summary';

    function lineChart(box) {
        const chart = JSON.parse(box.dataset.rsLine);
        const size = summary ? 12 : 10;
        new Chart(box.querySelector('canvas'), {
            type: 'line',
            data: {labels: chart.labels, datasets: chart.series.map((s, index) => {
                const color = COLORS[s.tone || Math.min(index + 1, 6)];
                return {label: s.label, data: s.data, borderColor: color, backgroundColor: s.fill ? `${color}12` : color,
                    borderWidth: summary ? 2 : 1.6, pointRadius: summary ? 3 : 2.8, pointBackgroundColor: color,
                    borderDash: s.dashed ? [6, 5] : [], fill: Boolean(s.fill), tension: 0, spanGaps: false};
            })},
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                plugins: {legend: {position: 'top', align: summary ? 'start' : 'center', labels: {color: '#626a65', boxWidth: 18, boxHeight: 2, padding: summary ? 14 : 10, font: {family: 'Arial', size: summary ? 12 : 10}}},
                    tooltip: {callbacks: {label: item => `${item.dataset.label}: ${chart.money ? item.parsed.y.toLocaleString('en-US', {minimumFractionDigits: 2}) : item.parsed.y}`}}},
                scales: {
                    y: {min: 0, max: chart.y_max, ticks: {autoSkip: false, stepSize: chart.y_step, ...AXIS, font: {family: 'Arial', size: size}, callback: v => chart.money ? money(v) : v}, grid: GRID, border: BORDER},
                    x: {ticks: {autoSkip: false, maxRotation: 0, padding: 8, ...AXIS, font: {family: 'Arial', size: size}}, grid: {color: '#f7f8f5'}, border: BORDER},
                },
            },
        });
    }

    function barChart(box) {
        const bars = JSON.parse(box.dataset.rsBars);
        const split = label => {
            const cut = label.length > 13 ? Math.max(label.lastIndexOf(' ', 14), label.indexOf(' ')) : -1;
            return cut > 0 ? [label.slice(0, cut), label.slice(cut + 1)] : label;
        };
        new Chart(box.querySelector('canvas'), {
            type: 'bar',
            data: {labels: bars.labels.map(split), datasets: [{data: bars.values, backgroundColor: context => {
                const gradient = context.chart.ctx.createLinearGradient(0, 20, 0, 200);
                gradient.addColorStop(0, '#e07b10'); gradient.addColorStop(1, '#e07b10'); return gradient;
            }, borderRadius: 3, barPercentage: .75, categoryPercentage: .9}]},
            plugins: [{id: 'categoryAmounts', afterDatasetsDraw(chart) {
                const ctx = chart.ctx; ctx.save(); ctx.fillStyle = '#191c1a'; ctx.font = 'bold 11px Arial'; ctx.textAlign = 'center';
                chart.getDatasetMeta(0).data.forEach((bar, i) => ctx.fillText(bars.value_labels[i], bar.x, bar.y - 8)); ctx.restore();
            }}],
            options: {responsive: true, maintainAspectRatio: false, animation: false, plugins: {legend: {display: false}}, scales: {
                y: {min: 0, max: bars.y_max, ticks: {stepSize: bars.y_step, ...AXIS, callback: money}, grid: GRID, border: {display: false}},
                x: {ticks: {autoSkip: false, maxRotation: 0, ...AXIS}, grid: {display: false}, border: BORDER},
            }},
        });
    }

    function donutChart(box) {
        const values = JSON.parse(box.dataset.rsDonut);
        const tones = JSON.parse(box.dataset.rsTones).map(tone => COLORS[tone]);
        new Chart(box.querySelector('canvas'), {type: 'doughnut', data: {datasets: [{data: values, backgroundColor: tones, borderWidth: 1, borderColor: '#fff'}]},
            options: {responsive: true, maintainAspectRatio: false, animation: false, cutout: box.dataset.summary === '1' ? '66%' : '64%', plugins: {legend: {display: false}, tooltip: {enabled: false}}}});
    }

    if (typeof Chart !== 'undefined') {
        document.querySelectorAll('[data-rs-line]').forEach(lineChart);
        document.querySelectorAll('[data-rs-bars]').forEach(barChart);
        document.querySelectorAll('[data-rs-donut]').forEach(donutChart);
    }

    /* Vista previa (referencia) y estado */
    const dialog = document.getElementById('rs-dialog');
    const status = document.getElementById('rs-status');
    let statusTimer = null;
    function preview(message) {
        dialog.querySelector('p').textContent = message;
        dialog.showModal();
    }
    function toast(message) {
        status.textContent = message;
        status.hidden = false;
        clearTimeout(statusTimer);
        statusTimer = setTimeout(() => { status.hidden = true; }, 6000);
    }
    const DEFAULT_NOTE = 'Vista de referencia: este control no modifica datos ni consulta registros reales.';
    document.querySelectorAll('[data-rs-preview]').forEach(button => button.addEventListener('click', () => preview(button.dataset.rsPreview || DEFAULT_NOTE)));

    /* Período (vivo): despliega el formulario GET; el servidor valida y recalcula. */
    const periodToggle = document.querySelector('[data-rs-period-toggle]');
    const periodForm = document.getElementById('rs-period');
    periodToggle?.addEventListener('click', () => {
        periodForm.hidden = !periodForm.hidden;
        periodToggle.setAttribute('aria-expanded', String(!periodForm.hidden));
        if (!periodForm.hidden) periodForm.querySelector('input[name="start"]').focus();
    });
    /* El navegador impide un "Hasta" anterior a "Desde"; el servidor valida igual. */
    const startInput = periodForm?.querySelector('input[name="start"]');
    const endInput = periodForm?.querySelector('input[name="end"]');
    startInput?.addEventListener('change', () => { endInput.min = startInput.value; });
    if (startInput && endInput) endInput.min = startInput.value;

    /* Filas por página: navegación GET en vivo; simulado en referencia. */
    document.querySelectorAll('[data-rs-per-page]').forEach(select => select.addEventListener('change', () => {
        if (config.reference || !config.page_base) { preview(DEFAULT_NOTE); select.selectedIndex = 0; return; }
        const url = new URL(config.page_base + '1', window.location.origin);
        url.searchParams.set('per_page', select.value);
        window.location.assign(url);
    }));

    /* Exportación: el primer clic abre el modal; la descarga ocurre solo al confirmar. */
    let busy = false;
    document.querySelectorAll('[data-rs-export]').forEach(button => button.addEventListener('click', () => {
        document.getElementById(`rs-export-${button.dataset.rsExport}`).showModal();
    }));
    document.querySelectorAll('[data-rs-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));

    function download(blob, filename) {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url; link.download = filename; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    function referenceExport(kind) {
        if (kind === 'pdf') { window.print(); return; }
        const rows = [['VISTA DE REFERENCIA — DATOS DE EJEMPLO']];
        document.querySelectorAll('.rs-table-card tr, .rs-summary-bottom tr').forEach(tr => {
            const cells = [...tr.children].filter(c => !c.querySelector('.rs-detail') && c.textContent.trim() !== 'Acciones');
            rows.push(cells.map(c => c.textContent.trim().replace(/\s+/g, ' ')));
        });
        const csv = rows.map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(',')).join('\r\n');
        download(new Blob(['\ufeff', csv], {type: 'text/csv;charset=utf-8'}), `referencia-ventas-${config.tab}.csv`);
    }

    async function liveExport(kind, button) {
        if (busy) return;
        busy = true;
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(`${config.export_url}&format=${kind}`, {credentials: 'same-origin', headers: {Accept: kind === 'pdf' ? 'application/pdf' : 'text/csv'}});
            if (!response.ok) throw new Error(String(response.status));
            const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(response.headers.get('Content-Disposition') || '');
            download(await response.blob(), match ? decodeURIComponent(match[1]) : `reporte_ventas.${kind}`);
            button.closest('dialog').close();
        } catch (error) {
            toast('No se pudo generar el reporte.');
        } finally {
            busy = false;
            button.disabled = false;
            button.removeAttribute('aria-busy');
        }
    }

    document.querySelectorAll('[data-rs-confirm]').forEach(button => button.addEventListener('click', () => {
        const kind = button.dataset.rsConfirm;
        if (config.reference || !config.export_url) { referenceExport(kind); button.closest('dialog').close(); return; }
        liveExport(kind, button);
    }));
})();
