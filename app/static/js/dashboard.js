/* Pantalla Inicio: dibuja KPIs, gráficos Chart.js, listas y tabla.

   Modo normal: una sola petición GET /api/dashboard/summary (solo lectura,
   cookies de sesión). Cada bloque trae `available`; inventario y ventas (Sales
   Core) llegan con datos reales y cero ventas se dibuja como $ 0.00, nunca como
   módulo ausente. Modo de referencia visual (debug + ?ref=1):
   window.INICIO_REFERENCE trae el modelo ya formateado y no se llama a la API.

   Tres estados distintos, nunca uno solo:
   - vacío real:      "No hay movimientos recientes." / "No hay ventas registradas en este período."
   - no soportado:    "No disponible." (bloque con available: false)
   - error de carga:  "No se pudo cargar." (401 -> enlace a /login; 403 -> sin permisos). */

(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);
    const REF = window.INICIO_REFERENCE || null;

    const COLORS = { blue: "#e07b10", blueLight: "#f1cfac" };
    /* Fixture: orden fijo de la referencia. */
    const DONUT = ["#e07b10", "#3fae62", "#c98c00", "#8e73e4", "#20c2c5", "#626a65", "#191c1a", "#c98c00"];
    /* Live: paleta persistida de Category.color (misma que ix_category_layers). */
    const TONES = { blue: "#2f7fc1", green: "#20aa58", orange: "#c98c00", purple: "#8658d4", teal: "#13b9b1",
        gray: "#626a65", red: "#ee4a4e", pink: "#e93d94", yellow: "#c98c00", brown: "#7a5600", navy: "#26406b" };
    const AXIS_FONT = { family: "Inter, 'Segoe UI', sans-serif", size: 10.5 };
    const AXIS_COLOR = "#626a65";
    const GRID = { color: "#f7f8f5" };

    const UNAVAILABLE = "No disponible.";
    const LOAD_ERROR = "No se pudo cargar.";
    const NO_SALES = "No hay ventas registradas en este período.";

    // ------------------------------------------------------------------
    // Formateadores
    // ------------------------------------------------------------------
    const intFormat = new Intl.NumberFormat("es-VE");
    const fmtInt = (v) => intFormat.format(Number(v || 0));
    /* Mismo formato que el backend (_money): "$ 18,450.00". */
    const moneyFormat = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const fmtMoney = (v) => `$ ${moneyFormat.format(Number(v || 0))}`;
    const MONTHS_ABBR = ["ene.", "feb.", "mar.", "abr.", "may.", "jun.", "jul.", "ago.", "sept.", "oct.", "nov.", "dic."];
    /* "2026-05-25" -> "25 may." (misma etiqueta que la referencia). */
    const fmtDay = (iso) => `${iso.slice(8, 10)} ${MONTHS_ABBR[Number(iso.slice(5, 7)) - 1]}`;
    const TZ = "America/Caracas";

    /* Máximo y paso "redondos" del eje Y (mismo criterio que dashboard_controller._scale). */
    function niceScale(values, steps = 5) {
        const top = Math.max(0, ...values.map(Number));
        if (top <= 0) return { yMax: steps, yStep: 1 };
        const magnitude = 10 ** Math.floor(Math.log10(top / steps));
        for (const factor of [1, 2, 2.5, 5, 10]) {
            const step = factor * magnitude;
            if (step * steps >= top) return { yMax: step * steps, yStep: step };
        }
        return { yMax: top, yStep: top / steps };
    }

    /* ISO datetime (UTC naive del backend) -> "Hoy, 11:42 a. m." / "DD/MM, 11:42 a. m." en hora comercial. */
    function fmtWhen(value, today) {
        if (!value) return "Sin fecha";
        const iso = String(value);
        const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
        if (isNaN(date.getTime())) return "Sin fecha";
        const day = date.toLocaleDateString("en-CA", { timeZone: TZ });
        const time = date.toLocaleTimeString("es-VE", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
        if (day === today) return `Hoy, ${time}`;
        const [y, m, d] = day.split("-");
        return `${d}/${m}/${y}, ${time}`;
    }

    // ------------------------------------------------------------------
    // API y mensajes
    // ------------------------------------------------------------------
    class ApiError extends Error {
        constructor(status, message) { super(message); this.status = status; }
    }

    async function apiFetch(path) {
        let response;
        try {
            response = await fetch(path, { credentials: "same-origin" });
        } catch (error) {
            throw new ApiError(0, "Error de conexión con el servidor.");
        }
        let data = {};
        try { data = await response.json(); } catch (error) { /* sin cuerpo JSON */ }
        if (!response.ok) throw new ApiError(response.status, data.error || `Error HTTP ${response.status}.`);
        return data;
    }

    // Los mensajes del servidor se pintan como texto; solo el enlace fijo al login usa HTML.
    function showGlobalAlert(kind, message, trustedHtml = false) {
        const box = $("global-alert");
        box.className = `alert alert-${kind}`;
        if (trustedHtml) box.innerHTML = message;
        else box.textContent = message;
    }

    function reportError(error) {
        if (error.status === 401) {
            showGlobalAlert("warning", 'Debe iniciar sesión para ver Inicio. <a href="/login" class="alert-link">Ir al login</a>', true);
        } else if (error.status === 403) {
            showGlobalAlert("danger", "No tiene permisos para ver Inicio.");
        } else {
            showGlobalAlert("danger", error.message || LOAD_ERROR);
        }
    }

    // ------------------------------------------------------------------
    // DOM y gráficos
    // ------------------------------------------------------------------
    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    /* Iconos rellenos que la webfont de Tabler no incluye; se dibujan inline. */
    const SVG_ICONS = {
        cube: '<path d="M12 2.4 3.2 6.8v10.4L12 21.6l8.8-4.4V6.8z"/>'
            + '<path d="M3.2 6.8 12 11.2l8.8-4.4M12 11.2v10.4" fill="none" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/>',
        users: '<circle cx="9" cy="8" r="3.6"/><path d="M2.2 19.4c0-3.5 3-6.3 6.8-6.3s6.8 2.8 6.8 6.3v.6H2.2z"/>'
            + '<circle cx="17" cy="9" r="2.9"/><path d="M16.9 20c0-2.5-.9-4.7-2.5-6.3.8-.4 1.7-.6 2.6-.6 2.8 0 5 2.1 5 4.7V20z"/>',
        "alert-triangle": '<path d="M10.3 3.6 2.2 17.9A2 2 0 0 0 3.9 21h16.2a2 2 0 0 0 1.7-3.1L13.7 3.6a2 2 0 0 0-3.4 0z"/>'
            + '<path d="M12 9v5" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="17.2" r="1.1" fill="#fff"/>',
        cart: '<circle cx="9" cy="20" r="1.8"/><circle cx="17.5" cy="20" r="1.8"/>'
            + '<path d="M2 3h2.6l2.9 11.4a1.6 1.6 0 0 0 1.6 1.2h9.2a1.6 1.6 0 0 0 1.5-1.1L22 7H6.2" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>'
            + '<path d="M6.4 7.3h15l-1.8 7H8.2z"/>',
        clipboard: '<rect x="4.5" y="4" width="15" height="18" rx="2.2"/><rect x="8.5" y="2.2" width="7" height="4" rx="1.2" stroke="#fff" stroke-width="1.2"/>'
            + '<path d="M8.3 12h7.4M8.3 16h5" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/>',
        "chart-bar": '<rect x="3" y="12.5" width="4.8" height="8.5" rx="1.2"/><rect x="9.6" y="7.5" width="4.8" height="13.5" rx="1.2"/><rect x="16.2" y="3" width="4.8" height="18" rx="1.2"/>',
        screw: '<path d="M12 1.8 15 5l-3 3-3-3z"/><path d="M9.8 8.2h4.4v8.4L12 22.2l-2.2-5.6z"/>'
            + '<path d="M9.8 11.2h4.4M9.8 14h4.4" fill="none" stroke="#fff" stroke-width="1"/>',
        "x-circle": '<circle cx="12" cy="12" r="10"/><path d="m8.6 8.6 6.8 6.8M15.4 8.6l-6.8 6.8" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/>',
    };

    function svgIcon(key) {
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("viewBox", "0 0 24 24");
        svg.setAttribute("fill", "currentColor");
        svg.setAttribute("class", "ix-svg");
        svg.innerHTML = SVG_ICONS[key];
        return svg;
    }

    function icon(name, className) {
        const wrap = el("span", className);
        wrap.setAttribute("aria-hidden", "true");
        wrap.appendChild(name.startsWith("svg:") ? svgIcon(name.slice(4)) : el("i", `ti ${name}`));
        return wrap;
    }

    /* Un solo slot: imagen real o icono de respaldo. El icono no convive con la foto. */
    function thumb(src, fallbackIcon, className) {
        const name = fallbackIcon || "ti-package";
        const cls = className || "ix-thumb";
        if (!src) return icon(name, cls);
        const wrap = el("span", cls);
        wrap.setAttribute("aria-hidden", "true");
        const img = el("img");
        img.alt = "";
        img.loading = "lazy";
        img.addEventListener("error", () => {
            wrap.replaceChildren();
            wrap.appendChild(name.startsWith("svg:") ? svgIcon(name.slice(4)) : el("i", `ti ${name}`));
        });
        wrap.appendChild(img);
        img.src = src;
        return wrap;
    }

    const charts = {};

    function destroyChart(canvasId) {
        if (charts[canvasId]) { charts[canvasId].destroy(); delete charts[canvasId]; }
    }

    /* Oculta el cuerpo del gráfico (y hermanos marcados) y muestra un mensaje. */
    function showCardMessage(canvasId, message, isError) {
        destroyChart(canvasId);
        const body = $(canvasId).closest(".ix-chart, .ix-category-body");
        body.classList.add("d-none");
        body.parentElement.querySelectorAll(".ix-chart-legend").forEach((n) => n.classList.add("d-none"));
        const msg = $(canvasId + "-msg");
        msg.textContent = message;
        msg.className = `ix-msg${isError ? " is-error" : ""}`;
    }

    function renderChart(canvasId, config) {
        destroyChart(canvasId);
        const body = $(canvasId).closest(".ix-chart, .ix-category-body");
        body.classList.remove("d-none");
        body.parentElement.querySelectorAll(".ix-chart-legend").forEach((n) => n.classList.remove("d-none"));
        $(canvasId + "-msg").classList.add("d-none");
        charts[canvasId] = new Chart($(canvasId), config);
        return charts[canvasId];
    }

    // ------------------------------------------------------------------
    // KPIs
    // ------------------------------------------------------------------
    function kpiCard(k) {
        const card = el("div", "ix-card ix-kpi");
        card.dataset.kpi = k.key || "";
        card.appendChild(icon(k.icon, `ix-kpi-icon tone-${k.tone}${k.solid ? " is-solid" : ""}`));
        const body = el("div", "ix-kpi-body");
        body.appendChild(el("div", "ix-kpi-label", k.label));
        const line = el("div", "ix-kpi-line");
        line.appendChild(el("span", "ix-kpi-value", k.value));
        if (k.trend) {
            const trend = el("span", `ix-trend${k.trendTone ? ` is-${k.trendTone}` : ""}`);
            if (!k.noArrow) trend.appendChild(el("i", `ti ti-arrow-${k.trendDown ? "down" : "up"}`));
            trend.appendChild(el("span", null, k.trend));
            line.appendChild(trend);
        }
        body.appendChild(line);
        body.appendChild(el("div", "ix-kpi-sub", k.sub));
        card.appendChild(body);
        card.title = `${k.label}: ${k.value} · ${k.sub}`;
        return card;
    }

    function renderKpis(list) {
        $("kpi-row").replaceChildren(...list.map(kpiCard));
        $("kpi-row").setAttribute("aria-busy", "false");
    }

    const KPI_META = {
        sales_today: { icon: "ti-currency-dollar", tone: "blue", solid: true, label: "Ventas de hoy", fmt: fmtMoney, upTone: "green", downTone: "red" },
        products_in_stock: { icon: "svg:cube", tone: "green", label: "Productos en stock", fmt: fmtInt, upTone: "green", downTone: "red", deltaText: true },
        customers_served: { icon: "svg:users", tone: "purple", label: "Clientes atendidos", fmt: fmtInt, upTone: "green", downTone: "red" },
        active_alerts: { icon: "svg:alert-triangle", tone: "orange", label: "Alertas activas", fmt: fmtInt, upTone: "red", downTone: "green", deltaText: true },
    };

    /* Inventario: delta firmado ("+2 vs. ayer", "-3 vs. ayer", "Sin cambios vs. ayer");
       sin cierre no hay variación. Ventas: ayer 0 y hoy > 0 -> "Nuevo" (sin porcentaje infinito); 0 y 0 -> sin tendencia. */
    function liveKpi(key, kpi) {
        const meta = KPI_META[key];
        const card = { key, icon: meta.icon, tone: meta.tone, solid: meta.solid, label: meta.label, value: "—", sub: UNAVAILABLE.replace(/\.$/, "") };
        if (!kpi || !kpi.available) return card;
        card.value = meta.fmt(kpi.value);
        const c = kpi.comparison;
        if (!c || !c.available || c.delta === null) { card.sub = "Sin comparación disponible"; return card; }
        const delta = Number(c.delta);
        if (c.reason === "new") {
            card.sub = `vs. ayer ${meta.fmt(c.value)}`;
            Object.assign(card, { trend: "Nuevo", trendTone: "green", noArrow: true });
        } else if (meta.deltaText) {
            if (delta === 0) { card.sub = "Sin cambios vs. ayer"; return card; }
            card.sub = "vs. ayer";
            Object.assign(card, { trend: `${delta > 0 ? "+" : "-"}${meta.fmt(Math.abs(delta))}`, trendDown: delta < 0, trendTone: delta > 0 ? meta.upTone : meta.downTone });
        } else if (delta !== 0) {
            card.sub = `vs. ayer ${meta.fmt(c.value)}`;
            card.trend = c.change_percent === null ? meta.fmt(Math.abs(delta)) : `${Math.abs(c.change_percent).toFixed(1)}%`;
            Object.assign(card, { trendDown: delta < 0, trendTone: delta > 0 ? meta.upTone : meta.downTone });
        } else {
            card.sub = `vs. ayer ${meta.fmt(c.value)}`;
        }
        return card;
    }

    function errorKpis() {
        return Object.keys(KPI_META).map((key) => ({ ...liveKpi(key, null), sub: LOAD_ERROR }));
    }

    // ------------------------------------------------------------------
    // Gráfico de líneas (ventas)
    // ------------------------------------------------------------------
    function renderSalesLegend(series) {
        const legend = $("chart-sales-legend");
        legend.replaceChildren(...series.map((s) => {
            const li = el("li");
            li.appendChild(el("span", `ix-swatch${s.dashed ? " is-dashed" : ""}`));
            li.appendChild(el("span", null, s.label));
            return li;
        }));
    }

    function placeTooltip(chart, tip) {
        const box = $("chart-sales-tip");
        box.classList.remove("is-visible");
        if (!tip) return;
        const point = chart.getDatasetMeta(0).data[tip.index];
        if (!point) return;
        box.replaceChildren(el("span", null, tip.title), el("strong", null, tip.value));
        box.classList.add("is-visible");
        box.style.left = `${Math.round(point.x - box.offsetWidth + 6)}px`;
        box.style.top = `${Math.round(point.y + 18)}px`;
    }

    function renderSales(model) {
        renderSalesLegend(model.series);
        const tipIndex = model.tooltip ? model.tooltip.index : -1;
        const datasets = model.series.map((s, i) => {
            const color = s.dashed ? COLORS.blueLight : COLORS.blue;
            return {
                label: s.label, data: s.data, borderColor: color,
                backgroundColor: s.dashed ? color : "rgba(224, 123, 16, 0.05)", fill: !s.dashed,
                borderWidth: 2.5, borderDash: s.dashed ? [5, 5] : [], tension: 0,
                pointRadius: s.data.map((_, idx) => (i === 0 && idx === tipIndex ? 5 : 4)),
                pointBorderWidth: s.data.map((_, idx) => (i === 0 && idx === tipIndex ? 4 : 0)),
                pointBorderColor: "rgba(224, 123, 16, 0.25)",
                pointBackgroundColor: color,
            };
        });
        const yTicks = { color: AXIS_COLOR, font: AXIS_FONT, padding: 8, precision: 0 };
        if (model.tickSuffix) {
            yTicks.stepSize = model.yStep;
            yTicks.callback = (v) => (v === 0 ? "0" : `${v / 1000}${model.tickSuffix}`);
        }
        renderChart("chart-sales", {
            type: "line",
            /* Recoloca la etiqueta flotante tras cada render (incluye redimensionar). */
            plugins: [{ id: "ixTip", afterRender: (chart) => placeTooltip(chart, model.tooltip) }],
            data: { labels: model.labels, datasets },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                layout: { padding: { top: 8, right: 14, left: 4 } },
                plugins: { legend: { display: false }, tooltip: { enabled: tipIndex < 0 } },
                scales: {
                    y: { beginAtZero: true, max: model.yMax, ticks: yTicks, grid: GRID, border: { display: false } },
                    x: { grid: { display: false }, border: { display: false }, ticks: { color: AXIS_COLOR, font: AXIS_FONT, padding: 10 } },
                },
            },
        });
    }

    /* Serie real de Sales Core: 7 días comerciales (hoy incluido) vs. los 7 anteriores; cero es un valor válido. */
    function liveSales(chart) {
        if (!chart || !chart.available) return showCardMessage("chart-sales", UNAVAILABLE);
        const current = chart.current.map(Number);
        const previous = chart.previous.map(Number);
        const { yMax, yStep } = niceScale(current.concat(previous));
        renderSales({
            labels: chart.days.map(fmtDay),
            series: [{ label: "Ventas ($)", data: current }, { label: "Semana anterior ($)", data: previous, dashed: true }],
            yMax, yStep, tickSuffix: yMax >= 1000 ? "K" : null, tooltip: null,
        });
    }

    // ------------------------------------------------------------------
    // Dona por categoría
    // ------------------------------------------------------------------
    function renderCategory(model) {
        const colorAt = (item, idx) => item.color || DONUT[idx % DONUT.length];
        renderChart("chart-category", {
            type: "doughnut",
            data: {
                labels: model.items.map((i) => i.label),
                datasets: [{
                    data: model.items.map((i) => i.value),
                    backgroundColor: model.items.map(colorAt),
                    borderWidth: 2, borderColor: "#ffffff",
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false, cutout: "54%",
                layout: { padding: 8 },
                plugins: { legend: { display: false } },
            },
        });
        $("chart-category-value").textContent = model.centerValue;
        $("chart-category-label").textContent = model.centerLabel;
        $("chart-category-legend").replaceChildren(...model.items.map((item, idx) => {
            const li = el("li");
            const dot = el("span", "ix-legend-dot");
            dot.style.background = colorAt(item, idx);
            li.appendChild(dot);
            const text = el("span", "ix-legend-text");
            text.appendChild(el("span", "ix-legend-name", item.label));
            text.appendChild(el("span", "ix-legend-sub", item.sub));
            li.appendChild(text);
            li.title = `${item.label}: ${item.sub}`;
            return li;
        }));
    }

    function liveCategory(dist) {
        if (!dist || !dist.available) return showCardMessage("chart-category", UNAVAILABLE);
        if (!dist.items.length) return showCardMessage("chart-category", "No hay productos en stock.");
        renderCategory({
            centerValue: fmtInt(dist.center_value),
            centerLabel: dist.center_label,
            items: dist.items.map((i) => ({
                label: i.name, value: i.value, color: TONES[i.color] || TONES.gray,
                sub: `${Math.round(i.percent)}% (${fmtInt(i.value)})`,
            })),
        });
    }

    // ------------------------------------------------------------------
    // Listas: movimientos recientes y bajo stock
    // ------------------------------------------------------------------
    function rowText(title, desc) {
        const text = el("span", "ix-row-text");
        text.appendChild(el("span", "ix-row-title", title));
        text.appendChild(el("span", "ix-row-desc", desc));
        return text;
    }

    function listMessage(containerId, message, isError) {
        $(containerId).replaceChildren(el("div", `ix-msg${isError ? " is-error" : ""}`, message));
    }

    function renderList(containerId, rows, buildRow, emptyMessage) {
        if (!rows || !rows.length) return listMessage(containerId, emptyMessage || "Sin datos");
        $(containerId).replaceChildren(...rows.map(buildRow));
    }

    function activityRow(r) {
        const row = el("div", "ix-row");
        row.title = `${r.title} — ${r.desc} — ${r.time} — ${r.amount}`;
        row.appendChild(icon(r.icon, `ix-row-icon tone-${r.tone}`));
        row.appendChild(rowText(r.title, r.desc));
        const side = el("span", "ix-row-side");
        side.appendChild(el("span", "ix-row-time", r.time));
        side.appendChild(el("span", `ix-row-amount is-${r.direction}`, r.amount));
        row.appendChild(side);
        return row;
    }

    const MOVEMENT_LOOK = {
        entrada: { icon: "svg:cube", tone: "blue" },
        salida: { icon: "ti-arrow-up-right", tone: "orange" },
        ajuste: { icon: "svg:alert-triangle", tone: "red" },
    };

    /* Solo campos persistidos: tipo, id, producto, motivo, fecha y variación de unidades.
       Una salida enlazada por SaleLine.stock_movement_id llega como "Venta #VTA-000123" con su cliente real. */
    function liveActivity(item, today) {
        const sale = Boolean(item.sale_number);
        const look = sale ? { icon: "svg:cart", tone: "green" } : MOVEMENT_LOOK[item.movement_type] || { icon: "ti-adjustments", tone: "gray" };
        const delta = Number(item.delta || 0);
        const direction = delta > 0 ? "up" : delta < 0 ? "down" : "neutral";
        const product = item.product_name || "Producto no disponible";
        return {
            icon: look.icon, tone: look.tone,
            title: sale ? item.title : `${item.title} · ${item.reference}`,
            desc: sale ? `${item.customer_name ? `Cliente: ${item.customer_name} · ` : ""}${product}` : `${product}${item.reason ? ` · ${item.reason}` : ""}`,
            time: fmtWhen(item.created_at, today),
            amount: `${delta > 0 ? "+ " : delta < 0 ? "- " : ""}${fmtInt(Math.abs(delta))} uds.`, direction,
        };
    }

    function lowStockRow(r) {
        const row = el("div", "ix-row");
        row.title = `${r.title} — ${r.desc} — ${r.badge}`;
        row.appendChild(thumb(r.thumb, r.icon, "ix-thumb"));
        row.appendChild(rowText(r.title, r.desc));
        row.appendChild(el("span", `ix-badge ${r.depleted ? "is-red" : "is-orange"}`, r.badge));
        return row;
    }

    function liveLowStock(item) {
        const depleted = item.status === "agotado";
        return {
            icon: "ti-package", thumb: item.image_url, title: item.name, depleted, badge: depleted ? "Agotado" : "Bajo stock",
            desc: `Stock actual: ${fmtInt(item.current_stock)} unidades · Mínimo: ${fmtInt(item.minimum_stock)}`,
        };
    }

    // ------------------------------------------------------------------
    // Tabla inferior: productos más vendidos
    // ------------------------------------------------------------------
    function cell(content, className) {
        const td = el("td", className);
        if (content instanceof Node) td.appendChild(content); else td.textContent = content;
        return td;
    }

    function topMessage(message, isError) {
        const tr = el("tr", "ix-empty");
        const td = cell(message, isError ? "is-error" : undefined);
        td.colSpan = 5;
        tr.appendChild(td);
        $("table-top").replaceChildren(tr);
    }

    /* Imagen real del producto actual si existe; si no, icono neutro. Nombre y dinero vienen del snapshot de SaleLine. */
    function liveTop(item) {
        return {
            icon: "ti-package", thumb: item.image_url, product: item.name, category: item.category || "Sin categoría",
            units: fmtInt(item.units), revenue: fmtMoney(item.revenue),
        };
    }

    function renderTop(rows, emptyMessage) {
        if (!rows || !rows.length) return topMessage(emptyMessage || "Sin datos");
        $("table-top").replaceChildren(...rows.map((r, idx) => {
            const tr = el("tr");
            const product = el("span", "ix-cell-product");
            product.appendChild(thumb(r.thumb, r.icon, "ix-thumb"));
            product.appendChild(el("span", "ix-cell-name", r.product));
            const productCell = cell(product);
            productCell.title = r.product;
            const categoryCell = cell(r.category);
            categoryCell.title = r.category;
            tr.append(cell(String(idx + 1), "is-rank"), productCell, categoryCell, cell(r.units, "is-center"), cell(r.revenue, "is-end"));
            return tr;
        }));
    }

    // ------------------------------------------------------------------
    // Carga
    // ------------------------------------------------------------------
    // Inicio (tablero de trabajo) y Reportes › Panorama (gráficas) comparten este script y la
    // misma API: cada pieza se dibuja solo si su contenedor existe en la página.
    const has = (id) => Boolean($(id));

    function renderReference() {
        if (has("kpi-row")) renderKpis(REF.kpis);
        if (has("chart-sales")) renderSales(REF.sales);
        if (has("chart-category")) renderCategory(REF.category);
        if (has("table-adjustments")) renderList("table-adjustments", REF.activity, activityRow);
        if (has("table-low-stock")) renderList("table-low-stock", REF.lowStock, lowStockRow);
        if (has("table-top")) renderTop(REF.top);
    }

    function renderLive(d) {
        if (has("kpi-row")) renderKpis(Object.keys(KPI_META).map((key) => liveKpi(key, d.kpis && d.kpis[key])));
        if (has("chart-sales")) liveSales(d.sales_chart);
        if (has("chart-category")) liveCategory(d.inventory_distribution);
        const movements = d.recent_movements || {};
        if (has("table-adjustments")) {
            if (movements.available) renderList("table-adjustments", (movements.items || []).map((m) => liveActivity(m, d.today)), activityRow, "No hay movimientos recientes.");
            else listMessage("table-adjustments", UNAVAILABLE);
        }
        const alerts = d.stock_alerts || {};
        if (has("table-low-stock")) {
            if (alerts.available) renderList("table-low-stock", (alerts.items || []).map(liveLowStock), lowStockRow, "No hay productos con bajo stock.");
            else listMessage("table-low-stock", UNAVAILABLE);
        }
        if (has("table-top")) {
            if (d.top_products && d.top_products.available) renderTop((d.top_products.items || []).map(liveTop), NO_SALES); else topMessage(UNAVAILABLE);
        }
    }

    function renderFailure(error) {
        reportError(error);
        if (has("kpi-row")) renderKpis(errorKpis());
        if (has("chart-sales")) showCardMessage("chart-sales", LOAD_ERROR, true);
        if (has("chart-category")) showCardMessage("chart-category", LOAD_ERROR, true);
        if (has("table-adjustments")) listMessage("table-adjustments", LOAD_ERROR, true);
        if (has("table-low-stock")) listMessage("table-low-stock", LOAD_ERROR, true);
        if (has("table-top")) topMessage(LOAD_ERROR, true);
    }

    async function loadLive() {
        try {
            renderLive(await apiFetch("/api/dashboard/summary"));
        } catch (error) {
            renderFailure(error);
        }
    }

    document.querySelectorAll("[data-svg]").forEach((node) => node.appendChild(svgIcon(node.dataset.svg)));
    if (REF) renderReference(); else loadLive();
})();
