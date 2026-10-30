/* Notas de entrega: consume /api/delivery-notes y /api/products.
   El stock solo se mueve vía POST crear / POST cancelar (backend). */

(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);
    const CAN_CREATE = window.CAN_CREATE === true;
    const CAN_CANCEL = window.CAN_CANCEL === true;
    const TABLE_COLS = 7;

    const STATUS_LABELS = { issued: "Emitida", cancelled: "Cancelada" };
    const STATUS_BADGES = { issued: "badge-issued", cancelled: "badge-cancelled" };

    let productMap = {};
    let createLines = [];
    let currentDetailId = null;
    let currentDetailStatus = null;

    const moneyFormat = new Intl.NumberFormat("es-VE", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
    const fmtMoney = (v) => v === null || v === undefined ? "—" : moneyFormat.format(Number(v));

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
            case 409: return error.message;
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
    // Fechas
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

    function statusBadge(status) {
        const span = document.createElement("span");
        span.className = `badge ${STATUS_BADGES[status] || "bg-secondary-lt"}`;
        span.textContent = STATUS_LABELS[status] || status;
        return span;
    }

    // ------------------------------------------------------------------
    // Productos activos
    // ------------------------------------------------------------------
    async function loadProducts() {
        const data = await apiFetch("/api/products");
        productMap = {};
        (data.items || []).forEach((p) => {
            productMap[p.id] = {
                id: p.id,
                code: p.code,
                name: p.name,
                current_stock: p.current_stock,
                sale_price: p.sale_price,
                unit: p.unit || "",
            };
        });
        if (CAN_CREATE) {
            fillProductSelect();
        }
    }

    function fillProductSelect() {
        const sel = $("c-product");
        if (!sel) return;
        const options = Object.values(productMap)
            .filter((p) => p.current_stock > 0)
            .sort((a, b) => a.name.localeCompare(b.name))
            .map((p) => {
                const opt = document.createElement("option");
                opt.value = String(p.id);
                opt.textContent = `${p.code} — ${p.name} (stock: ${p.current_stock}, ${fmtMoney(p.sale_price)})`;
                return opt;
            });
        sel.replaceChildren(sel.options[0], ...options);
    }

    function updateProductHint() {
        const hint = $("c-product-hint");
        if (!hint) return;
        const p = productMap[Number($("c-product").value)];
        hint.textContent = p
            ? `Stock disponible: ${p.current_stock} ${p.unit} · Precio: ${fmtMoney(p.sale_price)}`
            : "";
    }

    // ------------------------------------------------------------------
    // KPIs
    // ------------------------------------------------------------------
    function updateKpis(notes) {
        const issued = notes.filter((n) => n.status === "issued");
        const cancelled = notes.filter((n) => n.status === "cancelled");
        const totalAmount = issued.reduce((s, n) => s + Number(n.total_amount || 0), 0);
        const avg = issued.length ? totalAmount / issued.length : 0;

        $("kpi-issued").textContent = String(issued.length);
        $("kpi-cancelled").textContent = String(cancelled.length);
        const hasAmounts = issued.length > 0 && issued.every((n) => n.total_amount !== null && n.total_amount !== undefined);
        $("kpi-total-amount").textContent = hasAmounts ? fmtMoney(totalAmount) : "—";
        $("kpi-average").textContent = hasAmounts ? fmtMoney(avg) : "—";
    }

    // ------------------------------------------------------------------
    // Listado
    // ------------------------------------------------------------------
    function stateRow(content, cols) {
        const tr = document.createElement("tr");
        tr.className = "table-state-row";
        const td = document.createElement("td");
        td.colSpan = cols;
        if (typeof content === "string") td.textContent = content;
        else td.appendChild(content);
        tr.appendChild(td);
        return tr;
    }

    function getFilters() {
        return {
            status: $("f-status").value,
            customer_name: $("f-customer").value,
            date_from: $("f-date-from").value,
            date_to: $("f-date-to").value,
        };
    }

    function renderNotes(notes) {
        const tbody = $("notes-tbody");
        updateKpis(notes);
        $("notes-count").textContent =
            notes.length === 1 ? "1 nota" : `${notes.length} notas`;

        if (!notes.length) {
            tbody.replaceChildren(stateRow("Sin notas para los filtros seleccionados.", TABLE_COLS));
            return;
        }

        const rows = notes.map((n) => {
            const tr = document.createElement("tr");

            const tdNum = document.createElement("td");
            tdNum.className = "fw-bold";
            tdNum.textContent = n.note_number || "";
            tr.appendChild(tdNum);

            const tdCust = document.createElement("td");
            tdCust.textContent = n.customer_name || "";
            tr.appendChild(tdCust);

            const tdDate = document.createElement("td");
            tdDate.textContent = fmtDateTime(n.created_at);
            tr.appendChild(tdDate);

            const tdTotal = document.createElement("td");
            tdTotal.className = "text-end cell-num";
            tdTotal.textContent = fmtMoney(n.total_amount);
            tr.appendChild(tdTotal);

            const tdStatus = document.createElement("td");
            tdStatus.appendChild(statusBadge(n.status));
            tr.appendChild(tdStatus);

            const tdUser = document.createElement("td");
            tdUser.textContent = n.created_by || "—";
            tr.appendChild(tdUser);

            const tdActions = document.createElement("td");
            tdActions.className = "text-nowrap";
            const btnView = document.createElement("button");
            btnView.type = "button";
            btnView.className = "btn btn-sm btn-outline-primary";
            btnView.title = "Ver detalle";
            btnView.setAttribute("aria-label", "Ver detalle");
            const iconView = document.createElement("i");
            iconView.className = "ti ti-eye";
            btnView.appendChild(iconView);
            btnView.addEventListener("click", () => openDetail(n.id));
            tdActions.appendChild(btnView);
            tr.appendChild(tdActions);

            return tr;
        });

        tbody.replaceChildren(...rows);
    }

    async function loadNotes() {
        const tbody = $("notes-tbody");
        const wrap = document.createElement("div");
        const spinner = document.createElement("span");
        spinner.className = "spinner-border spinner-border-sm me-2";
        wrap.appendChild(spinner);
        wrap.append("Cargando notas…");
        tbody.replaceChildren(stateRow(wrap, TABLE_COLS));
        $("notes-count").textContent = "Cargando…";

        try {
            const data = await apiFetch("/api/delivery-notes", { params: getFilters() });
            renderNotes(data.items || []);
        } catch (error) {
            console.error("Error al cargar notas:", error);
            tbody.replaceChildren(stateRow(errorMessage(error), TABLE_COLS));
            handlePageError(error);
        }
    }

    // ------------------------------------------------------------------
    // Crear nota — líneas
    // ------------------------------------------------------------------
    function lineSubtotal(line) {
        return line.quantity * line.unit_price;
    }

    function createTotal() {
        return createLines.reduce((s, l) => s + lineSubtotal(l), 0);
    }

    function renderCreateLines() {
        const tbody = $("create-lines-tbody");
        tbody.replaceChildren();
        createLines.forEach((line, index) => {
            const tr = document.createElement("tr");

            const tdCode = document.createElement("td");
            tdCode.textContent = line.code;
            tr.appendChild(tdCode);

            const tdName = document.createElement("td");
            tdName.textContent = line.name;
            tr.appendChild(tdName);

            const tdPrice = document.createElement("td");
            tdPrice.className = "text-end cell-num";
            tdPrice.textContent = fmtMoney(line.unit_price);
            tr.appendChild(tdPrice);

            const tdQty = document.createElement("td");
            tdQty.className = "text-end cell-num";
            tdQty.textContent = String(line.quantity);
            tr.appendChild(tdQty);

            const tdSub = document.createElement("td");
            tdSub.className = "text-end cell-num";
            tdSub.textContent = fmtMoney(lineSubtotal(line));
            tr.appendChild(tdSub);

            const tdAct = document.createElement("td");
            const btnRemove = document.createElement("button");
            btnRemove.type = "button";
            btnRemove.className = "btn btn-sm btn-outline-danger";
            btnRemove.title = "Quitar producto";
            const icon = document.createElement("i");
            icon.className = "ti ti-trash";
            btnRemove.appendChild(icon);
            btnRemove.addEventListener("click", () => {
                createLines.splice(index, 1);
                renderCreateLines();
            });
            tdAct.appendChild(btnRemove);
            tr.appendChild(tdAct);

            tbody.appendChild(tr);
        });
        $("create-total").textContent = fmtMoney(createTotal());
    }

    function addLine() {
        const productId = Number($("c-product").value);
        const quantity = Number($("c-quantity").value);
        const errBox = $("create-form-error");

        if (!productId) {
            errBox.textContent = "Seleccione un producto.";
            errBox.classList.remove("d-none");
            return;
        }
        if (!Number.isInteger(quantity) || quantity <= 0) {
            errBox.textContent = "La cantidad debe ser un entero mayor a cero.";
            errBox.classList.remove("d-none");
            return;
        }

        const p = productMap[productId];
        if (!p) {
            errBox.textContent = "Producto no encontrado.";
            errBox.classList.remove("d-none");
            return;
        }
        if (createLines.some((l) => l.product_id === productId)) {
            errBox.textContent = "Ese producto ya está en la nota. Quite la línea y agréguela con la cantidad total.";
            errBox.classList.remove("d-none");
            return;
        }
        const alreadyInNote = createLines
            .filter((l) => l.product_id === productId)
            .reduce((s, l) => s + l.quantity, 0);
        if (quantity + alreadyInNote > p.current_stock) {
            errBox.textContent = `Stock insuficiente: disponible ${p.current_stock}, solicitado ${quantity}.`;
            errBox.classList.remove("d-none");
            return;
        }

        errBox.classList.add("d-none");
        createLines.push({
            product_id: productId,
            code: p.code,
            name: p.name,
            quantity,
            unit_price: p.sale_price,
        });
        renderCreateLines();
        $("c-product").value = "";
        $("c-quantity").value = "1";
        updateProductHint();
    }

    function resetCreateForm() {
        $("create-form-error").classList.add("d-none");
        $("c-customer-name").value = "";
        $("c-customer-document").value = "";
        $("c-customer-phone").value = "";
        $("c-customer-address").value = "";
        $("c-product").value = "";
        $("c-quantity").value = "1";
        $("c-product-hint").textContent = "";
        createLines = [];
        renderCreateLines();
    }

    async function submitCreate(e) {
        e.preventDefault();
        const errBox = $("create-form-error");
        errBox.classList.add("d-none");

        const customerName = $("c-customer-name").value.trim();
        if (!customerName) {
            errBox.textContent = "El nombre del cliente es obligatorio.";
            errBox.classList.remove("d-none");
            return;
        }
        if (!createLines.length) {
            errBox.textContent = "Debe agregar al menos un producto.";
            errBox.classList.remove("d-none");
            return;
        }

        const payload = {
            customer_name: customerName,
            customer_document: $("c-customer-document").value.trim(),
            customer_phone: $("c-customer-phone").value.trim(),
            customer_address: $("c-customer-address").value.trim(),
            items: createLines.map((l) => ({
                product_id: l.product_id,
                quantity: l.quantity,
            })),
        };

        const saveBtn = $("create-save-btn");
        saveBtn.disabled = true;
        try {
            const data = await apiFetch("/api/delivery-notes", { method: "POST", body: payload });
            getModal("create-modal").hide();
            resetCreateForm();
            showAlert("success", data.message || `Nota ${data.delivery_note.note_number} emitida correctamente.`);
            await Promise.all([loadNotes(), loadProducts()]);
        } catch (error) {
            console.error("Error al crear nota:", error);
            errBox.textContent = errorMessage(error);
            errBox.classList.remove("d-none");
        } finally {
            saveBtn.disabled = false;
        }
    }

    // ------------------------------------------------------------------
    // Detalle y cancelación
    // ------------------------------------------------------------------
    function setDetailVisible(loading) {
        $("detail-loading").classList.toggle("d-none", !loading);
        $("detail-content").classList.toggle("d-none", loading);
        $("detail-error").classList.add("d-none");
    }

    async function openDetail(noteId) {
        currentDetailId = noteId;
        currentDetailStatus = null;
        $("detail-title").textContent = "Detalle de nota";
        $("detail-cancelled-alert").classList.add("d-none");
        $("d-cancelled-wrap").classList.add("d-none");
        const btnCancel = $("btn-cancel-note");
        if (btnCancel) btnCancel.classList.add("d-none");
        const btnPdf = $("btn-download-pdf");
        if (btnPdf) btnPdf.disabled = true;
        setDetailVisible(true);
        getModal("detail-modal").show();

        try {
            const note = await apiFetch(`/api/delivery-notes/${noteId}`);
            currentDetailStatus = note.status;

            $("detail-title").textContent = `Nota ${note.note_number}`;
            $("d-status").replaceChildren(statusBadge(note.status));
            $("d-created-at").textContent = fmtDateTime(note.created_at);
            $("d-customer-name").textContent = note.customer_name || "";
            $("d-customer-document").textContent = note.customer_document || "—";
            $("d-customer-phone").textContent = note.customer_phone || "—";
            $("d-customer-address").textContent = note.customer_address || "—";
            $("d-created-by").textContent = note.created_by || "—";
            $("d-total").textContent = fmtMoney(note.total_amount);

            if (note.status === "cancelled") {
                $("detail-cancelled-alert").classList.remove("d-none");
                $("d-cancelled-wrap").classList.remove("d-none");
                $("d-cancelled-by").textContent = note.cancelled_by || "—";
                $("d-cancelled-at").textContent = fmtDateTime(note.cancelled_at);
            }

            const itemsBody = $("detail-items-tbody");
            itemsBody.replaceChildren();
            (note.items || []).forEach((item) => {
                const tr = document.createElement("tr");
                [
                    item.product_code,
                    item.product_name,
                    String(Number(item.quantity)),
                    fmtMoney(item.unit_price),
                    fmtMoney(item.line_total),
                ].forEach((text, i) => {
                    const td = document.createElement("td");
                    if (i >= 2) td.className = "text-end cell-num";
                    td.textContent = text;
                    tr.appendChild(td);
                });
                itemsBody.appendChild(tr);
            });

            if (btnCancel && CAN_CANCEL && note.status === "issued") {
                btnCancel.classList.remove("d-none");
            }
            if (btnPdf) btnPdf.disabled = false;

            setDetailVisible(false);
        } catch (error) {
            console.error("Error al cargar detalle:", error);
            setDetailVisible(false);
            $("detail-content").classList.add("d-none");
            const err = $("detail-error");
            err.textContent = errorMessage(error);
            err.classList.remove("d-none");
        }
    }

    function setPdfButtonBusy(busy) {
        const btn = $("btn-download-pdf");
        if (!btn) return;
        btn.disabled = busy || !currentDetailId;
        const icon = document.createElement("i");
        if (busy) {
            const spinner = document.createElement("span");
            spinner.className = "spinner-border spinner-border-sm me-1";
            spinner.setAttribute("aria-hidden", "true");
            btn.replaceChildren(spinner, document.createTextNode("Generando PDF…"));
        } else {
            icon.className = "ti ti-download me-1";
            btn.replaceChildren(icon, document.createTextNode("Descargar PDF"));
        }
    }

    function filenameFromDisposition(headerValue) {
        const raw = String(headerValue || "");
        const match = /filename="?([A-Za-z0-9._-]+)"?/i.exec(raw);
        return match ? match[1] : "nota-entrega.pdf";
    }

    async function downloadPdf() {
        if (!currentDetailId) return;
        const err = $("detail-error");
        err.classList.add("d-none");
        err.textContent = "";
        setPdfButtonBusy(true);
        try {
            let response;
            try {
                response = await fetch(`/api/delivery-notes/${currentDetailId}/pdf`, {
                    method: "GET",
                    credentials: "same-origin",
                });
            } catch (error) {
                throw new ApiError(0, "Error de conexión con el servidor.");
            }

            if (!response.ok) {
                let data = {};
                try {
                    data = await response.json();
                } catch (error) {
                    // cuerpo no JSON
                }
                throw new ApiError(
                    response.status,
                    data.error || `Error HTTP ${response.status}.`,
                );
            }

            const blob = await response.blob();
            const filename = filenameFromDisposition(
                response.headers.get("Content-Disposition"),
            );
            const objectUrl = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = objectUrl;
            link.download = filename;
            link.rel = "noopener";
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
        } catch (error) {
            console.error("Error al descargar PDF:", error);
            err.textContent = errorMessage(error);
            err.classList.remove("d-none");
            if (error.status === 401) {
                showAlert("warning", "Su sesión expiró.", true);
            }
        } finally {
            setPdfButtonBusy(false);
        }
    }

    async function cancelNote() {
        if (!currentDetailId || currentDetailStatus !== "issued") return;

        const ok = window.confirm(
            "¿Cancelar esta nota de entrega?\n\n" +
            "Al cancelar esta nota se devolverá el stock de sus productos."
        );
        if (!ok) return;

        const btnCancel = $("btn-cancel-note");
        if (btnCancel) btnCancel.disabled = true;
        try {
            const data = await apiFetch(`/api/delivery-notes/${currentDetailId}/cancel`, {
                method: "POST",
                body: {},
            });
            getModal("detail-modal").hide();
            showAlert("success", data.message || "Nota cancelada correctamente.");
            await Promise.all([loadNotes(), loadProducts()]);
        } catch (error) {
            console.error("Error al cancelar nota:", error);
            $("detail-error").textContent = errorMessage(error);
            $("detail-error").classList.remove("d-none");
        } finally {
            if (btnCancel) btnCancel.disabled = false;
        }
    }

    // ------------------------------------------------------------------
    // Eventos e inicio
    // ------------------------------------------------------------------
    $("filters-form").addEventListener("submit", (e) => {
        e.preventDefault();
        hideAlert();
        loadNotes();
    });

    $("btn-clear-filters").addEventListener("click", () => {
        $("f-status").value = "";
        $("f-customer").value = "";
        $("f-date-from").value = "";
        $("f-date-to").value = "";
        hideAlert();
        loadNotes();
    });

    if (CAN_CREATE) {
        $("btn-new-note").addEventListener("click", () => {
            resetCreateForm();
            fillProductSelect();
            getModal("create-modal").show();
        });
        $("c-product").addEventListener("change", updateProductHint);
        $("btn-add-line").addEventListener("click", addLine);
        $("create-form").addEventListener("submit", submitCreate);
    }

    if (CAN_CANCEL) {
        $("btn-cancel-note").addEventListener("click", cancelNote);
    }

    $("btn-download-pdf").addEventListener("click", downloadPdf);

    async function init() {
        try {
            await loadProducts();
        } catch (error) {
            console.error("Error al cargar productos:", error);
            handlePageError(error);
        }
        await loadNotes();
        const details = new URLSearchParams(window.location.search).getAll("detail");
        if (!details.length) return;
        if (details.length !== 1 || !/^[1-9]\d{0,9}$/.test(details[0]) || Number(details[0]) > 2147483647) {
            showAlert("danger", "El identificador de la nota de entrega no es válido.");
            return;
        }
        await openDetail(Number(details[0]));
    }

    init();
})();
