/* Dashboard de Reportes (shell "ix"): dibuja los gráficos del modo de referencia a partir
   de los data-* del marcado (línea de ventas con etiqueta flotante, barras por categoría,
   donas y pronóstico histórico/proyectado). Sin lógica de negocio. */
(function () {
    "use strict";

    /* Paleta compartida con Inicio e Inventario. */
    const TONES = {
        blue: "#1a73e8", green: "#3fae62", orange: "#fdb429", purple: "#8e73e4",
        teal: "#20c2c5", gray: "#a6adb8", red: "#dc2626",
    };
    const BLUE_LIGHT = "#8ab4f8";
    const BLUE_FILL = "rgba(26, 115, 232, 0.10)";
    const FONT = { family: "Inter, 'Segoe UI', sans-serif", size: 10 };
    const AXIS_COLOR = "#6b7280";
    const GRID = "#eef0f3";
    const BASE = {
        responsive: true, maintainAspectRatio: false, animation: false, events: [],
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
    };

    function parseJSON(el, attr) {
        const raw = el.getAttribute(attr);
        return raw ? JSON.parse(raw) : null;
    }

    function el(tag, text) {
        const node = document.createElement(tag);
        node.textContent = text;
        return node;
    }

    function kTick(v) {
        return v >= 1000 ? `${v / 1000}K` : String(v);
    }

    function yScale(max, step, padding) {
        return {
            beginAtZero: true, max,
            ticks: { color: AXIS_COLOR, font: FONT, padding, stepSize: step, callback: kTick, autoSkip: false },
            grid: { color: GRID }, border: { display: false },
        };
    }

    function xScale(extra) {
        return Object.assign({
            grid: { display: false }, border: { display: false },
            ticks: { color: AXIS_COLOR, font: FONT, padding: 8, maxRotation: 0, autoSkip: false },
        }, extra || {});
    }

    function lineDataset(data, dashed, fill, pointRadius) {
        const color = dashed ? BLUE_LIGHT : TONES.blue;
        return {
            data, borderColor: color, backgroundColor: fill ? BLUE_FILL : color, fill: fill ? "origin" : false,
            borderWidth: dashed ? 2 : 2.5, borderDash: dashed ? [5, 5] : [], tension: 0, spanGaps: false,
            pointRadius, pointBorderWidth: 0, pointBackgroundColor: color,
        };
    }

    function ready(box) {
        const canvas = box.querySelector("canvas");
        return canvas && typeof Chart !== "undefined" ? canvas : null;
    }

    /* Opciones base + escalas/relleno propios (+ opciones para los plugins locales). */
    function options(layoutPadding, scales, pluginOptions) {
        return Object.assign({ layout: { padding: layoutPadding }, scales }, BASE,
            { plugins: Object.assign({}, BASE.plugins, pluginOptions || {}) });
    }

    /* ---- Donas ---- */
    document.querySelectorAll(".iv-donut").forEach((box) => {
        const canvas = ready(box);
        if (!canvas) return;
        const values = parseJSON(box, "data-donut") || [];
        const tones = parseJSON(box, "data-tones") || [];
        new Chart(canvas, {
            type: "doughnut",
            data: { datasets: [{ data: values, backgroundColor: tones.map((t) => TONES[t] || TONES.gray), borderWidth: 2, borderColor: "#fff", hoverOffset: 0 }] },
            options: Object.assign({ cutout: Number(box.dataset.cutout || 60) + "%" }, BASE),
        });
    });

    /* ---- Ventas de los últimos 12 meses (etiqueta flotante bajo el último punto) ---- */
    function placeTooltip(chart, box, tip) {
        if (!box || !tip) return;
        const point = chart.getDatasetMeta(0).data[tip.index];
        if (!point) return;
        box.replaceChildren(el("span", tip.title), ...tip.lines.map(([text, bold]) => el(bold ? "strong" : "span", text)));
        box.classList.add("is-visible");
        box.style.left = `${Math.round(point.x + tip.dx)}px`;
        box.style.top = `${Math.round(point.y + tip.dy)}px`;
    }

    document.querySelectorAll("[data-line-chart]").forEach((box) => {
        const canvas = ready(box);
        const cfg = parseJSON(box, "data-line-chart");
        if (!canvas || !cfg) return;
        const tipBox = box.querySelector(".ix-chart-tip");
        const datasets = cfg.series.map((s) => lineDataset(s.data, s.dashed, s.fill, s.dashed ? 3 : 3.5));
        new Chart(canvas, {
            type: "line",
            plugins: [{ id: "rpTip", afterRender: (chart) => placeTooltip(chart, tipBox, cfg.tooltip) }],
            data: { labels: cfg.labels, datasets },
            options: options({ top: 6, right: 10, left: 2 },
                { y: yScale(cfg.y_max, cfg.y_step, 8), x: xScale({ grid: { color: GRID } }) }),
        });
    });

    /* ---- Ventas por categoría (valor sobre cada barra) ---- */
    const barValues = {
        id: "rpBarValues",
        afterDatasetsDraw(chart, _args, opts) {
            const { ctx } = chart;
            ctx.save();
            ctx.font = `700 10.5px ${FONT.family}`;
            ctx.fillStyle = "#1f2937";
            ctx.textAlign = "center";
            ctx.textBaseline = "bottom";
            chart.getDatasetMeta(0).data.forEach((bar, i) => ctx.fillText(opts.labels[i], bar.x, bar.y - 5));
            ctx.restore();
        },
    };

    document.querySelectorAll("[data-bar-chart]").forEach((box) => {
        const canvas = ready(box);
        const cfg = parseJSON(box, "data-bar-chart");
        if (!canvas || !cfg) return;
        new Chart(canvas, {
            type: "bar",
            plugins: [barValues],
            data: {
                labels: cfg.labels,
                datasets: [{ data: cfg.values, backgroundColor: TONES.blue, borderRadius: { topLeft: 3, topRight: 3 }, barPercentage: 0.57, categoryPercentage: 1 }],
            },
            options: options({ top: 18, right: 6, left: 0 }, {
                y: yScale(cfg.y_max, cfg.y_step, 6),
                x: xScale({ ticks: { color: AXIS_COLOR, font: { family: FONT.family, size: 9 }, padding: 6, maxRotation: 0, autoSkip: false } }),
            }, { rpBarValues: { labels: cfg.value_labels } }),
        });
    });

    /* ---- Pronóstico de demanda (histórico sólido + proyección discontinua) ---- */
    const splitLine = {
        id: "rpSplit",
        afterDatasetsDraw(chart, _args, opts) {
            const point = chart.getDatasetMeta(0).data[opts.index];
            if (!point) return;
            const { ctx, chartArea } = chart;
            ctx.save();
            ctx.strokeStyle = TONES.blue;
            ctx.setLineDash([4, 4]);
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(point.x, chartArea.top);
            ctx.lineTo(point.x, chartArea.bottom);
            ctx.stroke();
            ctx.restore();
        },
    };

    document.querySelectorAll("[data-forecast-chart]").forEach((box) => {
        const canvas = ready(box);
        const cfg = parseJSON(box, "data-forecast-chart");
        if (!canvas || !cfg) return;
        const history = cfg.labels.map((_, i) => (i < cfg.history.length ? cfg.history[i] : null));
        const forecast = cfg.labels.map((_, i) => (i >= cfg.split_index ? cfg.forecast[i - cfg.split_index] : null));
        const ticks = new Set(cfg.tick_labels);
        new Chart(canvas, {
            type: "line",
            plugins: [splitLine],
            data: {
                labels: cfg.labels,
                datasets: [lineDataset(history, false, true, 2.5), lineDataset(forecast, true, true, 2.5)],
            },
            options: options({ top: 2, right: 8, left: 0 }, {
                /* La referencia no rotula el 0 en este eje. */
                y: Object.assign(yScale(cfg.y_max, cfg.y_step, 6), { afterTickToLabelConversion: (s) => { s.ticks[0].label = ""; } }),
                x: xScale({
                    grid: { color: GRID },
                    ticks: { color: AXIS_COLOR, font: FONT, padding: 4, maxRotation: 0, autoSkip: false, callback: (_v, i) => (ticks.has(cfg.labels[i]) ? cfg.labels[i] : "") },
                }),
            }, { rpSplit: { index: cfg.split_index } }),
        });
    });
})();
