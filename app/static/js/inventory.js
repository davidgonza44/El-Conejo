/* Página de inventario: movimientos de stock contra /api/inventory.

   Reglas:
   - fetch con credentials: "same-origin".
   - window.CAN_MOVE controla botones de entrada/salida/ajuste.
   - Filtro por fechas solo en frontend (la API no lo soporta).
   - El código del producto se resuelve desde el mapa cargado con GET /api/products. */

(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);
    const CAN_MOVE = window.CAN_MOVE === true;
    const TABLE_COLS = 9;

    const TYPE_LABELS = {
        entrada: "Entrada",
        salida: "Salida",
        ajuste: "Ajuste",
    };
    const TYPE_BADGES = {
        entrada: "badge-entrada",
        salida: "badge-salida",
        ajuste: "badge-ajuste",
    };

    let productMap = {};   // id -> { code, name, current_stock, unit }
    let allMovements = []; // cache completo desde la API

    const intFormat = new Intl.NumberFormat("es-VE");
    const fmtInt = (v) => intFormat.format(Number(v || 0));

    // ------------------------------------------------------------------
    // API
    // ------------------------------------------------------------------
    class ApiError extends Error {
        constructor(status, message) {
            super(message);
            this.status = status;
        }
    }

    async function apiFetch(path, { method = "GET", params = {}, body = null } = {}) {
        const url = new URL(path, window.location.origin);
        Object.entries(params).forEach(([key, value]) => {
            if (value !== null && value !== undefined && String(value).trim() !== "") {
                url.searchParams.set(key, String(value).trim());
            }
        });

        const options = { method, credentials: "same-origin" };
        if (body !== null) {
            options.headers = { "Content-Type": "application/json" };
            options.body = JSON.stringify(body);
        }

        let response;
        try {
            response = await fetch(url, options);
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
            case 403: return "No tiene permisos para realizar esta acción.";
            case 404: return error.message || "Recurso no encontrado.";
            case 409: return error.message; // stock insuficiente, producto inactivo
            case 500: return "Error interno del servidor. Intente de nuevo.";
            default: return error.message;
        }
    }

    // ------------------------------------------------------------------
    // Alertas
    // ------------------------------------------------------------------
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

    // ------------------------------------------------------------------
    // Formateo de fechas
    // ------------------------------------------------------------------
    function fmtDateTime(value) {
        if (!value) return "—";
        const iso = String(value);
        const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
        if (isNaN(date.getTime())) return "—";
        return date.toLocaleString("es-VE", {
            day: "2-digit", month: "2-digit", year: "numeric",
            hour: "2-digit", minute: "2-digit",
        });
    }

    /* ISO UTC naive -> 'YYYY-MM-DD' en hora local para filtro por fechas. */
    function toLocalDateKey(iso) {
        const isoStr = String(iso);
        const date = new Date(isoStr.endsWith("Z") || isoStr.includes("+") ? isoStr : isoStr + "Z");
        if (isNaN(date.getTime())) return "";
        const y = date.getFullYear();
        const m = String(date.getMonth() + 1).padStart(2, "0");
        const d = String(date.getDate()).padStart(2, "0");
        return `${y}-${m}-${d}`;
    }

    // ------------------------------------------------------------------
    // Modales
    // ------------------------------------------------------------------
    const modalInstances = {};

    function getModal(id) {
        if (!modalInstances[id]) {
            modalInstances[id] = new bootstrap.Modal($(id));
        }
        return modalInstances[id];
    }

    function showFormError(boxId, message) {
        const box = $(boxId);
        box.textContent = message;
        box.classList.remove("d-none");
    }

    function hideFormError(boxId) {
        $(boxId).classList.add("d-none");
    }

    // ------------------------------------------------------------------
    // Productos (mapa para selects y código en tabla)
    // ------------------------------------------------------------------
    function productOptions() {
        return Object.values(productMap)
            .sort((a, b) => a.name.localeCompare(b.name))
            .map((p) => {
                const opt = document.createElement("option");
                opt.value = String(p.id);
                opt.textContent = `${p.code} — ${p.name} (stock: ${fmtInt(p.current_stock)})`;
                return opt;
            });
    }

    function fillProductSelects() {
        const options = productOptions();
        ["f-product", "entry-product", "exit-product", "adj-product"].forEach((id) => {
            const sel = $(id);
            if (!sel) return;
            const keep = sel.value;
            const first = sel.options[0];
            sel.replaceChildren(first, ...options.map((o) => o.cloneNode(true)));
            sel.value = keep;
        });
    }

    async function loadProducts() {
        const data = await apiFetch("/api/products");
        productMap = {};
        (data.items || []).forEach((p) => {
            productMap[p.id] = {
                id: p.id,
                code: p.code,
                name: p.name,
                current_stock: p.current_stock,
                unit: p.unit || "",
            };
        });
        fillProductSelects();
    }

    function stockHint(productId, hintId) {
        const hint = $(hintId);
        if (!hint) return;
        const p = productMap[Number(productId)];
        hint.textContent = p
            ? `Stock actual: ${fmtInt(p.current_stock)} ${p.unit}`.trim()
            : "";
    }

    // ------------------------------------------------------------------
    // Filtros
    // ------------------------------------------------------------------
    function getApiFilters() {
        return {
            product_id: $("f-product").value,
            movement_type: $("f-type").value,
        };
    }

    function getDateFilters() {
        return {
            date_from: $("f-date-from").value,
            date_to: $("f-date-to").value,
        };
    }

    function applyDateFilters(items) {
        const { date_from, date_to } = getDateFilters();
        if (!date_from && !date_to) return items;
        return items.filter((m) => {
            const key = toLocalDateKey(m.created_at);
            if (!key) return false;
            if (date_from && key < date_from) return false;
            if (date_to && key > date_to) return false;
            return true;
        });
    }

    // ------------------------------------------------------------------
    // KPIs
    // ------------------------------------------------------------------
    function updateKpis(items) {
        $("kpi-total").textContent = fmtInt(items.length);
        $("kpi-entries").textContent = fmtInt(items.filter((m) => m.movement_type === "entrada").length);
        $("kpi-exits").textContent = fmtInt(items.filter((m) => m.movement_type === "salida").length);
        $("kpi-adjustments").textContent = fmtInt(items.filter((m) => m.movement_type === "ajuste").length);
    }

    // ------------------------------------------------------------------
    // Tabla de movimientos
    // ------------------------------------------------------------------
    function stateRow(content, cols) {
        const tr = document.createElement("tr");
        tr.className = "table-state-row";
        const td = document.createElement("td");
        td.colSpan = cols;
        if (typeof content === "string") {
            td.textContent = content;
        } else {
            td.appendChild(content);
        }
        tr.appendChild(td);
        return tr;
    }

    function typeBadge(type) {
        const span = document.createElement("span");
        span.className = `badge ${TYPE_BADGES[type] || "bg-secondary-lt"}`;
        span.textContent = TYPE_LABELS[type] || type;
        return span;
    }

    function renderMovements(items) {
        const tbody = $("movements-tbody");
        updateKpis(items);
        $("movements-count").textContent =
            items.length === 1 ? "1 movimiento" : `${fmtInt(items.length)} movimientos`;

        if (!items.length) {
            tbody.replaceChildren(stateRow("Sin movimientos para los filtros seleccionados.", TABLE_COLS));
            return;
        }

        const rows = items.map((m) => {
            const tr = document.createElement("tr");
            const prod = productMap[m.product_id] || {};

            const tdDate = document.createElement("td");
            tdDate.textContent = fmtDateTime(m.created_at);
            tr.appendChild(tdDate);

            const tdName = document.createElement("td");
            tdName.textContent = m.product || prod.name || "—";
            tr.appendChild(tdName);

            const tdCode = document.createElement("td");
            tdCode.textContent = prod.code || "—";
            tr.appendChild(tdCode);

            const tdType = document.createElement("td");
            tdType.appendChild(typeBadge(m.movement_type));
            tr.appendChild(tdType);

            const tdQty = document.createElement("td");
            tdQty.className = "text-end cell-num";
            tdQty.textContent = fmtInt(m.quantity);
            tr.appendChild(tdQty);

            const tdPrev = document.createElement("td");
            tdPrev.className = "text-end cell-num";
            tdPrev.textContent = fmtInt(m.previous_stock);
            tr.appendChild(tdPrev);

            const tdNew = document.createElement("td");
            tdNew.className = "text-end cell-num";
            tdNew.textContent = fmtInt(m.new_stock);
            tr.appendChild(tdNew);

            const tdReason = document.createElement("td");
            tdReason.textContent = m.reason || "—";
            if (m.reason) tdReason.title = m.reason;
            tr.appendChild(tdReason);

            const tdUser = document.createElement("td");
            tdUser.textContent = m.user || "—";
            tr.appendChild(tdUser);

            return tr;
        });

        tbody.replaceChildren(...rows);
    }

    async function loadMovements() {
        const tbody = $("movements-tbody");
        const wrap = document.createElement("div");
        const spinner = document.createElement("span");
        spinner.className = "spinner-border spinner-border-sm me-2";
        wrap.appendChild(spinner);
        wrap.append("Cargando movimientos…");
        tbody.replaceChildren(stateRow(wrap, TABLE_COLS));
        $("movements-count").textContent = "Cargando…";

        const filters = getApiFilters();
        try {
            const data = await apiFetch("/api/inventory/movements", { params: filters });
            allMovements = data.items || [];
            renderMovements(applyDateFilters(allMovements));
        } catch (error) {
            console.error("Error al cargar movimientos:", error);
            tbody.replaceChildren(stateRow(errorMessage(error), TABLE_COLS));
            handlePageError(error);
        }
    }

    // ------------------------------------------------------------------
    // Bajo stock
    // ------------------------------------------------------------------
    async function loadLowStock() {
        const tbody = $("low-stock-tbody");
        try {
            const data = await apiFetch("/api/inventory/low-stock");
            const items = data.items || [];
            $("kpi-low-stock").textContent = fmtInt(items.length);

            if (!items.length) {
                tbody.replaceChildren(stateRow("Ningún producto en bajo stock.", 4));
                return;
            }

            const rows = items.map((p) => {
                const tr = document.createElement("tr");
                tr.className = "low-stock-row";

                const tdCode = document.createElement("td");
                tdCode.textContent = p.code || "";
                tr.appendChild(tdCode);

                const tdName = document.createElement("td");
                tdName.textContent = p.name || "";
                tr.appendChild(tdName);

                const tdStock = document.createElement("td");
                tdStock.className = "text-end cell-num";
                tdStock.textContent = fmtInt(p.current_stock);
                tr.appendChild(tdStock);

                const tdMin = document.createElement("td");
                tdMin.className = "text-end cell-num";
                tdMin.textContent = fmtInt(p.minimum_stock);
                tr.appendChild(tdMin);

                return tr;
            });
            tbody.replaceChildren(...rows);
        } catch (error) {
            console.error("Error al cargar bajo stock:", error);
            tbody.replaceChildren(stateRow(errorMessage(error), 4));
            $("kpi-low-stock").textContent = "—";
        }
    }

    // ------------------------------------------------------------------
    // Registrar movimientos
    // ------------------------------------------------------------------
    async function submitMovement(endpoint, payload, modalId, errorBoxId, successMsg) {
        hideFormError(errorBoxId);
        try {
            const data = await apiFetch(endpoint, { method: "POST", body: payload });
            getModal(modalId).hide();
            showAlert("success", successMsg.replace("{stock}", fmtInt(data.product.current_stock)));
            // Actualizar mapa local con stock nuevo
            if (productMap[data.product.id]) {
                productMap[data.product.id].current_stock = data.product.current_stock;
                fillProductSelects();
            }
            await Promise.all([loadProducts(), loadMovements(), loadLowStock()]);
        } catch (error) {
            console.error(`Error en ${endpoint}:`, error);
            showFormError(errorBoxId, errorMessage(error));
        }
    }

    function validateProduct(productId) {
        if (!productId) return "Debe seleccionar un producto.";
        return null;
    }

    function validateQuantity(value) {
        const q = Number(value);
        if (!Number.isInteger(q) || q <= 0) return "La cantidad debe ser un entero mayor a cero.";
        return null;
    }

    if (CAN_MOVE) {
        // --- Entrada ---
        $("btn-new-entry").addEventListener("click", () => {
            hideFormError("entry-form-error");
            $("entry-product").value = "";
            $("entry-quantity").value = "1";
            $("entry-reason").value = "";
            $("entry-stock-hint").textContent = "";
            getModal("entry-modal").show();
        });

        $("entry-product").addEventListener("change", () => {
            stockHint($("entry-product").value, "entry-stock-hint");
        });

        $("entry-form").addEventListener("submit", (e) => {
            e.preventDefault();
            const productId = $("entry-product").value;
            let err = validateProduct(productId) || validateQuantity($("entry-quantity").value);
            if (err) { showFormError("entry-form-error", err); return; }
            submitMovement("/api/inventory/entry", {
                product_id: Number(productId),
                quantity: Number($("entry-quantity").value),
                reason: $("entry-reason").value.trim(),
            }, "entry-modal", "entry-form-error",
                "Entrada registrada. Stock actual: {stock}.");
        });

        // --- Salida ---
        $("btn-new-exit").addEventListener("click", () => {
            hideFormError("exit-form-error");
            $("exit-product").value = "";
            $("exit-quantity").value = "1";
            $("exit-reason").value = "";
            $("exit-stock-hint").textContent = "";
            getModal("exit-modal").show();
        });

        $("exit-product").addEventListener("change", () => {
            stockHint($("exit-product").value, "exit-stock-hint");
        });

        $("exit-form").addEventListener("submit", (e) => {
            e.preventDefault();
            const productId = $("exit-product").value;
            let err = validateProduct(productId) || validateQuantity($("exit-quantity").value);
            if (err) { showFormError("exit-form-error", err); return; }
            submitMovement("/api/inventory/exit", {
                product_id: Number(productId),
                quantity: Number($("exit-quantity").value),
                reason: $("exit-reason").value.trim(),
            }, "exit-modal", "exit-form-error",
                "Salida registrada. Stock actual: {stock}.");
        });

        // --- Ajuste ---
        $("btn-new-adjustment").addEventListener("click", () => {
            hideFormError("adjustment-form-error");
            $("adj-product").value = "";
            $("adj-new-stock").value = "0";
            $("adj-reason").value = "";
            $("adj-stock-hint").textContent = "";
            getModal("adjustment-modal").show();
        });

        $("adj-product").addEventListener("change", () => {
            const id = $("adj-product").value;
            stockHint(id, "adj-stock-hint");
            const p = productMap[Number(id)];
            if (p) $("adj-new-stock").value = String(p.current_stock);
        });

        $("adjustment-form").addEventListener("submit", (e) => {
            e.preventDefault();
            const productId = $("adj-product").value;
            const errProd = validateProduct(productId);
            if (errProd) { showFormError("adjustment-form-error", errProd); return; }

            const newStock = Number($("adj-new-stock").value);
            if (!Number.isInteger(newStock) || newStock < 0) {
                showFormError("adjustment-form-error", "El nuevo stock debe ser un entero no negativo.");
                return;
            }
            const reason = $("adj-reason").value.trim();
            if (!reason) {
                showFormError("adjustment-form-error", "El motivo es obligatorio en los ajustes.");
                return;
            }
            submitMovement("/api/inventory/adjustment", {
                product_id: Number(productId),
                new_stock: newStock,
                reason,
            }, "adjustment-modal", "adjustment-form-error",
                "Ajuste registrado. Stock actual: {stock}.");
        });
    }

    // ------------------------------------------------------------------
    // Filtros e inicio
    // ------------------------------------------------------------------
    $("filters-form").addEventListener("submit", (e) => {
        e.preventDefault();
        hideAlert();
        loadMovements();
    });

    $("btn-clear-filters").addEventListener("click", () => {
        $("f-product").value = "";
        $("f-type").value = "";
        $("f-date-from").value = "";
        $("f-date-to").value = "";
        hideAlert();
        loadMovements();
    });

    async function init() {
        try {
            await loadProducts();
        } catch (error) {
            console.error("Error al cargar productos:", error);
            handlePageError(error);
        }
        await Promise.all([loadMovements(), loadLowStock()]);
    }

    init();
})();
