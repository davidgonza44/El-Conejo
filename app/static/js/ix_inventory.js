/* Módulo Inventario (shell "ix"): dibuja los gráficos del modo de referencia a
   partir de los data-* del marcado (donas por categoría/tipo y la línea de
   entradas/salidas con su etiqueta flotante). Sin lógica de negocio. */
(function () {
    "use strict";

    /* Paleta de Inicio (dashboard.js) para que los gráficos compartan colores. */
    const TONES = {
        blue: "#1a73e8", green: "#3fae62", orange: "#fdb429", purple: "#8e73e4",
        teal: "#20c2c5", gray: "#a6adb8", red: "#dc2626", pink: "#e93d94", yellow: "#fbc21b",
        brown: "#92400e", navy: "#1e3a8a",
    };
    const BLUE_LIGHT = "#8ab4f8";
    const AXIS_FONT = { family: "Inter, 'Segoe UI', sans-serif", size: 10.5 };
    const AXIS_COLOR = "#6b7280";

    function parseJSON(el, attr, fallback) {
        const raw = el.getAttribute(attr);
        return raw ? JSON.parse(raw) : fallback;
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    /* ---- Donas ---- */
    document.querySelectorAll(".iv-donut").forEach((box) => {
        const canvas = box.querySelector("canvas");
        if (!canvas || typeof Chart === "undefined") return;
        const values = parseJSON(box, "data-donut", []);
        const tones = parseJSON(box, "data-tones", []);
        new Chart(canvas, {
            type: "doughnut",
            data: {
                datasets: [{
                    data: values,
                    backgroundColor: tones.map((t) => TONES[t] || TONES.gray),
                    borderWidth: 2,
                    borderColor: "#fff",
                    hoverOffset: 0,
                }],
            },
            options: {
                cutout: Number(box.dataset.cutout || 60) + "%",
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                events: [],
            },
        });
    });

    /* ---- Línea de entradas/salidas con etiqueta flotante ---- */
    function placeTooltip(chart, box, tip) {
        if (!box || !tip) return;
        const point = chart.getDatasetMeta(0).data[tip.index];
        if (!point) return;
        box.replaceChildren(el("span", null, tip.title),
            ...tip.lines.map(([text, bold]) => el(bold ? "strong" : "span", null, text)));
        box.classList.add("is-visible");
        box.style.left = `${Math.round(point.x - box.offsetWidth - 12)}px`;
        box.style.top = `${Math.round(point.y + 26)}px`;
    }

    document.querySelectorAll("[data-line-chart]").forEach((box) => {
        const canvas = box.querySelector("canvas");
        if (!canvas || typeof Chart === "undefined") return;
        const cfg = parseJSON(box, "data-line-chart", null);
        if (!cfg) return;
        const tipBox = box.querySelector(".ix-chart-tip");
        const tipIndex = cfg.tooltip ? cfg.tooltip.index : -1;
        const datasets = cfg.series.map((s, i) => {
            const color = s.dashed ? BLUE_LIGHT : TONES.blue;
            return {
                label: s.label, data: s.data, borderColor: color, backgroundColor: color, fill: false,
                spanGaps: false, /* null = mes sin cobertura real, no cero */
                borderWidth: 2.5, borderDash: s.dashed ? [5, 5] : [], tension: 0,
                pointRadius: s.data.map((_, idx) => (i === 0 && idx === tipIndex ? 5 : 4)),
                pointBorderWidth: s.data.map((_, idx) => (i === 0 && idx === tipIndex ? 4 : 0)),
                pointBorderColor: "rgba(26, 115, 232, 0.25)",
                pointBackgroundColor: color,
            };
        });
        new Chart(canvas, {
            type: "line",
            plugins: [{ id: "ivTip", afterRender: (chart) => placeTooltip(chart, tipBox, cfg.tooltip) }],
            data: { labels: cfg.labels, datasets },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false, events: [],
                layout: { padding: { top: 6, right: 14, left: 4 } },
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                scales: {
                    y: {
                        beginAtZero: true, max: cfg.y_max,
                        ticks: {
                            color: AXIS_COLOR, font: AXIS_FONT, padding: 8, stepSize: cfg.y_step,
                            callback: (v) => (v >= 1000 ? `${v / 1000}K` : String(v)),
                        },
                        grid: { color: "#eef0f3" }, border: { display: false },
                    },
                    x: {
                        grid: { color: "#eef0f3" }, border: { display: false },
                        ticks: { color: AXIS_COLOR, font: AXIS_FONT, padding: 10 },
                    },
                },
            },
        });
    });
})();
