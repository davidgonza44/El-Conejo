/* Diagnóstico de suficiencia histórica. Solo lectura; nunca pronostica. */

(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);
    const TABLE_COLS = 11;
    const CLASS_BADGES = {
        NO_HISTORY: "badge-no-history",
        INSUFFICIENT: "badge-insufficient",
        LIMITED: "badge-limited",
        SIMPLE_READY: "badge-simple-ready",
        ADVANCED_READY: "badge-advanced-ready",
    };
    const PALETTE = {
        blue: "#206bc4",
        darkBlue: "#1d3d5f",
        blueSoft: "rgba(32, 108, 196, 0.15)",
    };

    const intFormat = new Intl.NumberFormat("es-VE");
    const qtyFormat = new Intl.NumberFormat("es-VE", {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2,
    });
    const pctFormat = new Intl.NumberFormat("es-VE", {
        style: "percent",
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
    });

    let allProducts = [];
    let historyChart = null;
    const modalInstances = {};

    class ApiError extends Error {
        constructor(status, message) {
            super(message);
            this.status = status;
        }
    }

    async function apiFetch(path) {
        let response;
        try {
            response = await fetch(path, { method: "GET", credentials: "same-origin" });
        } catch (error) {
            throw new ApiError(0, "Error de conexión con el servidor.");
        }

        let data = {};
        try {
            data = await response.json();
        } catch (error) {
            // sin cuerpo JSON
        }
        if (!response.ok) {
            throw new ApiError(response.status, data.error || `Error HTTP ${response.status}.`);
        }
        return data;
    }

    function errorMessage(error) {
        if (!(error instanceof ApiError)) return "Error inesperado.";
        switch (error.status) {
            case 0: return error.message;
            case 401: return "Su sesión expiró. Vuelva a iniciar sesión.";
            case 403: return "No tiene permisos para ver el análisis predictivo.";
            case 404: return error.message || "Producto no encontrado.";
            default: return error.message;
        }
    }

    function showAlert(kind, message, withLoginLink = false) {
        const alert = $("page-alert");
        alert.className = `alert alert-${kind}`;
        alert.textContent = message;
        if (withLoginLink) {
            alert.append(" ");
            const link = document.createElement("a");
            link.href = "/login";
            link.textContent = "Ir al login";
            alert.appendChild(link);
        }
        alert.classList.remove("d-none");
    }

    function hideAlert() {
        $("page-alert").classList.add("d-none");
    }

    function handlePageError(error) {
        if (error.status === 401) {
            showAlert("warning", "Su sesión expiró.", true);
        } else {
            showAlert("danger", errorMessage(error));
        }
    }

    function fmtInt(value) {
        if (value === null || value === undefined) return "—";
        return intFormat.format(Number(value));
    }

    function fmtQty(value) {
        if (value === null || value === undefined) return "—";
        return qtyFormat.format(Number(value));
    }

    function fmtRatio(value) {
        if (value === null || value === undefined) return "—";
        return pctFormat.format(Number(value));
    }

    function fmtDate(value) {
        if (!value) return "—";
        const parts = String(value).split("T")[0].split("-");
        if (parts.length !== 3) return String(value);
        return `${parts[2]}/${parts[1]}/${parts[0]}`;
    }

    function periodLabel(item) {
        if (!item.start_date || !item.end_date) return "—";
        return `${fmtDate(item.start_date)} — ${fmtDate(item.end_date)}`;
    }

    function getModal(id) {
        if (!modalInstances[id]) {
            modalInstances[id] = new bootstrap.Modal($(id));
        }
        return modalInstances[id];
    }

    function classBadge(item) {
        const span = document.createElement("span");
        span.className = `badge ${CLASS_BADGES[item.sufficiency_class] || "bg-secondary-lt"}`;
        span.textContent = item.sufficiency_label || item.sufficiency_class;
        return span;
    }

    function statusBadge(isActive) {
        const span = document.createElement("span");
        span.className = isActive ? "badge bg-green-lt" : "badge bg-secondary-lt";
        span.textContent = isActive ? "Activo" : "Inactivo";
        return span;
    }

    function setKpis(kpis) {
        $("kpi-active").textContent = fmtInt(kpis.active_products);
        $("kpi-with-history").textContent = fmtInt(kpis.products_with_history);
        $("kpi-no-history").textContent = fmtInt(kpis.no_history);
        $("kpi-insufficient").textContent = fmtInt(kpis.insufficient);
        $("kpi-limited").textContent = fmtInt(kpis.limited);
        $("kpi-simple").textContent = fmtInt(kpis.simple_ready);
        $("kpi-advanced").textContent = fmtInt(kpis.advanced_ready);
    }

    function emptyRow(message) {
        const tr = document.createElement("tr");
        tr.className = "table-state-row";
        const td = document.createElement("td");
        td.colSpan = TABLE_COLS;
        td.textContent = message;
        tr.appendChild(td);
        return tr;
    }

    function filteredProducts() {
        const search = ($("f-search").value || "").trim().toLowerCase();
        const klass = $("f-class").value;
        const status = $("f-status").value;
        return allProducts.filter((item) => {
            if (klass && item.sufficiency_class !== klass) return false;
            if (status === "active" && !item.is_active) return false;
            if (status === "inactive" && item.is_active) return false;
            if (!search) return true;
            const haystack = `${item.code || ""} ${item.name || ""}`.toLowerCase();
            return haystack.includes(search);
        });
    }

    function renderTable() {
        const tbody = $("products-tbody");
        tbody.replaceChildren();
        const rows = filteredProducts();
        $("products-count").textContent = `${intFormat.format(rows.length)} producto(s)`;
        if (!rows.length) {
            tbody.appendChild(emptyRow("Sin historial disponible"));
            return;
        }
        rows.forEach((item) => {
            const tr = document.createElement("tr");
            if (!item.is_active) tr.classList.add("inactive-row");

            const cells = [
                item.code || "—",
                item.name || "—",
                item.category || "—",
                periodLabel(item),
            ];
            cells.forEach((text, index) => {
                const td = document.createElement("td");
                td.textContent = text;
                tr.appendChild(td);
            });

            const numeric = [
                fmtInt(item.original_event_count),
                fmtInt(item.positive_periods),
                fmtRatio(item.zero_ratio),
                fmtQty(item.average_daily_demand),
            ];
            numeric.forEach((text) => {
                const td = document.createElement("td");
                td.className = "text-end cell-num";
                td.textContent = text;
                tr.appendChild(td);
            });

            const classTd = document.createElement("td");
            classTd.appendChild(classBadge(item));
            tr.appendChild(classTd);

            const statusTd = document.createElement("td");
            statusTd.appendChild(statusBadge(item.is_active));
            tr.appendChild(statusTd);

            const actionTd = document.createElement("td");
            const button = document.createElement("button");
            button.type = "button";
            button.className = "btn btn-outline-primary btn-sm";
            const icon = document.createElement("i");
            icon.className = "ti ti-eye me-1";
            button.append(icon, "Ver diagnóstico");
            button.addEventListener("click", () => openDetail(item.product_id));
            actionTd.appendChild(button);
            tr.appendChild(actionTd);

            tbody.appendChild(tr);
        });
    }

    function statTile(label, value) {
        const col = document.createElement("div");
        col.className = "col-6 col-md-4 col-lg-3";
        const tile = document.createElement("div");
        tile.className = "stat-tile";
        const lab = document.createElement("div");
        lab.className = "stat-label";
        lab.textContent = label;
        const val = document.createElement("div");
        val.className = "stat-value";
        val.textContent = value;
        tile.append(lab, val);
        col.appendChild(tile);
        return col;
    }

    function destroyChart() {
        if (historyChart) {
            historyChart.destroy();
            historyChart = null;
        }
    }

    function renderChart(series) {
        destroyChart();
        const wrap = $("chart-wrap");
        const empty = $("chart-empty");
        if (!series || !series.length) {
            wrap.classList.add("d-none");
            empty.classList.remove("d-none");
            return;
        }
        empty.classList.add("d-none");
        wrap.classList.remove("d-none");
        historyChart = new Chart($("history-chart"), {
            type: "bar",
            data: {
                labels: series.map((point) => fmtDate(point.date)),
                datasets: [{
                    label: "Demanda histórica observada",
                    data: series.map((point) => Number(point.demand || 0)),
                    backgroundColor: PALETTE.blueSoft,
                    borderColor: PALETTE.blue,
                    borderWidth: 1,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: true },
                    tooltip: { mode: "index", intersect: false },
                },
                scales: {
                    x: {
                        ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
                    },
                    y: { beginAtZero: true },
                },
            },
        });
    }

    function sourceLabel(value) {
        if (value === "historical") return "Histórica";
        if (value === "operational") return "Operativa";
        if (value === "combined") return "Combinada";
        return "Sin fuente";
    }

    async function openDetail(productId) {
        const modal = getModal("detail-modal");
        $("detail-error").classList.add("d-none");
        $("detail-content").classList.add("d-none");
        $("detail-loading").classList.remove("d-none");
        $("detail-insufficient").classList.add("d-none");
        modal.show();

        try {
            const data = await apiFetch(`/api/predictions/products/${productId}`);
            $("detail-title").textContent = `${data.code} — ${data.name}`;
            const insufficient = (
                data.sufficiency_class === "NO_HISTORY"
                || data.sufficiency_class === "INSUFFICIENT"
                || !data.has_historical_series
            );
            $("detail-insufficient").classList.toggle("d-none", !insufficient);

            const meta = $("detail-meta");
            meta.replaceChildren();
            [
                ["Producto", data.name || "—"],
                ["Código", data.code || "—"],
                ["Categoría", data.category || "—"],
                ["Estado", data.is_active ? "Activo" : "Inactivo"],
                ["Fuente de datos", sourceLabel(data.data_source)],
                ["Clasificación", data.sufficiency_label || "—"],
                ["Patrón descriptivo", data.demand_pattern_label || "—"],
                ["Recomendación de reabastecimiento", "No existe en esta fase"],
            ].forEach(([label, value]) => meta.appendChild(statTile(label, value)));

            const stats = $("detail-stats");
            stats.replaceChildren();
            [
                ["Inicio", fmtDate(data.start_date)],
                ["Fin", fmtDate(data.end_date)],
                ["Períodos", fmtInt(data.periods)],
                ["Períodos positivos", fmtInt(data.positive_periods)],
                ["Días en cero", fmtInt(data.zero_periods)],
                ["Proporción de ceros", fmtRatio(data.zero_ratio)],
                ["Eventos originales", fmtInt(data.original_event_count)],
                ["Demanda total", fmtQty(data.total_demand)],
                ["Promedio diario", fmtQty(data.average_daily_demand)],
                ["Mediana", fmtQty(data.median)],
                ["Desviación estándar", fmtQty(data.standard_deviation)],
                ["Máximo", fmtQty(data.max_demand)],
                ["Última demanda", fmtDate(data.last_date_with_demand)],
                ["Días desde última demanda", fmtInt(data.days_since_last_demand)],
            ].forEach(([label, value]) => stats.appendChild(statTile(label, value)));

            const reason = document.createElement("div");
            reason.className = "col-12";
            const note = document.createElement("p");
            note.className = "text-secondary mb-0";
            note.textContent = data.classification_reason || "";
            reason.appendChild(note);
            stats.appendChild(reason);

            const box = $("detail-inconsistencies");
            if (data.inconsistencies && data.inconsistencies.length) {
                box.className = "alert alert-warning mb-3";
                box.replaceChildren();
                const title = document.createElement("strong");
                title.textContent = "Inconsistencias detectadas";
                box.appendChild(title);
                const list = document.createElement("ul");
                list.className = "mb-0 mt-2";
                data.inconsistencies.forEach((item) => {
                    const li = document.createElement("li");
                    li.textContent = item.message || item.code;
                    list.appendChild(li);
                });
                box.appendChild(list);
            } else {
                box.className = "d-none mb-3";
                box.replaceChildren();
            }

            renderChart(data.daily_series || []);
            $("detail-loading").classList.add("d-none");
            $("detail-content").classList.remove("d-none");
        } catch (error) {
            $("detail-loading").classList.add("d-none");
            const alert = $("detail-error");
            alert.textContent = errorMessage(error);
            alert.classList.remove("d-none");
        }
    }

    async function loadPage() {
        hideAlert();
        try {
            const [summary, products] = await Promise.all([
                apiFetch("/api/predictions/readiness"),
                apiFetch("/api/predictions/products"),
            ]);
            setKpis(summary.kpis || {});
            $("insufficient-banner").classList.toggle(
                "d-none",
                summary.show_insufficient_banner !== true
            );
            allProducts = products.items || [];
            renderTable();
        } catch (error) {
            $("products-tbody").replaceChildren(emptyRow("No se pudo cargar el diagnóstico."));
            $("products-count").textContent = "Error";
            handlePageError(error);
        }
    }

    $("filters-form").addEventListener("submit", (event) => {
        event.preventDefault();
        renderTable();
    });
    $("btn-clear-filters").addEventListener("click", () => {
        $("f-search").value = "";
        $("f-class").value = "";
        $("f-status").value = "";
        renderTable();
    });
    $("detail-modal").addEventListener("hidden.bs.modal", destroyChart);

    loadPage();
})();
