/* Módulo Ventas (shell "ix"): gráficos propios del modo de referencia a partir de los
   data-* del marcado (barras de categorías del dashboard y tendencia barras+línea de
   Reportes de ventas). Donas y línea de 12 meses las dibuja ix_inventory.js. Sin lógica
   de negocio. */
(function () {
    "use strict";

    const TONES = {
        blue: "#2f7fc1", green: "#3fae62", orange: "#c98c00", purple: "#8e73e4",
        teal: "#20c2c5", gray: "#626a65", red: "#dc2626",
    };
    /* Tendencia de ventas: barras azul claro y línea azul grisácea con marcadores en cruz. */
    const TREND_BAR = "#e07b10";
    const TREND_LINE = "#626a65";
    const AXIS_FONT = { family: "Inter, 'Segoe UI', sans-serif", size: 8 };
    const AXIS_COLOR = "#626a65";
    const GRID = "#f7f8f5";

    function parseJSON(el, attr) {
        const raw = el.getAttribute(attr);
        return raw ? JSON.parse(raw) : null;
    }

    const kTick = (v) => (v >= 1000 ? `${v / 1000}K` : String(v));

    /* Etiquetas de valor sobre cada barra (plugin de Chart.js). */
    const barLabels = {
        id: "saBarLabels",
        afterDatasetsDraw(chart, _args, opts) {
            const meta = chart.getDatasetMeta(0);
            const ctx = chart.ctx;
            ctx.save();
            ctx.font = `600 ${opts.size || 7.5}px Inter, 'Segoe UI', sans-serif`;
            ctx.fillStyle = "#191c1a";
            ctx.textAlign = "center";
            ctx.textBaseline = "bottom";
            meta.data.forEach((bar, i) => {
                const label = opts.labels[i];
                if (label) ctx.fillText(label, bar.x, bar.y - 2);
            });
            ctx.restore();
        },
    };

    /* ---- Barras por categoría (dashboard) ---- */
    document.querySelectorAll("[data-mini-bars]").forEach((box) => {
        const canvas = box.querySelector("canvas");
        const cfg = parseJSON(box, "data-mini-bars");
        if (!canvas || !cfg || typeof Chart === "undefined") return;
        new Chart(canvas, {
            type: "bar",
            plugins: [barLabels],
            data: {
                labels: cfg.x_labels || cfg.values.map(() => ""),
                datasets: [{
                    data: cfg.values,
                    backgroundColor: cfg.tones.map((t) => TONES[t] || TONES.gray),
                    borderRadius: 3,
                    barPercentage: 0.55,
                    categoryPercentage: 0.9,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false, events: [],
                layout: { padding: { top: 12, right: 4 } },
                plugins: {
                    legend: { display: false }, tooltip: { enabled: false },
                    saBarLabels: { labels: cfg.value_labels, size: 7.5 },
                },
                scales: {
                    y: {
                        beginAtZero: true, max: cfg.y_max,
                        ticks: { color: AXIS_COLOR, font: AXIS_FONT, padding: 4, stepSize: cfg.y_step, callback: kTick },
                        grid: { color: GRID }, border: { display: false },
                    },
                    x: {
                        grid: { display: false }, border: { display: false },
                        ticks: { display: Boolean(cfg.x_labels), color: AXIS_COLOR, font: AXIS_FONT, padding: 4 },
                    },
                },
            },
        });
    });

    /* ---- Tendencia de ventas: barras (ventas $) + línea (número de ventas) ---- */
    document.querySelectorAll("[data-trend-chart]").forEach((box) => {
        const canvas = box.querySelector("canvas");
        const cfg = parseJSON(box, "data-trend-chart");
        if (!canvas || !cfg || typeof Chart === "undefined") return;
        new Chart(canvas, {
            data: {
                labels: cfg.labels,
                datasets: [
                    {
                        type: "line", data: cfg.line, yAxisID: "y2", order: 0,
                        borderColor: TREND_LINE, borderWidth: 1.5, tension: 0.3,
                        pointStyle: "cross", pointRadius: 3.5, pointBorderColor: TREND_LINE, pointBorderWidth: 1.5,
                    },
                    {
                        type: "bar", data: cfg.bars, yAxisID: "y", order: 1,
                        backgroundColor: TREND_BAR, borderRadius: 3, barPercentage: 0.5, categoryPercentage: 0.9,
                    },
                ],
            },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false, events: [],
                layout: { padding: { top: 6, right: 6, left: 4 } },
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                scales: {
                    y: {
                        beginAtZero: true, max: cfg.y_max,
                        ticks: { color: AXIS_COLOR, font: AXIS_FONT, padding: 6, stepSize: cfg.y_step, callback: kTick },
                        grid: { color: GRID }, border: { display: false },
                    },
                    y2: {
                        position: "right", beginAtZero: true, max: cfg.y2_max,
                        ticks: { color: AXIS_COLOR, font: AXIS_FONT, padding: 6, stepSize: cfg.y2_step },
                        grid: { drawOnChartArea: false }, border: { display: false },
                    },
                    x: {
                        grid: { display: false }, border: { display: false },
                        ticks: { color: AXIS_COLOR, font: AXIS_FONT, padding: 8 },
                    },
                },
            },
        });
    });
})();
