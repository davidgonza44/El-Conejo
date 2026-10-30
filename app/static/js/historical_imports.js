(function () {
    "use strict";

    const config = window.HISTORICAL_IMPORT_CONFIG || {};
    const API_BASE = String(config.apiBase || "/api/historical-imports");
    const CAN_UPLOAD = config.canUpload === true;
    const CAN_REVIEW = config.canReview === true;
    const CAN_CONFIRM = config.canConfirm === true;
    const CAN_REVERT = config.canRevert === true;
    const CAN_EXPORT = config.canExport === true;
    const CSRF_TOKEN = typeof config.csrfToken === "string" ? config.csrfToken : "";
    const MAX_FILE_BYTES = 10 * 1024 * 1024;
    const PAGE_SIZE = 25;

    const CANONICAL_FIELDS = Object.freeze([
        { name: "event_date", required: true, rule: "YYYY-MM-DD · solo 2025" },
        { name: "product_code", required: true, rule: "Código histórico" },
        { name: "product_name", required: false, rule: "Sugerencia opcional" },
        { name: "quantity", required: true, rule: "Decimal positivo" },
        { name: "record_type", required: true, rule: "sale / return / cancellation / correction" },
        { name: "record_status", required: true, rule: "issued / active / cancelled / voided / superseded" },
        { name: "document_number", required: false, rule: "Condicional según tipo · n.º original de factura si la fuente lo identifica así" },
        { name: "source_record_id", required: false, rule: "Identificador opcional" },
        { name: "source_line_id", required: false, rule: "Fortalece deduplicación" },
        { name: "unit_price", required: false, rule: "Decimal no negativo" },
    ]);

    const STATUS_LABELS = Object.freeze({
        uploaded: "Cargado",
        previewed: "Previsualizado",
        dry_run_ready: "Simulación lista",
        confirmed: "Confirmado",
        reverted: "Revertido",
    });

    const STATUS_ICONS = Object.freeze({
        uploaded: "ti-upload",
        previewed: "ti-eye",
        dry_run_ready: "ti-player-play",
        confirmed: "ti-circle-check",
        reverted: "ti-history",
    });

    const MATCH_LABELS = Object.freeze({
        pending: "Pendiente",
        exact: "Código exacto",
        exact_inactive: "Exacto inactivo",
        inactive_review: "Inactivo · revisar",
        manual_confirmed: "Confirmado manual",
        manual_inactive_approved: "Inactivo aprobado",
        name_suggested: "Sugerido por nombre",
        unmatched: "Sin coincidencia",
        code_collision: "Colisión de código",
    });

    const SEVERITY_LABELS = Object.freeze({
        error: "Error",
        warning: "Warning",
        review: "Revisión",
    });

    const RESOLUTION_LABELS = Object.freeze({
        unresolved: "Pendiente",
        resolved: "Resuelta",
        not_required: "No requerida",
    });

    const DUPLICATE_CODES = new Set([
        "strong_duplicate_in_file",
        "strong_duplicate_existing",
        "weak_possible_duplicate",
    ]);

    const PRODUCT_REVIEW_CODES = new Set([
        "product_unmatched",
        "product_name_suggestion",
        "product_inactive",
        "product_code_collision",
        "related_record_missing",
        "related_record_ambiguous",
        "related_record_invalid",
        "negative_net_demand",
        "weak_possible_duplicate",
    ]);

    const RELATIONSHIP_REVIEW_CODES = new Set([
        "related_record_missing",
        "related_record_ambiguous",
        "related_record_invalid",
    ]);

    const state = {
        importsPage: 1,
        importsPagination: null,
        currentImport: null,
        currentTab: "records",
        pages: {
            records: 1,
            errors: 1,
            pending: 1,
            duplicates: 1,
        },
        paginations: {
            records: null,
            errors: null,
            pending: null,
            duplicates: null,
        },
        pendingUploadHeaders: null,
        reviewRecord: null,
        reviewIssueCodes: new Set(),
        allowedReviewProductIds: new Set(),
        allowedReviewRelatedIds: new Set(),
        relationshipCandidatesPage: 1,
        relationshipCandidatesPagination: null,
        relationshipRequestSerial: 0,
    };

    const headerAllowlists = new Map();
    const confirmationTokens = new Map();
    const recordCache = new Map();
    const issueCodesByRecord = new Map();
    const modalInstances = new Map();

    const byId = (id) => document.getElementById(id);

    class HttpError extends Error {
        constructor(status, message) {
            super(message);
            this.name = "HttpError";
            this.status = status;
        }
    }

    function createElement(tag, className, value) {
        const node = document.createElement(tag);
        if (className) {
            node.className = className;
        }
        if (value !== undefined && value !== null) {
            node.textContent = String(value);
        }
        return node;
    }

    function createIcon(name, className) {
        const icon = createElement("i", `ti ${name}${className ? ` ${className}` : ""}`);
        icon.setAttribute("aria-hidden", "true");
        return icon;
    }

    function appendCell(row, value, className) {
        const cell = createElement("td", className || "", value);
        row.appendChild(cell);
        return cell;
    }

    function safeText(value, fallback = "—") {
        if (value === null || value === undefined || value === "") {
            return fallback;
        }
        return String(value);
    }

    function safeCount(value) {
        const number = Number(value);
        return Number.isFinite(number) && number >= 0 ? String(Math.trunc(number)) : "0";
    }

    function formatBytes(value) {
        const bytes = Number(value);
        if (!Number.isFinite(bytes) || bytes < 0) {
            return "—";
        }
        if (bytes < 1024) {
            return `${bytes} B`;
        }
        if (bytes < 1024 * 1024) {
            return `${(bytes / 1024).toFixed(1)} KiB`;
        }
        return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
    }

    function parseServerDate(value) {
        if (!value) {
            return null;
        }
        const candidate = String(value);
        const normalized = candidate.endsWith("Z") || candidate.includes("+")
            ? candidate
            : `${candidate}Z`;
        const date = new Date(normalized);
        return Number.isNaN(date.getTime()) ? null : date;
    }

    function formatDateTime(value) {
        const date = parseServerDate(value);
        if (!date) {
            return "—";
        }
        return date.toLocaleString("es-VE", {
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    function formatDecimal(value) {
        const number = Number(value);
        if (!Number.isFinite(number)) {
            return "—";
        }
        return new Intl.NumberFormat("es-VE", {
            minimumFractionDigits: 0,
            maximumFractionDigits: 2,
        }).format(number);
    }

    function statusBadge(status) {
        const normalized = Object.prototype.hasOwnProperty.call(STATUS_LABELS, status)
            ? status
            : "";
        const badge = createElement(
            "span",
            `badge historical-status ${normalized ? `historical-status-${normalized.replaceAll("_", "-")}` : "bg-secondary-lt"}`,
        );
        badge.appendChild(createIcon(STATUS_ICONS[normalized] || "ti-help-circle"));
        badge.appendChild(document.createTextNode(STATUS_LABELS[normalized] || safeText(status, "Desconocido")));
        return badge;
    }

    function matchBadge(status) {
        const normalized = Object.prototype.hasOwnProperty.call(MATCH_LABELS, status)
            ? status
            : "";
        return createElement(
            "span",
            `badge ${normalized ? `historical-match-${normalized.replaceAll("_", "-")}` : "bg-secondary-lt"}`,
            MATCH_LABELS[normalized] || safeText(status),
        );
    }

    function severityBadge(severity) {
        const normalized = Object.prototype.hasOwnProperty.call(SEVERITY_LABELS, severity)
            ? severity
            : "";
        return createElement(
            "span",
            `badge ${normalized ? `historical-severity-${normalized}` : "bg-secondary-lt"}`,
            SEVERITY_LABELS[normalized] || safeText(severity),
        );
    }

    function resolutionBadge(status) {
        const normalized = Object.prototype.hasOwnProperty.call(RESOLUTION_LABELS, status)
            ? status
            : "";
        return createElement(
            "span",
            `badge ${normalized ? `historical-resolution-${normalized.replaceAll("_", "-")}` : "bg-secondary-lt"}`,
            RESOLUTION_LABELS[normalized] || safeText(status),
        );
    }

    function stateRow(columns, message, iconName = "ti-inbox") {
        const row = createElement("tr", "historical-state-row");
        const cell = createElement("td");
        cell.colSpan = columns;
        const content = createElement("span", "historical-state-content");
        content.appendChild(createIcon(iconName));
        content.appendChild(createElement("span", "", message));
        cell.appendChild(content);
        row.appendChild(cell);
        return row;
    }

    function loadingRow(columns, message) {
        const row = createElement("tr", "historical-state-row");
        const cell = createElement("td");
        cell.colSpan = columns;
        const content = createElement("span", "historical-state-content");
        const spinner = createElement("span", "spinner-border historical-spinner");
        spinner.setAttribute("aria-hidden", "true");
        content.appendChild(spinner);
        content.appendChild(createElement("span", "", message));
        cell.appendChild(content);
        row.appendChild(cell);
        return row;
    }

    function setAlert(id, kind, message, focus = false) {
        const alert = byId(id);
        if (!alert) {
            return;
        }
        alert.className = `alert alert-${kind}`;
        alert.textContent = message;
        if (focus) {
            alert.focus({ preventScroll: true });
        }
    }

    function hideAlert(id) {
        const alert = byId(id);
        if (!alert) {
            return;
        }
        alert.classList.add("d-none");
        alert.textContent = "";
    }

    function announce(message) {
        const status = byId("page-status");
        if (!status) {
            return;
        }
        status.textContent = "";
        window.setTimeout(() => {
            status.textContent = message;
        }, 20);
    }

    function friendlyError(error) {
        if (!(error instanceof HttpError)) {
            return "Ocurrió un error inesperado.";
        }
        switch (error.status) {
            case 0:
                return "No se pudo conectar con el servidor. Verifique su conexión.";
            case 400:
                return error.message || "La solicitud contiene datos inválidos.";
            case 401:
                return "Su sesión expiró. Inicie sesión nuevamente.";
            case 403:
                return "No tiene permisos para realizar esta acción.";
            case 404:
                return error.message || "El recurso solicitado no existe.";
            case 409:
                return error.message || "El estado del lote cambió. Actualice la vista.";
            case 413:
                return error.message || "El archivo o la petición excede el tamaño permitido.";
            case 422:
                return error.message || "El archivo no supera las validaciones del servidor.";
            case 500:
                return "El servidor no pudo procesar la importación histórica.";
            default:
                return error.message || `Error HTTP ${error.status}.`;
        }
    }

    async function apiRequest(path, options = {}) {
        const method = String(options.method || "GET").toUpperCase();
        const url = new URL(path, window.location.origin);
        const params = options.params || {};
        Object.entries(params).forEach(([key, value]) => {
            if (value !== null && value !== undefined && String(value).trim() !== "") {
                url.searchParams.set(key, String(value).trim());
            }
        });

        const requestOptions = {
            method,
            credentials: "same-origin",
            headers: {
                Accept: options.accept || "application/json",
            },
        };

        if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
            if (!CSRF_TOKEN) {
                throw new HttpError(400, "No hay un token CSRF disponible para esta operación.");
            }
            requestOptions.headers["X-CSRFToken"] = CSRF_TOKEN;
        }

        if (options.json !== undefined) {
            requestOptions.headers["Content-Type"] = "application/json";
            requestOptions.body = JSON.stringify(options.json);
        } else if (options.formData instanceof FormData) {
            requestOptions.body = options.formData;
        }

        let response;
        try {
            response = await fetch(url, requestOptions);
        } catch (_error) {
            throw new HttpError(0, "Error de red.");
        }

        if (options.responseType === "blob" && response.ok) {
            return {
                body: await response.blob(),
                headers: response.headers,
            };
        }

        let payload = null;
        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("application/json")) {
            try {
                payload = await response.json();
            } catch (_error) {
                payload = null;
            }
        }

        if (!response.ok) {
            const message = payload && typeof payload.error === "string"
                ? payload.error
                : `Error HTTP ${response.status}.`;
            throw new HttpError(response.status, message);
        }

        if (options.responseType === "blob") {
            return {
                body: await response.blob(),
                headers: response.headers,
            };
        }
        return payload || {};
    }

    function setButtonBusy(button, busy, busyLabel, idleLabel) {
        if (!button) {
            return;
        }
        button.disabled = busy;
        button.setAttribute("aria-busy", busy ? "true" : "false");
        if (busy) {
            const spinner = createElement("span", "spinner-border spinner-border-sm me-2");
            spinner.setAttribute("aria-hidden", "true");
            button.replaceChildren(spinner, document.createTextNode(busyLabel));
        } else if (idleLabel) {
            button.textContent = idleLabel;
        }
    }

    function getModal(id) {
        if (!modalInstances.has(id)) {
            const element = byId(id);
            if (!element || !window.bootstrap) {
                return null;
            }
            modalInstances.set(id, new window.bootstrap.Modal(element));
        }
        return modalInstances.get(id);
    }

    function updatePagination(prefix, pagination) {
        const page = Number(pagination && pagination.page) || 1;
        const pages = Number(pagination && pagination.pages) || 0;
        const total = Number(pagination && pagination.total) || 0;
        const label = byId(`${prefix}-page-label`);
        const previous = byId(`${prefix}-prev-page`);
        const next = byId(`${prefix}-next-page`);
        if (label) {
            label.textContent = pages > 0
                ? `Página ${page} de ${pages} · ${total} resultados`
                : "Sin resultados";
        }
        if (previous) {
            previous.disabled = page <= 1;
        }
        if (next) {
            next.disabled = pages === 0 || page >= pages;
        }
    }

    function renderImportCounts(counts) {
        const container = createElement("div", "historical-counts");
        const values = [
            ["Filas", counts && counts.rows],
            ["Válidas", counts && counts.valid],
            ["Errores", counts && counts.errors],
            ["Pendientes", counts && counts.reviews_pending],
            ["Duplicados", counts && counts.possible_duplicates],
        ];
        values.forEach(([label, value]) => {
            const item = createElement("span");
            item.appendChild(createElement("strong", "", safeCount(value)));
            item.appendChild(document.createTextNode(` ${label}`));
            container.appendChild(item);
        });
        return container;
    }

    function renderImports(items) {
        const tbody = byId("imports-tbody");
        if (!tbody) {
            return;
        }
        if (!Array.isArray(items) || items.length === 0) {
            tbody.replaceChildren(stateRow(7, "No hay lotes para el filtro seleccionado."));
            return;
        }

        const rows = items.map((item) => {
            const row = createElement("tr");
            const metadata = item && typeof item.admin_metadata === "object"
                ? item.admin_metadata
                : null;

            const idCell = appendCell(row, null);
            idCell.appendChild(statusBadge(item.status));
            idCell.appendChild(createElement("div", "historical-uuid mt-2", safeText(item.id)));

            const fileCell = appendCell(row, null);
            if (metadata && metadata.original_filename) {
                const name = createElement("strong", "historical-file-name", metadata.original_filename);
                name.title = String(metadata.original_filename);
                fileCell.appendChild(name);
                fileCell.appendChild(createElement("small", "text-secondary", formatBytes(metadata.file_size_bytes)));
            } else {
                fileCell.appendChild(createElement("span", "text-secondary small", "No disponible para este rol"));
            }

            appendCell(row, formatDateTime(item.created_at), "text-nowrap");

            const userCell = appendCell(row, null);
            userCell.textContent = metadata && metadata.created_by_user_id
                ? `Usuario #${metadata.created_by_user_id}`
                : "No disponible para este rol";
            userCell.classList.add(metadata && metadata.created_by_user_id ? "text-nowrap" : "text-secondary", "small");

            const countsCell = appendCell(row, null);
            countsCell.appendChild(renderImportCounts(item.counts || {}));

            const shaCell = appendCell(row, null, "historical-code small");
            shaCell.textContent = metadata && metadata.sha256
                ? `${String(metadata.sha256).slice(0, 12)}…`
                : "No disponible";

            const actionsCell = appendCell(row, null, "text-end");
            const openButton = createElement("button", "btn btn-sm btn-outline-primary historical-inline-action");
            openButton.type = "button";
            openButton.appendChild(createIcon("ti-eye", "me-1"));
            openButton.appendChild(document.createTextNode("Abrir"));
            openButton.addEventListener("click", () => {
                openImportDetail(item.id).catch((error) => {
                    setAlert("page-alert", "danger", friendlyError(error), true);
                });
            });
            actionsCell.appendChild(openButton);
            return row;
        });
        tbody.replaceChildren(...rows);
    }

    async function loadImports() {
        const tbody = byId("imports-tbody");
        if (tbody) {
            tbody.replaceChildren(loadingRow(7, "Cargando lotes históricos…"));
        }
        hideAlert("page-alert");
        const status = byId("imports-status-filter") ? byId("imports-status-filter").value : "";
        try {
            const data = await apiRequest(API_BASE, {
                params: {
                    page: state.importsPage,
                    per_page: PAGE_SIZE,
                    status,
                },
            });
            state.importsPagination = data.pagination || null;
            renderImports(data.items || []);
            const total = Number(data.pagination && data.pagination.total) || 0;
            byId("imports-count").textContent = total === 1 ? "1 lote" : `${total} lotes`;
            updatePagination("imports", data.pagination);
            announce("Listado de lotes actualizado.");
        } catch (error) {
            if (tbody) {
                tbody.replaceChildren(stateRow(7, friendlyError(error), "ti-alert-circle"));
            }
            byId("imports-count").textContent = "No se pudo cargar";
            setAlert("page-alert", error.status === 401 ? "warning" : "danger", friendlyError(error), true);
            throw error;
        }
    }

    function setText(id, value, fallback) {
        const node = byId(id);
        if (node) {
            node.textContent = safeText(value, fallback);
        }
    }

    function setActionVisibility(id, visible, disabled, title) {
        const button = byId(id);
        if (!button) {
            return;
        }
        button.classList.toggle("d-none", !visible);
        button.disabled = Boolean(disabled);
        if (title) {
            button.title = title;
        } else {
            button.removeAttribute("title");
        }
    }

    function currentTokenEntry() {
        if (!state.currentImport || !state.currentImport.id) {
            return null;
        }
        const entry = confirmationTokens.get(state.currentImport.id);
        if (!entry) {
            return null;
        }
        if (entry.expiresAt && Date.now() >= entry.expiresAt.getTime()) {
            confirmationTokens.delete(state.currentImport.id);
            return null;
        }
        return entry;
    }

    function renderDetailActions(item) {
        const status = item.status;
        const mutable = status !== "confirmed" && status !== "reverted";
        setActionVisibility("btn-preview-import", CAN_REVIEW && mutable, false);
        setActionVisibility(
            "btn-dry-run",
            CAN_REVIEW && (status === "previewed" || status === "dry_run_ready"),
            false,
        );

        const tokenEntry = currentTokenEntry();
        const canShowConfirm = CAN_CONFIRM && status === "dry_run_ready";
        setActionVisibility(
            "btn-confirm-import",
            canShowConfirm,
            canShowConfirm && !tokenEntry,
            canShowConfirm && !tokenEntry
                ? "Ejecute nuevamente la simulación para obtener un token en memoria."
                : "",
        );
        setActionVisibility("btn-revert-import", CAN_REVERT && status === "confirmed", false);

        const mappingSubmit = byId("btn-save-preview");
        if (mappingSubmit) {
            mappingSubmit.disabled = !mutable;
            mappingSubmit.title = mutable ? "" : "Este lote ya no puede reprocesarse.";
        }
    }

    function addSummaryItem(container, label, value) {
        const item = createElement("div", "historical-summary-item");
        item.appendChild(createElement("span", "", label));
        item.appendChild(createElement("strong", "", safeText(value)));
        container.appendChild(item);
    }

    function renderDryRunSummary(summary) {
        const container = byId("dry-run-summary");
        if (!container) {
            return;
        }
        if (!summary || typeof summary !== "object") {
            container.replaceChildren(
                createElement("div", "historical-empty-inline", "Todavía no hay una simulación disponible."),
            );
            return;
        }
        const grid = createElement("div", "historical-summary-grid");
        addSummaryItem(grid, "Filas válidas", safeCount(summary.valid_rows));
        addSummaryItem(grid, "Productos enlazados", safeCount(summary.matched));
        addSummaryItem(grid, "Warnings", safeCount(summary.warnings));
        addSummaryItem(grid, "Revisiones pendientes", safeCount(summary.unresolved_reviews));
        const quantities = summary.quantities && typeof summary.quantities === "object"
            ? summary.quantities
            : {};
        addSummaryItem(grid, "Ventas", formatDecimal(quantities.sales));
        addSummaryItem(grid, "Devoluciones", formatDecimal(quantities.returns));
        addSummaryItem(grid, "Correcciones", formatDecimal(quantities.corrections));
        addSummaryItem(grid, "Demanda neta histórica", formatDecimal(quantities.net_demand));
        container.replaceChildren(grid);
    }

    function uniqueHeaders(values) {
        const result = [];
        const seen = new Set();
        values.forEach((value) => {
            if (typeof value !== "string") {
                return;
            }
            const normalized = value.trim();
            if (!normalized || seen.has(normalized)) {
                return;
            }
            seen.add(normalized);
            result.push(normalized);
        });
        return result;
    }

    function renderMapping(item) {
        const tbody = byId("mapping-tbody");
        const alert = byId("mapping-alert");
        if (!tbody || !alert) {
            return;
        }
        const metadata = item && typeof item.admin_metadata === "object"
            ? item.admin_metadata
            : null;
        const mapping = metadata && metadata.mapping && typeof metadata.mapping === "object"
            ? metadata.mapping
            : null;
        let headers = headerAllowlists.get(item.id) || [];
        const persistedHeaders = metadata
            && metadata.metadata
            && Array.isArray(metadata.metadata.headers)
            ? metadata.metadata.headers
            : [];
        if (headers.length === 0 && persistedHeaders.length > 0) {
            headers = uniqueHeaders(persistedHeaders);
            headerAllowlists.set(item.id, headers);
        }
        if (headers.length === 0 && mapping) {
            headers = uniqueHeaders(Object.values(mapping));
            headerAllowlists.set(item.id, headers);
        }

        const canEdit = CAN_REVIEW && item.status !== "confirmed" && item.status !== "reverted";
        if (!metadata && !CAN_REVIEW) {
            alert.className = "alert alert-info mb-0";
            alert.textContent = "El mapping técnico no forma parte del payload de solo lectura para este rol.";
        } else if (headers.length > 0) {
            alert.className = "alert alert-success mb-0";
            alert.textContent = `${headers.length} encabezados permitidos disponibles. Los selects no aceptan valores fuera de esta lista.`;
        } else {
            alert.className = "alert alert-warning mb-0";
            alert.textContent = "La API no expone encabezados de lotes existentes sin mapping. Preview puede usar el automapping únicamente si el archivo coincide exactamente con la plantilla v1.";
        }

        const rows = CANONICAL_FIELDS.map((field) => {
            const row = createElement("tr");
            const canonicalCell = appendCell(row, null);
            canonicalCell.appendChild(createElement("code", "", field.name));
            if (field.required) {
                const required = createElement("span", "historical-required-dot ms-1", "*");
                required.title = "Obligatorio";
                canonicalCell.appendChild(required);
            }

            const selectCell = appendCell(row, null);
            const select = createElement("select", "form-select form-select-sm");
            select.id = `mapping-${field.name}`;
            select.dataset.canonical = field.name;
            select.disabled = !canEdit || headers.length === 0;
            select.setAttribute("aria-label", `Encabezado para ${field.name}`);
            const empty = createElement(
                "option",
                "",
                field.required ? "Seleccione un encabezado" : "No mapear",
            );
            empty.value = "";
            select.appendChild(empty);
            headers.forEach((header) => {
                const option = createElement("option", "", header);
                option.value = header;
                select.appendChild(option);
            });
            const current = mapping && typeof mapping[field.name] === "string"
                ? mapping[field.name]
                : (headers.includes(field.name) ? field.name : "");
            if (headers.includes(current)) {
                select.value = current;
            }
            selectCell.appendChild(select);
            appendCell(row, field.rule, "text-secondary small");
            return row;
        });
        tbody.replaceChildren(...rows);
    }

    function renderImportDetail(item) {
        state.currentImport = item;
        const metadata = item && typeof item.admin_metadata === "object"
            ? item.admin_metadata
            : null;
        byId("detail-status-badge").replaceChildren(statusBadge(item.status));
        setText("detail-id", item.id);
        setText("detail-source", `Origen: ${safeText(item.source_system)} · Esquema: ${safeText(item.schema_version)}`);
        setText(
            "detail-filename",
            metadata && metadata.original_filename
                ? `${metadata.original_filename} · ${formatBytes(metadata.file_size_bytes)}`
                : "No disponible para este rol",
        );
        setText("detail-created-at", formatDateTime(item.created_at));
        setText(
            "detail-created-by",
            metadata && metadata.created_by_user_id
                ? `Usuario #${metadata.created_by_user_id}`
                : "No disponible para este rol",
        );
        setText(
            "detail-sha",
            metadata && metadata.sha256
                ? `${String(metadata.sha256).slice(0, 16)}…`
                : "No disponible para este rol",
        );
        const counts = item.counts || {};
        setText("detail-count-valid", safeCount(counts.valid));
        setText("detail-count-warnings", safeCount(counts.warnings));
        setText("detail-count-errors", safeCount(counts.errors));
        setText("detail-count-pending", safeCount(counts.reviews_pending));
        setText("detail-count-duplicates", safeCount(counts.possible_duplicates));
        renderDryRunSummary(item.dry_run_summary);
        renderMapping(item);
        renderDetailActions(item);
    }

    function renderDetailLoading(importId) {
        const badge = createElement("span", "badge bg-secondary-lt");
        const spinner = createElement("span", "spinner-border spinner-border-sm me-1");
        spinner.setAttribute("aria-hidden", "true");
        badge.appendChild(spinner);
        badge.appendChild(document.createTextNode("Cargando"));
        byId("detail-status-badge").replaceChildren(badge);
        setText("detail-id", importId);
        setText("detail-source", "Cargando metadatos…");
        setText("detail-filename", "—");
        setText("detail-created-at", "—");
        setText("detail-created-by", "—");
        setText("detail-sha", "—");
        setText("detail-count-valid", "—");
        setText("detail-count-warnings", "—");
        setText("detail-count-errors", "—");
        setText("detail-count-pending", "—");
        setText("detail-count-duplicates", "—");
        renderDryRunSummary(null);
        byId("mapping-alert").className = "alert alert-info mb-0";
        byId("mapping-alert").textContent = "Cargando mapping del lote…";
        byId("mapping-tbody").replaceChildren(loadingRow(3, "Cargando mapping…"));
        byId("records-tbody").replaceChildren(loadingRow(8, "Cargando registros…"));
        setActionVisibility("btn-preview-import", false, true);
        setActionVisibility("btn-dry-run", false, true);
        setActionVisibility("btn-confirm-import", false, true);
        setActionVisibility("btn-revert-import", false, true);
    }

    async function loadImportDetail() {
        if (!state.currentImport || !state.currentImport.id) {
            return;
        }
        hideAlert("detail-alert");
        const data = await apiRequest(`${API_BASE}/${encodeURIComponent(state.currentImport.id)}`);
        renderImportDetail(data);
    }

    async function openImportDetail(importId) {
        if (!importId) {
            return;
        }
        state.currentImport = { id: String(importId), status: "uploaded" };
        state.pages = { records: 1, errors: 1, pending: 1, duplicates: 1 };
        state.currentTab = "records";
        recordCache.clear();
        issueCodesByRecord.clear();
        byId("imports-view").classList.add("d-none");
        byId("import-detail-view").classList.remove("d-none");
        activateTab("records", false);
        window.scrollTo({ top: 0, behavior: "auto" });
        renderDetailLoading(importId);
        try {
            await loadImportDetail();
            await loadRecords("records");
            byId("btn-back-to-imports").focus({ preventScroll: true });
        } catch (error) {
            setAlert("detail-alert", error.status === 401 ? "warning" : "danger", friendlyError(error), true);
            throw error;
        }
    }

    function closeImportDetail() {
        byId("import-detail-view").classList.add("d-none");
        byId("imports-view").classList.remove("d-none");
        state.currentImport = null;
        state.reviewRecord = null;
        state.relationshipRequestSerial += 1;
        recordCache.clear();
        issueCodesByRecord.clear();
        window.scrollTo({ top: 0, behavior: "auto" });
        const refresh = byId("btn-refresh-imports");
        if (refresh) {
            refresh.focus({ preventScroll: true });
        }
        loadImports().catch(() => {});
    }

    function cacheRecords(items) {
        if (!Array.isArray(items)) {
            return;
        }
        items.forEach((record) => {
            if (record && Number.isInteger(Number(record.id))) {
                recordCache.set(Number(record.id), record);
            }
        });
    }

    function reviewButton(record, issueCode) {
        const button = createElement("button", "btn btn-sm btn-outline-primary");
        button.type = "button";
        button.textContent = "Revisar";
        const status = state.currentImport && state.currentImport.status;
        const allowedStatus = status === "previewed" || status === "dry_run_ready";
        button.disabled = !CAN_REVIEW || !allowedStatus;
        if (!allowedStatus) {
            button.title = "Solo se revisan filas de lotes previsualizados no confirmados.";
        }
        button.addEventListener("click", () => {
            openReviewModal(record, issueCode ? [issueCode] : []);
        });
        return button;
    }

    function renderRecords(items) {
        const tbody = byId("records-tbody");
        if (!tbody) {
            return;
        }
        if (!Array.isArray(items) || items.length === 0) {
            tbody.replaceChildren(stateRow(8, "Este lote todavía no tiene registros previsualizados."));
            return;
        }
        const rows = items.map((record) => {
            const row = createElement("tr");
            appendCell(row, safeText(record.source_row_number), "historical-code");
            appendCell(row, safeText(record.event_date), "text-nowrap");

            const productCell = appendCell(row, null, "historical-product-cell");
            productCell.appendChild(createElement("strong", "", safeText(record.product_code)));
            productCell.appendChild(createElement("span", "", safeText(record.product_name, "Sin nombre histórico")));

            const typeCell = appendCell(row, null);
            typeCell.appendChild(createElement("strong", "", safeText(record.record_type)));
            typeCell.appendChild(createElement("div", "text-secondary small", safeText(record.record_status)));

            appendCell(row, formatDecimal(record.quantity), "text-end historical-code");
            const matchCell = appendCell(row, null);
            matchCell.appendChild(matchBadge(record.match_status));
            appendCell(row, safeText(record.fingerprint_strength), "text-nowrap");

            const actionsCell = appendCell(row, null, "text-end");
            if (CAN_REVIEW) {
                actionsCell.appendChild(reviewButton(record));
            } else {
                actionsCell.appendChild(createElement("span", "text-secondary small", "Solo lectura"));
            }
            return row;
        });
        tbody.replaceChildren(...rows);
    }

    function renderPendingRecords(items) {
        const tbody = byId("pending-tbody");
        if (!tbody) {
            return;
        }
        if (!Array.isArray(items) || items.length === 0) {
            tbody.replaceChildren(stateRow(5, "No hay registros con este estado de match."));
            return;
        }
        const rows = items.map((record) => {
            const row = createElement("tr");
            appendCell(row, safeText(record.source_row_number), "historical-code");
            const productCell = appendCell(row, null, "historical-product-cell");
            productCell.appendChild(createElement("strong", "", safeText(record.product_code)));
            productCell.appendChild(createElement("span", "", safeText(record.product_name, "Sin nombre histórico")));
            const matchCell = appendCell(row, null);
            matchCell.appendChild(matchBadge(record.match_status));
            const candidate = record.suggested_product_id || record.product_id;
            appendCell(
                row,
                candidate ? `Producto #${candidate}` : "La API no entregó candidatos",
                candidate ? "historical-code" : "text-secondary small",
            );
            const actionsCell = appendCell(row, null, "text-end");
            if (CAN_REVIEW) {
                actionsCell.appendChild(reviewButton(record));
            } else {
                actionsCell.appendChild(createElement("span", "text-secondary small", "Solo lectura"));
            }
            return row;
        });
        tbody.replaceChildren(...rows);
    }

    async function loadRecords(kind) {
        if (!state.currentImport || !state.currentImport.id) {
            return;
        }
        const isPending = kind === "pending";
        const tbody = byId(isPending ? "pending-tbody" : "records-tbody");
        const columns = isPending ? 5 : 8;
        if (tbody) {
            tbody.replaceChildren(loadingRow(columns, "Cargando registros…"));
        }
        const params = {
            page: state.pages[kind],
            per_page: PAGE_SIZE,
        };
        if (isPending) {
            params.match_status = byId("pending-match-status").value;
        }
        try {
            const data = await apiRequest(
                `${API_BASE}/${encodeURIComponent(state.currentImport.id)}/records`,
                { params },
            );
            state.paginations[kind] = data.pagination || null;
            cacheRecords(data.items || []);
            if (isPending) {
                renderPendingRecords(data.items || []);
            } else {
                renderRecords(data.items || []);
            }
            updatePagination(kind, data.pagination);
        } catch (error) {
            if (tbody) {
                tbody.replaceChildren(stateRow(columns, friendlyError(error), "ti-alert-circle"));
            }
            throw error;
        }
    }

    function cacheIssues(items) {
        if (!Array.isArray(items)) {
            return;
        }
        items.forEach((issue) => {
            const recordId = Number(issue && issue.record_id);
            if (!Number.isInteger(recordId)) {
                return;
            }
            if (!issueCodesByRecord.has(recordId)) {
                issueCodesByRecord.set(recordId, new Set());
            }
            if (typeof issue.code === "string") {
                issueCodesByRecord.get(recordId).add(issue.code);
            }
        });
    }

    async function findRecordForIssue(issue) {
        const recordId = Number(issue.record_id);
        if (recordCache.has(recordId)) {
            return recordCache.get(recordId);
        }
        if (!state.currentImport || !state.currentImport.id) {
            throw new HttpError(404, "No hay un lote abierto.");
        }

        const targetRow = Number(issue.source_row_number);
        let low = 1;
        let high = null;
        let page = 1;
        for (let attempt = 0; attempt < 12; attempt += 1) {
            const data = await apiRequest(
                `${API_BASE}/${encodeURIComponent(state.currentImport.id)}/records`,
                { params: { page, per_page: 100 } },
            );
            cacheRecords(data.items || []);
            if (recordCache.has(recordId)) {
                return recordCache.get(recordId);
            }
            const pages = Number(data.pagination && data.pagination.pages) || 0;
            if (high === null) {
                high = pages;
            }
            if (pages === 0 || !Array.isArray(data.items) || data.items.length === 0) {
                high = Math.max(low, page - 1);
            } else if (Number.isFinite(targetRow)) {
                const firstRow = Number(data.items[0].source_row_number);
                const lastRow = Number(data.items[data.items.length - 1].source_row_number);
                if (targetRow < firstRow) {
                    high = page - 1;
                } else if (targetRow > lastRow) {
                    low = page + 1;
                } else {
                    break;
                }
            } else {
                low = page + 1;
            }
            if (high !== null && low > high) {
                break;
            }
            page = high === null ? low : Math.floor((low + high) / 2);
        }
        throw new HttpError(404, "No se pudo localizar el registro asociado en el contrato paginado.");
    }

    function issueReviewButton(issue) {
        const button = createElement("button", "btn btn-sm btn-outline-primary", "Revisar");
        const allowed = CAN_REVIEW
            && issue.record_id
            && issue.resolution_status === "unresolved"
            && PRODUCT_REVIEW_CODES.has(issue.code)
            && state.currentImport
            && (state.currentImport.status === "previewed" || state.currentImport.status === "dry_run_ready");
        button.disabled = !allowed;
        if (!allowed) {
            button.title = "Este hallazgo no admite revisión manual en el estado actual.";
        }
        button.addEventListener("click", () => {
            button.disabled = true;
            findRecordForIssue(issue)
                .then((record) => {
                    openReviewModal(record, [issue.code]);
                })
                .catch((error) => {
                    setAlert("detail-alert", "danger", friendlyError(error), true);
                })
                .finally(() => {
                    button.disabled = !allowed;
                });
        });
        return button;
    }

    function renderErrors(items) {
        const tbody = byId("errors-tbody");
        if (!tbody) {
            return;
        }
        if (!Array.isArray(items) || items.length === 0) {
            tbody.replaceChildren(stateRow(6, "No hay hallazgos para estos filtros."));
            return;
        }
        const rows = items.map((issue) => {
            const row = createElement("tr");
            appendCell(row, safeText(issue.source_row_number, "General"), "historical-code");
            const severityCell = appendCell(row, null);
            severityCell.appendChild(severityBadge(issue.severity));
            const codeCell = appendCell(row, null);
            codeCell.appendChild(createElement("strong", "", safeText(issue.field, "General")));
            codeCell.appendChild(createElement("div", "historical-code text-secondary small", safeText(issue.code)));
            appendCell(row, safeText(issue.message), "historical-message-cell");
            const resolutionCell = appendCell(row, null);
            resolutionCell.appendChild(resolutionBadge(issue.resolution_status));
            const actionsCell = appendCell(row, null, "text-end");
            actionsCell.appendChild(issueReviewButton(issue));
            return row;
        });
        tbody.replaceChildren(...rows);
    }

    function renderDuplicates(items) {
        const tbody = byId("duplicates-tbody");
        if (!tbody) {
            return;
        }
        const duplicateItems = Array.isArray(items)
            ? items.filter((issue) => DUPLICATE_CODES.has(issue.code))
            : [];
        if (duplicateItems.length === 0) {
            tbody.replaceChildren(
                stateRow(5, "No hay hallazgos de duplicación en esta página del endpoint."),
            );
            return;
        }
        const rows = duplicateItems.map((issue) => {
            const row = createElement("tr");
            appendCell(row, safeText(issue.source_row_number, "General"), "historical-code");
            const typeCell = appendCell(row, null);
            typeCell.appendChild(severityBadge(issue.severity));
            typeCell.appendChild(createElement("div", "historical-code text-secondary small mt-1", issue.code));
            appendCell(row, safeText(issue.message), "historical-message-cell");
            const resolutionCell = appendCell(row, null);
            resolutionCell.appendChild(resolutionBadge(issue.resolution_status));
            const actionsCell = appendCell(row, null, "text-end");
            actionsCell.appendChild(issueReviewButton(issue));
            return row;
        });
        tbody.replaceChildren(...rows);
    }

    async function loadErrors(kind) {
        if (!state.currentImport || !state.currentImport.id) {
            return;
        }
        const isDuplicates = kind === "duplicates";
        const tbody = byId(isDuplicates ? "duplicates-tbody" : "errors-tbody");
        const columns = isDuplicates ? 5 : 6;
        if (tbody) {
            tbody.replaceChildren(loadingRow(columns, "Cargando hallazgos…"));
        }
        const params = {
            page: state.pages[kind],
            per_page: PAGE_SIZE,
        };
        if (!isDuplicates) {
            params.severity = byId("errors-severity").value;
            params.resolution_status = byId("errors-resolution").value;
        } else {
            params.category = "duplicate";
        }
        try {
            const data = await apiRequest(
                `${API_BASE}/${encodeURIComponent(state.currentImport.id)}/errors`,
                { params },
            );
            state.paginations[kind] = data.pagination || null;
            cacheIssues(data.items || []);
            if (isDuplicates) {
                renderDuplicates(data.items || []);
            } else {
                renderErrors(data.items || []);
            }
            updatePagination(kind, data.pagination);
        } catch (error) {
            if (tbody) {
                tbody.replaceChildren(stateRow(columns, friendlyError(error), "ti-alert-circle"));
            }
            throw error;
        }
    }

    function activateTab(name, shouldLoad = true) {
        const allowed = new Set(["records", "errors", "pending", "duplicates"]);
        if (!allowed.has(name)) {
            return;
        }
        state.currentTab = name;
        document.querySelectorAll("[data-historical-tab]").forEach((button) => {
            const active = button.dataset.historicalTab === name;
            button.classList.toggle("active", active);
            button.setAttribute("aria-selected", active ? "true" : "false");
            button.tabIndex = active ? 0 : -1;
        });
        document.querySelectorAll("[data-historical-panel]").forEach((panel) => {
            const active = panel.dataset.historicalPanel === name;
            panel.classList.toggle("d-none", !active);
            panel.hidden = !active;
        });
        if (!shouldLoad) {
            return;
        }
        const loader = name === "records" || name === "pending"
            ? loadRecords(name)
            : loadErrors(name);
        loader.catch((error) => {
            setAlert("detail-alert", "danger", friendlyError(error), true);
        });
    }

    function collectMapping() {
        if (!state.currentImport || !state.currentImport.id) {
            throw new Error("No hay lote abierto.");
        }
        const headers = headerAllowlists.get(state.currentImport.id) || [];
        if (headers.length === 0) {
            return null;
        }
        const allowed = new Set(headers);
        const used = new Set();
        const mapping = {};
        CANONICAL_FIELDS.forEach((field) => {
            const select = byId(`mapping-${field.name}`);
            const value = select ? select.value : "";
            if (field.required && !value) {
                throw new Error(`Seleccione un encabezado para ${field.name}.`);
            }
            if (!value) {
                return;
            }
            if (!allowed.has(value)) {
                throw new Error(`El encabezado seleccionado para ${field.name} no está permitido.`);
            }
            if (used.has(value)) {
                throw new Error(`El encabezado “${value}” no puede usarse más de una vez.`);
            }
            used.add(value);
            mapping[field.name] = value;
        });
        return mapping;
    }

    async function runPreview(triggerButton) {
        if (!CAN_REVIEW || !state.currentImport || !state.currentImport.id) {
            return;
        }
        hideAlert("detail-alert");
        let mapping;
        try {
            mapping = collectMapping();
        } catch (error) {
            setAlert("detail-alert", "warning", error.message, true);
            return;
        }

        setButtonBusy(triggerButton, true, "Generando preview…");
        confirmationTokens.delete(state.currentImport.id);
        try {
            const payload = mapping ? { mapping } : {};
            const data = await apiRequest(
                `${API_BASE}/${encodeURIComponent(state.currentImport.id)}/preview`,
                { method: "POST", json: payload },
            );
            renderImportDetail(data.historical_import);
            state.pages.records = 1;
            state.pages.errors = 1;
            state.pages.pending = 1;
            state.pages.duplicates = 1;
            recordCache.clear();
            issueCodesByRecord.clear();
            setAlert("detail-alert", "success", data.message || "Preview generado correctamente.", true);
            await loadRecords("records");
            announce("Preview histórico actualizado.");
        } catch (error) {
            setAlert("detail-alert", error.status === 422 ? "warning" : "danger", friendlyError(error), true);
        } finally {
            const idleLabel = triggerButton && triggerButton.id === "btn-preview-import"
                ? "Preview"
                : "Generar preview con este mapping";
            setButtonBusy(triggerButton, false, "", idleLabel);
            renderDetailActions(state.currentImport);
        }
    }

    async function runDryRun() {
        if (!CAN_REVIEW || !state.currentImport || !state.currentImport.id) {
            return;
        }
        const button = byId("btn-dry-run");
        hideAlert("detail-alert");
        setButtonBusy(button, true, "Simulando…");
        confirmationTokens.delete(state.currentImport.id);
        try {
            const data = await apiRequest(
                `${API_BASE}/${encodeURIComponent(state.currentImport.id)}/dry-run`,
                { method: "POST" },
            );
            const expiresValue = data.historical_import
                && data.historical_import.admin_metadata
                && data.historical_import.admin_metadata.confirmation_token_expires_at;
            const expiresAt = parseServerDate(expiresValue);
            if (typeof data.confirmation_token !== "string" || !data.confirmation_token) {
                throw new HttpError(500, "El servidor no devolvió un token de confirmación.");
            }
            confirmationTokens.set(state.currentImport.id, {
                token: data.confirmation_token,
                expiresAt,
            });
            renderImportDetail(data.historical_import);
            renderDryRunSummary(data.summary);
            setAlert("detail-alert", "success", data.message || "Simulación completada.", true);
            announce("Simulación completada; el token está disponible en memoria.");
        } catch (error) {
            confirmationTokens.delete(state.currentImport.id);
            setAlert(
                "detail-alert",
                error.status === 409 || error.status === 422 ? "warning" : "danger",
                friendlyError(error),
                true,
            );
            if (error.status === 409 || error.status === 422) {
                await loadImportDetail().catch(() => {});
            }
        } finally {
            setButtonBusy(button, false, "", "Ejecutar simulación");
            if (state.currentImport) {
                renderDetailActions(state.currentImport);
            }
        }
    }

    function fillConfirmSummary() {
        const container = byId("confirm-summary");
        if (!container) {
            return;
        }
        const summary = state.currentImport && state.currentImport.dry_run_summary;
        const counts = state.currentImport && state.currentImport.counts
            ? state.currentImport.counts
            : {};
        const values = [
            ["Lote", state.currentImport ? state.currentImport.id : "—"],
            ["Filas válidas", summary ? safeCount(summary.valid_rows) : safeCount(counts.valid)],
            ["Errores", summary ? safeCount(summary.errors) : safeCount(counts.errors)],
            ["Revisiones pendientes", summary ? safeCount(summary.unresolved_reviews) : safeCount(counts.reviews_pending)],
            ["Posibles duplicados", safeCount(counts.possible_duplicates)],
        ];
        const nodes = values.map(([label, value]) => {
            const item = createElement("div");
            item.appendChild(createElement("span", "", label));
            item.appendChild(createElement("strong", "", value));
            return item;
        });
        container.replaceChildren(...nodes);
    }

    function openConfirmModal() {
        const tokenEntry = currentTokenEntry();
        if (!tokenEntry) {
            setAlert(
                "detail-alert",
                "warning",
                "El token no está disponible o expiró. Ejecute nuevamente la simulación.",
                true,
            );
            renderDetailActions(state.currentImport);
            return;
        }
        hideAlert("confirm-error");
        byId("confirm-atomic").checked = false;
        fillConfirmSummary();
        const modal = getModal("confirm-modal");
        if (modal) {
            modal.show();
        }
    }

    async function confirmImport(event) {
        event.preventDefault();
        if (!CAN_CONFIRM || !state.currentImport || !state.currentImport.id) {
            return;
        }
        hideAlert("confirm-error");
        if (!byId("confirm-atomic").checked) {
            setAlert("confirm-error", "warning", "Debe aceptar la confirmación todo-o-nada.");
            return;
        }
        const tokenEntry = currentTokenEntry();
        if (!tokenEntry) {
            setAlert("confirm-error", "warning", "El token expiró. Ejecute nuevamente la simulación.");
            renderDetailActions(state.currentImport);
            return;
        }
        const button = byId("btn-confirm-submit");
        setButtonBusy(button, true, "Confirmando…");
        try {
            const data = await apiRequest(
                `${API_BASE}/${encodeURIComponent(state.currentImport.id)}/confirm`,
                {
                    method: "POST",
                    json: { confirmation_token: tokenEntry.token },
                },
            );
            confirmationTokens.delete(state.currentImport.id);
            const modal = getModal("confirm-modal");
            if (modal) {
                modal.hide();
            }
            renderImportDetail(data.historical_import);
            setAlert("detail-alert", "success", data.message || "Lote confirmado.", true);
            announce("Importación histórica confirmada.");
        } catch (error) {
            if (error.status === 409 || error.status === 422) {
                confirmationTokens.delete(state.currentImport.id);
            }
            setAlert(
                "confirm-error",
                error.status === 409 || error.status === 422 ? "warning" : "danger",
                friendlyError(error),
            );
            await loadImportDetail().catch(() => {});
        } finally {
            setButtonBusy(button, false, "", "Confirmar importación");
            if (state.currentImport) {
                renderDetailActions(state.currentImport);
            }
        }
    }

    function openRevertModal() {
        hideAlert("revert-error");
        byId("revert-reason").value = "";
        byId("revert-reason-count").textContent = "0";
        byId("revert-confirmation").checked = false;
        const modal = getModal("revert-modal");
        if (modal) {
            modal.show();
        }
    }

    async function revertImport(event) {
        event.preventDefault();
        if (!CAN_REVERT || !state.currentImport || !state.currentImport.id) {
            return;
        }
        hideAlert("revert-error");
        const reason = byId("revert-reason").value.trim();
        if (!reason) {
            setAlert("revert-error", "warning", "El motivo de reversión es obligatorio.");
            return;
        }
        if (reason.length > 1000) {
            setAlert("revert-error", "warning", "El motivo no puede exceder 1000 caracteres.");
            return;
        }
        if (!byId("revert-confirmation").checked) {
            setAlert("revert-error", "warning", "Confirme la reversión lógica del lote.");
            return;
        }
        const button = byId("btn-revert-submit");
        setButtonBusy(button, true, "Revirtiendo…");
        try {
            const data = await apiRequest(
                `${API_BASE}/${encodeURIComponent(state.currentImport.id)}/revert`,
                { method: "POST", json: { reason } },
            );
            confirmationTokens.delete(state.currentImport.id);
            const modal = getModal("revert-modal");
            if (modal) {
                modal.hide();
            }
            renderImportDetail(data.historical_import);
            setAlert("detail-alert", "success", data.message || "Lote revertido.", true);
            announce("Reversión lógica completada; el stock no fue modificado.");
        } catch (error) {
            setAlert(
                "revert-error",
                error.status === 409 || error.status === 422 ? "warning" : "danger",
                friendlyError(error),
            );
            await loadImportDetail().catch(() => {});
        } finally {
            setButtonBusy(button, false, "", "Revertir lote");
        }
    }

    function getRecordFlags(record) {
        const metadata = record && record.admin_metadata;
        return metadata && metadata.review_flags && typeof metadata.review_flags === "object"
            ? metadata.review_flags
            : {};
    }

    function setReviewCheckVisibility(wrapId, inputId, visible, checked) {
        const wrap = byId(wrapId);
        const input = byId(inputId);
        if (!wrap || !input) {
            return;
        }
        wrap.classList.toggle("d-none", !visible);
        input.checked = visible && Boolean(checked);
    }

    function hasRelationshipReviewIssue() {
        return Array.from(state.reviewIssueCodes).some((code) => (
            RELATIONSHIP_REVIEW_CODES.has(code)
        ));
    }

    function updateRelationshipCandidatePagination(pagination) {
        const page = Number(pagination && pagination.page) || 1;
        const pages = Number(pagination && pagination.pages) || 0;
        const total = Number(pagination && pagination.total) || 0;
        state.relationshipCandidatesPage = page;
        state.relationshipCandidatesPagination = pagination || null;
        byId("review-related-page-label").textContent = pages > 0
            ? `Página ${page} de ${pages} · ${total} candidatos`
            : "Sin candidatos";
        byId("review-related-prev-page").disabled = page <= 1;
        byId("review-related-next-page").disabled = pages === 0 || page >= pages;
    }

    function renderRelationshipCandidates(record, data) {
        const items = Array.isArray(data && data.items) ? data.items : [];
        const pagination = data && data.pagination ? data.pagination : null;
        const relationGroup = byId("review-relationship-group");
        const relationSelect = byId("review-related-record-id");
        const relationOptions = [];
        const blankRelation = createElement("option", "", "No cambiar la relación");
        blankRelation.value = "";
        relationOptions.push(blankRelation);
        state.allowedReviewRelatedIds = new Set();

        items.forEach((candidate) => {
            const id = Number(candidate && candidate.id);
            if (!Number.isInteger(id) || id < 1 || state.allowedReviewRelatedIds.has(id)) {
                return;
            }
            state.allowedReviewRelatedIds.add(id);
            const parts = [
                `Registro #${id}`,
                `fila ${safeText(candidate.source_row_number, "externa")}`,
                safeText(candidate.record_type, "venta/corrección"),
            ];
            if (candidate.event_date) {
                parts.push(String(candidate.event_date));
            }
            if (candidate.quantity !== null && candidate.quantity !== undefined) {
                parts.push(`cantidad ${formatDecimal(candidate.quantity)}`);
            }
            const option = createElement("option", "", parts.join(" · "));
            option.value = String(id);
            relationOptions.push(option);
        });

        const currentRelatedId = record.related_record_id === null
            || record.related_record_id === undefined
            || record.related_record_id === ""
            ? null
            : Number(record.related_record_id);
        if (
            Number.isInteger(currentRelatedId)
            && currentRelatedId > 0
            && !state.allowedReviewRelatedIds.has(currentRelatedId)
        ) {
            state.allowedReviewRelatedIds.add(currentRelatedId);
            const current = createElement(
                "option",
                "",
                `Relación vigente entregada por la API #${currentRelatedId}`,
            );
            current.value = String(currentRelatedId);
            relationOptions.push(current);
        }

        relationSelect.replaceChildren(...relationOptions);
        relationSelect.disabled = state.allowedReviewRelatedIds.size === 0;
        updateRelationshipCandidatePagination(pagination);

        const total = Number(pagination && pagination.total) || 0;
        const showRelationship = record.record_type !== "sale"
            && (
                total > 0
                || (Number.isInteger(currentRelatedId) && currentRelatedId > 0)
                || hasRelationshipReviewIssue()
            );
        relationGroup.classList.toggle("d-none", !showRelationship);
        byId("review-related-hint").textContent = total > 0
            ? "Candidatos validados por el servidor; la selección se vuelve a comprobar al guardar."
            : "El servidor no encontró candidatos válidos para esta relación.";

        const flags = getRecordFlags(record);
        setReviewCheckVisibility(
            "review-relationship-wrap",
            "review-relationship",
            showRelationship && state.allowedReviewRelatedIds.size > 0,
            flags.relationship,
        );
    }

    async function loadRelationshipCandidates(record, page = 1) {
        if (
            !CAN_REVIEW
            || !state.currentImport
            || !record
            || record.record_type === "sale"
        ) {
            return;
        }
        const importId = String(state.currentImport.id);
        const recordId = Number(record.id);
        const requestSerial = ++state.relationshipRequestSerial;
        const relationGroup = byId("review-relationship-group");
        const relationSelect = byId("review-related-record-id");
        const loadingOption = createElement("option", "", "Cargando candidatos validados…");
        loadingOption.value = "";
        relationSelect.replaceChildren(loadingOption);
        relationSelect.disabled = true;
        relationGroup.classList.remove("d-none");
        byId("review-related-hint").textContent = "Consultando candidatos válidos en el servidor…";
        byId("review-related-page-label").textContent = "Cargando…";
        byId("review-related-prev-page").disabled = true;
        byId("review-related-next-page").disabled = true;
        setReviewCheckVisibility(
            "review-relationship-wrap",
            "review-relationship",
            false,
            false,
        );

        try {
            const data = await apiRequest(
                `${API_BASE}/${encodeURIComponent(importId)}/records/${encodeURIComponent(recordId)}/relationship-candidates`,
                { params: { page, per_page: PAGE_SIZE } },
            );
            if (
                requestSerial !== state.relationshipRequestSerial
                || !state.currentImport
                || String(state.currentImport.id) !== importId
                || !state.reviewRecord
                || Number(state.reviewRecord.id) !== recordId
            ) {
                return;
            }
            renderRelationshipCandidates(record, data);
        } catch (error) {
            if (requestSerial !== state.relationshipRequestSerial) {
                return;
            }
            state.allowedReviewRelatedIds = new Set();
            updateRelationshipCandidatePagination(null);
            const unavailable = createElement(
                "option",
                "",
                "No se pudieron cargar los candidatos",
            );
            unavailable.value = "";
            relationSelect.replaceChildren(unavailable);
            byId("review-related-hint").textContent = friendlyError(error);
            setAlert("review-error", "danger", friendlyError(error));
        }
    }

    function changeRelationshipCandidatePage(delta) {
        if (!state.reviewRecord) {
            return;
        }
        const pagination = state.relationshipCandidatesPagination;
        const current = Number(pagination && pagination.page) || 1;
        const pages = Number(pagination && pagination.pages) || 0;
        const next = current + delta;
        if (next < 1 || next > pages) {
            return;
        }
        loadRelationshipCandidates(state.reviewRecord, next).catch((error) => {
            setAlert("review-error", "danger", friendlyError(error));
        });
    }

    function openReviewModal(record, issueCodes) {
        if (!CAN_REVIEW || !record) {
            return;
        }
        state.reviewRecord = record;
        state.reviewIssueCodes = new Set(issueCodes || []);
        const cachedCodes = issueCodesByRecord.get(Number(record.id));
        if (cachedCodes) {
            cachedCodes.forEach((code) => state.reviewIssueCodes.add(code));
        }
        state.allowedReviewProductIds = new Set();
        state.allowedReviewRelatedIds = new Set();
        state.relationshipCandidatesPage = 1;
        state.relationshipCandidatesPagination = null;
        state.relationshipRequestSerial += 1;
        hideAlert("review-error");
        setText(
            "review-record-context",
            `Fila ${safeText(record.source_row_number)} · ${safeText(record.product_code)} · ${safeText(record.record_type)}`,
        );

        const productSelect = byId("review-product-id");
        const productOptions = [];
        const blankProduct = createElement("option", "", "No cambiar el enlace de producto");
        blankProduct.value = "";
        productOptions.push(blankProduct);
        [
            [record.product_id, "Producto enlazado"],
            [record.suggested_product_id, "Producto sugerido por la API"],
        ].forEach(([id, label]) => {
            const number = Number(id);
            if (
                !Number.isInteger(number)
                || number < 1
                || state.allowedReviewProductIds.has(number)
            ) {
                return;
            }
            state.allowedReviewProductIds.add(number);
            const option = createElement("option", "", `${label} #${number}`);
            option.value = String(number);
            productOptions.push(option);
        });
        productSelect.replaceChildren(...productOptions);
        productSelect.disabled = state.allowedReviewProductIds.size === 0;
        byId("review-product-hint").textContent = state.allowedReviewProductIds.size > 0
            ? "La lista contiene únicamente IDs presentes en el payload del registro."
            : "El contrato no entregó IDs candidatos para esta fila; no se permite escribir uno manualmente.";

        const relationGroup = byId("review-relationship-group");
        const relationSelect = byId("review-related-record-id");
        const blankRelation = createElement("option", "", "No cambiar la relación");
        blankRelation.value = "";
        relationSelect.replaceChildren(blankRelation);
        relationSelect.disabled = true;
        relationGroup.classList.toggle("d-none", record.record_type === "sale");
        byId("review-related-page-label").textContent = "Página —";
        byId("review-related-prev-page").disabled = true;
        byId("review-related-next-page").disabled = true;
        byId("review-related-hint").textContent = record.record_type === "sale"
            ? "Las ventas no requieren una relación anterior."
            : "Consultando candidatos válidos en el servidor…";

        const flags = getRecordFlags(record);
        setReviewCheckVisibility(
            "review-weak-wrap",
            "review-weak-duplicate",
            record.fingerprint_strength === "weak",
            flags.weak_duplicate,
        );
        setReviewCheckVisibility(
            "review-inactive-wrap",
            "review-inactive-product",
            record.match_status === "inactive_review"
                || state.reviewIssueCodes.has("product_inactive"),
            flags.inactive_product,
        );
        setReviewCheckVisibility(
            "review-negative-wrap",
            "review-negative-net",
            record.record_type === "correction"
                && state.reviewIssueCodes.has("negative_net_demand"),
            flags.negative_net,
        );
        setReviewCheckVisibility(
            "review-relationship-wrap",
            "review-relationship",
            false,
            false,
        );

        const modal = getModal("review-modal");
        if (modal) {
            modal.show();
        }
        loadRelationshipCandidates(record, 1).catch((error) => {
            setAlert("review-error", "danger", friendlyError(error));
        });
    }

    function checkedAndVisible(wrapId, inputId) {
        const wrap = byId(wrapId);
        const input = byId(inputId);
        return Boolean(wrap && input && !wrap.classList.contains("d-none") && input.checked);
    }

    async function submitReview(event) {
        event.preventDefault();
        if (!CAN_REVIEW || !state.currentImport || !state.reviewRecord) {
            return;
        }
        hideAlert("review-error");
        const payload = {};
        const approvals = [];
        const productValue = byId("review-product-id").value;
        if (productValue) {
            const productId = Number(productValue);
            if (!state.allowedReviewProductIds.has(productId)) {
                setAlert("review-error", "danger", "El producto seleccionado no pertenece a la lista permitida.");
                return;
            }
            payload.product_id = productId;
        }
        const relatedValue = byId("review-related-record-id").value;
        if (relatedValue) {
            const relatedId = Number(relatedValue);
            if (!state.allowedReviewRelatedIds.has(relatedId)) {
                setAlert("review-error", "danger", "La relación seleccionada no pertenece a la lista permitida.");
                return;
            }
            payload.related_record_id = relatedId;
        }
        if (checkedAndVisible("review-weak-wrap", "review-weak-duplicate")) {
            approvals.push("weak_duplicate");
        }
        if (checkedAndVisible("review-inactive-wrap", "review-inactive-product")) {
            approvals.push("inactive_product");
        }
        if (checkedAndVisible("review-negative-wrap", "review-negative-net")) {
            approvals.push("negative_net");
        }
        if (checkedAndVisible("review-relationship-wrap", "review-relationship")) {
            approvals.push("relationship");
        }
        if (approvals.length > 0) {
            payload.approve = approvals;
        }
        if (Object.keys(payload).length === 0) {
            setAlert("review-error", "warning", "Seleccione un candidato o una aprobación aplicable.");
            return;
        }

        const button = byId("btn-review-submit");
        setButtonBusy(button, true, "Guardando…");
        try {
            const data = await apiRequest(
                `${API_BASE}/${encodeURIComponent(state.currentImport.id)}/records/${encodeURIComponent(state.reviewRecord.id)}/review`,
                { method: "POST", json: payload },
            );
            confirmationTokens.delete(state.currentImport.id);
            if (data.record) {
                recordCache.set(Number(data.record.id), data.record);
            }
            const modal = getModal("review-modal");
            if (modal) {
                modal.hide();
            }
            setAlert("detail-alert", "success", data.message || "Revisión guardada.", true);
            await loadImportDetail();
            if (state.currentTab === "records" || state.currentTab === "pending") {
                await loadRecords(state.currentTab);
            } else {
                await loadErrors(state.currentTab);
            }
            announce("Revisión manual guardada; ejecute la simulación nuevamente.");
        } catch (error) {
            setAlert(
                "review-error",
                error.status === 409 || error.status === 422 ? "warning" : "danger",
                friendlyError(error),
            );
        } finally {
            setButtonBusy(button, false, "", "Guardar revisión");
        }
    }

    function validateCsvFile(file) {
        if (!(file instanceof File)) {
            throw new Error("Seleccione un archivo CSV.");
        }
        if (!file.name.toLocaleLowerCase("es").endsWith(".csv")) {
            throw new Error("Solo se aceptan archivos con extensión .csv; XLSX no está permitido.");
        }
        if (file.size <= 0) {
            throw new Error("El archivo CSV está vacío.");
        }
        if (file.size > MAX_FILE_BYTES) {
            throw new Error("El archivo excede el máximo de 10 MiB.");
        }
    }

    function parseCsvHeaderRecord(text) {
        const headers = [];
        let value = "";
        let quoted = false;
        for (let index = 0; index < text.length; index += 1) {
            const character = text[index];
            if (character === "\"") {
                if (quoted && text[index + 1] === "\"") {
                    value += "\"";
                    index += 1;
                } else if (value.length === 0 || quoted) {
                    quoted = !quoted;
                } else {
                    throw new Error("El encabezado CSV contiene comillas inválidas.");
                }
            } else if (character === ";" && !quoted) {
                headers.push(value.trim());
                value = "";
            } else if ((character === "\n" || character === "\r") && !quoted) {
                headers.push(value.trim());
                break;
            } else {
                value += character;
            }
        }
        if (quoted) {
            throw new Error("El encabezado CSV tiene una comilla sin cerrar.");
        }
        if (headers.length === 0 || (headers.length === 1 && value)) {
            headers.push(value.trim());
        } else if (text.length > 0 && !text.includes("\n") && !text.includes("\r")) {
            headers.push(value.trim());
        }
        const cleaned = uniqueHeaders(headers);
        if (cleaned.length !== headers.length || cleaned.some((header) => !header)) {
            throw new Error("El CSV contiene encabezados vacíos o duplicados.");
        }
        const foldedHeaders = cleaned.map((header) => header.toLocaleLowerCase("es"));
        if (new Set(foldedHeaders).size !== foldedHeaders.length) {
            throw new Error("El CSV contiene encabezados duplicados o ambiguos.");
        }
        if (cleaned.length > 40) {
            throw new Error("El CSV excede el máximo de 40 columnas.");
        }
        if (cleaned.some((header) => header.length > 4096)) {
            throw new Error("Un encabezado excede el máximo de 4096 caracteres.");
        }
        if (cleaned.some((header) => /[\p{Cc}\p{Cf}\p{Cs}]/u.test(header))) {
            throw new Error("El CSV contiene controles Unicode no permitidos en sus encabezados.");
        }
        if (cleaned.some((header) => /^[=+\-@]/u.test(header))) {
            throw new Error("El CSV contiene un encabezado con prefijo no permitido.");
        }
        return cleaned;
    }

    async function inspectSelectedFile(file) {
        validateCsvFile(file);
        const bytes = new Uint8Array(await file.arrayBuffer());
        if (bytes.length < 3 || bytes[0] !== 0xef || bytes[1] !== 0xbb || bytes[2] !== 0xbf) {
            throw new Error("El CSV debe estar codificado como UTF-8 con BOM.");
        }
        let text;
        try {
            text = new TextDecoder("utf-8", { fatal: true }).decode(bytes.slice(3));
        } catch (_error) {
            throw new Error("El archivo no contiene texto UTF-8 válido.");
        }
        const headers = parseCsvHeaderRecord(text);
        if (headers.length === 0) {
            throw new Error("El CSV no contiene encabezados.");
        }
        return headers;
    }

    async function handleFileSelection() {
        const input = byId("upload-file");
        const status = byId("upload-file-status");
        state.pendingUploadHeaders = null;
        status.className = "small mt-2 text-secondary";
        status.textContent = "";
        if (!input.files || input.files.length === 0) {
            return;
        }
        const file = input.files[0];
        status.textContent = "Validando archivo y encabezados…";
        try {
            const headers = await inspectSelectedFile(file);
            state.pendingUploadHeaders = headers;
            status.className = "small mt-2 text-success";
            status.textContent = `${file.name} · ${formatBytes(file.size)} · ${headers.length} encabezados detectados`;
        } catch (error) {
            input.value = "";
            status.className = "small mt-2 text-danger";
            status.textContent = error.message;
        }
    }

    function openUploadModal() {
        const form = byId("upload-form");
        if (form) {
            form.reset();
        }
        state.pendingUploadHeaders = null;
        hideAlert("upload-error");
        setText("upload-file-status", "");
        const modal = getModal("upload-modal");
        if (modal) {
            modal.show();
        }
    }

    async function uploadImport(event) {
        event.preventDefault();
        if (!CAN_UPLOAD) {
            return;
        }
        hideAlert("upload-error");
        const input = byId("upload-file");
        const file = input.files && input.files[0];
        const sourceSystem = byId("upload-source-system").value.trim();
        const documentType = byId("upload-document-type").value.trim();
        try {
            validateCsvFile(file);
            if (!state.pendingUploadHeaders) {
                state.pendingUploadHeaders = await inspectSelectedFile(file);
            }
            if (!sourceSystem) {
                throw new Error("El sistema de origen es obligatorio.");
            }
            if (sourceSystem.length > 100) {
                throw new Error("El sistema de origen no puede exceder 100 caracteres.");
            }
            if (documentType.length > 50) {
                throw new Error("El tipo documental no puede exceder 50 caracteres.");
            }
        } catch (error) {
            setAlert("upload-error", "warning", error.message);
            return;
        }

        const formData = new FormData();
        formData.append("file", file, file.name);
        formData.append("source_system", sourceSystem);
        if (documentType) {
            formData.append("document_type", documentType);
        }

        const button = byId("btn-upload-submit");
        setButtonBusy(button, true, "Guardando lote…");
        try {
            const data = await apiRequest(`${API_BASE}/upload`, {
                method: "POST",
                formData,
            });
            const item = data.historical_import;
            if (!item || !item.id) {
                throw new HttpError(500, "El servidor no devolvió el lote creado.");
            }
            headerAllowlists.set(item.id, [...state.pendingUploadHeaders]);
            const modal = getModal("upload-modal");
            if (modal) {
                modal.hide();
            }
            state.importsPage = 1;
            await loadImports();
            setAlert("page-alert", "success", data.message || "Lote guardado correctamente.", true);
            await openImportDetail(item.id);
        } catch (error) {
            setAlert(
                "upload-error",
                error.status === 409 || error.status === 413 || error.status === 422
                    ? "warning"
                    : "danger",
                friendlyError(error),
            );
        } finally {
            setButtonBusy(button, false, "", "Guardar lote");
        }
    }

    async function downloadCsv(path, filename) {
        try {
            const result = await apiRequest(path, {
                accept: "text/csv",
                responseType: "blob",
            });
            const objectUrl = URL.createObjectURL(result.body);
            const link = createElement("a");
            link.href = objectUrl;
            link.download = filename;
            link.rel = "noopener";
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
            announce(`Descarga preparada: ${filename}.`);
        } catch (error) {
            const target = state.currentImport ? "detail-alert" : "page-alert";
            setAlert(target, error.status === 401 ? "warning" : "danger", friendlyError(error), true);
        }
    }

    function changePage(kind, delta) {
        const pagination = kind === "imports"
            ? state.importsPagination
            : state.paginations[kind];
        const current = Number(pagination && pagination.page) || 1;
        const pages = Number(pagination && pagination.pages) || 0;
        const next = current + delta;
        if (next < 1 || next > pages) {
            return;
        }
        if (kind === "imports") {
            state.importsPage = next;
            loadImports().catch(() => {});
            return;
        }
        state.pages[kind] = next;
        const loader = kind === "records" || kind === "pending"
            ? loadRecords(kind)
            : loadErrors(kind);
        loader.catch((error) => {
            setAlert("detail-alert", "danger", friendlyError(error), true);
        });
    }

    function bindEvents() {
        byId("imports-filter-form").addEventListener("submit", (event) => {
            event.preventDefault();
            state.importsPage = 1;
            loadImports().catch(() => {});
        });
        byId("btn-refresh-imports").addEventListener("click", () => {
            loadImports().catch(() => {});
        });
        byId("imports-prev-page").addEventListener("click", () => changePage("imports", -1));
        byId("imports-next-page").addEventListener("click", () => changePage("imports", 1));
        byId("btn-back-to-imports").addEventListener("click", closeImportDetail);
        byId("btn-refresh-detail").addEventListener("click", () => {
            loadImportDetail()
                .then(() => {
                    if (state.currentTab === "records" || state.currentTab === "pending") {
                        return loadRecords(state.currentTab);
                    }
                    return loadErrors(state.currentTab);
                })
                .catch((error) => {
                    setAlert("detail-alert", "danger", friendlyError(error), true);
                });
        });

        const tabButtons = Array.from(document.querySelectorAll("[data-historical-tab]"));
        tabButtons.forEach((button, index) => {
            button.addEventListener("click", () => activateTab(button.dataset.historicalTab));
            button.addEventListener("keydown", (event) => {
                let targetIndex = null;
                if (event.key === "ArrowRight") {
                    targetIndex = (index + 1) % tabButtons.length;
                } else if (event.key === "ArrowLeft") {
                    targetIndex = (index - 1 + tabButtons.length) % tabButtons.length;
                } else if (event.key === "Home") {
                    targetIndex = 0;
                } else if (event.key === "End") {
                    targetIndex = tabButtons.length - 1;
                }
                if (targetIndex === null) {
                    return;
                }
                event.preventDefault();
                const target = tabButtons[targetIndex];
                activateTab(target.dataset.historicalTab);
                target.focus();
            });
        });

        ["records", "errors", "pending", "duplicates"].forEach((kind) => {
            byId(`${kind}-prev-page`).addEventListener("click", () => changePage(kind, -1));
            byId(`${kind}-next-page`).addEventListener("click", () => changePage(kind, 1));
        });

        byId("errors-filter-form").addEventListener("submit", (event) => {
            event.preventDefault();
            state.pages.errors = 1;
            loadErrors("errors").catch((error) => {
                setAlert("detail-alert", "danger", friendlyError(error), true);
            });
        });
        byId("pending-match-status").addEventListener("change", () => {
            state.pages.pending = 1;
            loadRecords("pending").catch((error) => {
                setAlert("detail-alert", "danger", friendlyError(error), true);
            });
        });

        const mappingForm = byId("mapping-form");
        if (mappingForm && CAN_REVIEW) {
            mappingForm.addEventListener("submit", (event) => {
                event.preventDefault();
                runPreview(byId("btn-save-preview")).catch(() => {});
            });
        }
        const previewButton = byId("btn-preview-import");
        if (previewButton) {
            previewButton.addEventListener("click", () => {
                runPreview(previewButton).catch(() => {});
            });
        }
        const dryRunButton = byId("btn-dry-run");
        if (dryRunButton) {
            dryRunButton.addEventListener("click", () => {
                runDryRun().catch(() => {});
            });
        }

        if (CAN_UPLOAD) {
            byId("btn-new-import").addEventListener("click", openUploadModal);
            byId("upload-file").addEventListener("change", () => {
                handleFileSelection().catch((error) => {
                    setAlert("upload-error", "danger", error.message);
                });
            });
            byId("upload-form").addEventListener("submit", (event) => {
                uploadImport(event).catch((error) => {
                    setAlert("upload-error", "danger", friendlyError(error));
                });
            });
        }

        if (CAN_CONFIRM) {
            byId("btn-confirm-import").addEventListener("click", openConfirmModal);
            byId("confirm-form").addEventListener("submit", (event) => {
                confirmImport(event).catch((error) => {
                    setAlert("confirm-error", "danger", friendlyError(error));
                });
            });
        }

        if (CAN_REVERT) {
            byId("btn-revert-import").addEventListener("click", openRevertModal);
            byId("revert-form").addEventListener("submit", (event) => {
                revertImport(event).catch((error) => {
                    setAlert("revert-error", "danger", friendlyError(error));
                });
            });
            byId("revert-reason").addEventListener("input", (event) => {
                byId("revert-reason-count").textContent = String(event.target.value.length);
            });
        }

        if (CAN_REVIEW) {
            byId("review-form").addEventListener("submit", (event) => {
                submitReview(event).catch((error) => {
                    setAlert("review-error", "danger", friendlyError(error));
                });
            });
            byId("review-related-prev-page").addEventListener("click", () => {
                changeRelationshipCandidatePage(-1);
            });
            byId("review-related-next-page").addEventListener("click", () => {
                changeRelationshipCandidatePage(1);
            });
        }

        if (CAN_EXPORT) {
            byId("btn-download-template").addEventListener("click", () => {
                downloadCsv(`${API_BASE}/template.csv`, "historical-import-template-v1.csv");
            });
            byId("btn-export-errors").addEventListener("click", () => {
                if (!state.currentImport || !state.currentImport.id) {
                    return;
                }
                const shortId = String(state.currentImport.id).slice(0, 8);
                downloadCsv(
                    `${API_BASE}/${encodeURIComponent(state.currentImport.id)}/errors.csv`,
                    `historical-import-errors-${shortId}.csv`,
                );
            });
        }
    }

    function initialize() {
        bindEvents();
        renderMapping({ id: "", status: "uploaded" });
        loadImports().catch(() => {});
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize, { once: true });
    } else {
        initialize();
    }
})();

