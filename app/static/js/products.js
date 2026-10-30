/* Página de productos: listar, buscar, filtrar, crear, editar y
   activar/desactivar productos contra /api/products.

   Reglas:
   - fetch con credentials: "same-origin" (sesión de Flask-Login).
   - El stock NUNCA se edita aquí: solo se define al crear (regla del backend).
   - window.CAN_WRITE (inyectado por Jinja) decide si se muestran acciones de
     escritura; la API valida permisos igualmente.
   - Render con textContent / createElement para evitar XSS. */

(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);
    const CAN_WRITE = window.CAN_WRITE === true;
    const TABLE_COLS = 9;

    const IMAGE_MAX_BYTES = 2 * 1024 * 1024; // 2 MB (mismo límite del backend)
    const IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"];

    let categories = [];
    let editingId = null; // null = creando; número = editando
    let pendingImageFile = null;   // archivo elegido, se sube tras guardar
    let removeImageFlag = false;   // el usuario pidió quitar la imagen actual
    let previewObjectUrl = null;   // URL temporal del preview (se revoca)

    // ------------------------------------------------------------------
    // Formateadores
    // ------------------------------------------------------------------
    const moneyFormat = new Intl.NumberFormat("es-VE", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
    const intFormat = new Intl.NumberFormat("es-VE");
    const fmtMoney = (v) => moneyFormat.format(Number(v || 0));
    const fmtInt = (v) => intFormat.format(Number(v || 0));

    // ------------------------------------------------------------------
    // Helper de API (GET/POST/PUT/DELETE con JSON)
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
        if (body instanceof FormData) {
            options.body = body; // el navegador arma el multipart con boundary
        } else if (body !== null) {
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
            // Respuesta sin cuerpo JSON: data queda vacío.
        }
        if (!response.ok) {
            throw new ApiError(response.status, data.error || `Error HTTP ${response.status}.`);
        }
        return data;
    }

    /* Mensaje amigable según el código HTTP. */
    function errorMessage(error) {
        if (!(error instanceof ApiError)) return "Error inesperado.";
        switch (error.status) {
            case 0: return error.message;
            case 401: return "Su sesión expiró. Vuelva a iniciar sesión.";
            case 403: return "No tiene permisos para realizar esta acción.";
            case 404: return error.message || "Recurso no encontrado.";
            case 500: return "Error interno del servidor. Intente de nuevo.";
            default: return error.message; // 400 validación / 409 conflicto
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

    /* Errores 401/403 se tratan como problema de página completa. */
    function handlePageError(error) {
        if (error.status === 401) {
            showAlert("warning", "Su sesión expiró.", true);
        } else {
            showAlert("danger", errorMessage(error));
        }
    }

    // ------------------------------------------------------------------
    // Modal (Bootstrap incluido por Tabler)
    // ------------------------------------------------------------------
    let modalInstance = null;

    function getModal() {
        if (!modalInstance) {
            modalInstance = new bootstrap.Modal($("product-modal"));
        }
        return modalInstance;
    }

    const openModal = () => getModal().show();
    const closeModal = () => getModal().hide();

    function showFormError(message) {
        const box = $("product-form-error");
        box.textContent = message;
        box.classList.remove("d-none");
    }

    function hideFormError() {
        $("product-form-error").classList.add("d-none");
    }

    // ------------------------------------------------------------------
    // Render de tabla
    // ------------------------------------------------------------------
    function stateRow(content) {
        const tr = document.createElement("tr");
        tr.className = "table-state-row";
        const td = document.createElement("td");
        td.colSpan = TABLE_COLS;
        if (typeof content === "string") {
            td.textContent = content;
        } else {
            td.appendChild(content);
        }
        tr.appendChild(td);
        return tr;
    }

    function showLoading(tbody) {
        const wrap = document.createElement("div");
        const spinner = document.createElement("span");
        spinner.className = "spinner-border spinner-border-sm me-2";
        spinner.setAttribute("role", "status");
        wrap.appendChild(spinner);
        wrap.append("Cargando productos…");
        tbody.replaceChildren(stateRow(wrap));
    }

    function badge(text, className) {
        const span = document.createElement("span");
        span.className = `badge ${className}`;
        span.textContent = text;
        return span;
    }

    function iconButton(iconClass, title, btnClass, onClick) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = `btn btn-sm ${btnClass}`;
        btn.title = title;
        btn.setAttribute("aria-label", title);
        const icon = document.createElement("i");
        icon.className = `ti ${iconClass}`;
        btn.appendChild(icon);
        btn.addEventListener("click", onClick);
        return btn;
    }

    function renderProducts(items) {
        const tbody = $("products-tbody");

        if (!items.length) {
            tbody.replaceChildren(stateRow(
                "Sin productos para los filtros seleccionados."
            ));
            return;
        }

        const rows = items.map((p) => {
            const tr = document.createElement("tr");
            if (!p.is_active) tr.className = "row-inactive";

            // Miniatura (o placeholder)
            const tdThumb = document.createElement("td");
            const thumb = document.createElement("span");
            thumb.className = "product-thumb";
            if (p.image_url) {
                const img = document.createElement("img");
                img.src = p.image_url;
                img.alt = p.name || "Producto";
                img.loading = "lazy";
                thumb.appendChild(img);
            } else {
                const icon = document.createElement("i");
                icon.className = "ti ti-photo";
                thumb.appendChild(icon);
            }
            tdThumb.appendChild(thumb);
            tr.appendChild(tdThumb);

            // Código
            const tdCode = document.createElement("td");
            tdCode.textContent = p.code || "";
            tr.appendChild(tdCode);

            // Nombre (+ descripción secundaria)
            const tdName = document.createElement("td");
            const title = document.createElement("div");
            title.className = "cell-title";
            title.textContent = p.name || "";
            tdName.appendChild(title);
            if (p.description) {
                const sub = document.createElement("div");
                sub.className = "cell-sub";
                sub.textContent = p.description;
                sub.title = p.description;
                tdName.appendChild(sub);
            }
            tr.appendChild(tdName);

            // Categoría
            const tdCat = document.createElement("td");
            tdCat.textContent = p.category || "Sin categoría";
            tr.appendChild(tdCat);

            // Stock actual / mínimo
            const tdStock = document.createElement("td");
            tdStock.className = "text-end cell-num";
            tdStock.textContent = `${fmtInt(p.current_stock)} ${p.unit || ""}`.trim();
            tr.appendChild(tdStock);

            const tdMin = document.createElement("td");
            tdMin.className = "text-end cell-num";
            tdMin.textContent = fmtInt(p.minimum_stock);
            tr.appendChild(tdMin);

            // Precio de venta
            const tdPrice = document.createElement("td");
            tdPrice.className = "text-end cell-num";
            tdPrice.textContent = fmtMoney(p.sale_price);
            tr.appendChild(tdPrice);

            // Estado (badges)
            const tdState = document.createElement("td");
            if (p.is_active) {
                tdState.appendChild(badge("Activo", "bg-green-lt"));
            } else {
                tdState.appendChild(badge("Inactivo", "bg-secondary-lt"));
            }
            if (p.is_active && p.is_low_stock) {
                tdState.append(" ");
                tdState.appendChild(badge("Bajo stock", "badge-low-stock"));
            }
            tr.appendChild(tdState);

            // Acciones
            const tdActions = document.createElement("td");
            tdActions.className = "text-nowrap";
            if (CAN_WRITE) {
                tdActions.appendChild(iconButton(
                    "ti-edit", "Editar producto", "btn-outline-primary me-1",
                    () => openEdit(p)
                ));
                if (p.is_active) {
                    tdActions.appendChild(iconButton(
                        "ti-ban", "Desactivar producto", "btn-outline-danger",
                        () => deactivateProduct(p)
                    ));
                } else {
                    tdActions.appendChild(iconButton(
                        "ti-circle-check", "Reactivar producto", "btn-outline-success",
                        () => reactivateProduct(p)
                    ));
                }
            } else {
                tdActions.textContent = "—";
            }
            tr.appendChild(tdActions);

            return tr;
        });

        tbody.replaceChildren(...rows);
    }

    // ------------------------------------------------------------------
    // Filtros
    // ------------------------------------------------------------------
    function getFilters() {
        const status = $("f-status").value;
        return {
            search: $("f-search").value,
            category_id: $("f-category").value,
            low_stock: $("f-low-stock").checked ? "1" : "",
            include_inactive: status === "active" ? "" : "1",
            _status: status,
        };
    }

    // ------------------------------------------------------------------
    // Cargas
    // ------------------------------------------------------------------
    async function loadCategories() {
        const data = await apiFetch("/api/categories");
        categories = data.items || [];

        const options = categories.map((c) => {
            const opt = document.createElement("option");
            opt.value = String(c.id);
            opt.textContent = c.name;
            return opt;
        });

        const filterSelect = $("f-category");
        const keepFilter = filterSelect.value;
        filterSelect.replaceChildren(filterSelect.options[0], ...options.map((o) => o.cloneNode(true)));
        filterSelect.value = keepFilter;

        // El modal solo existe para roles con permiso de escritura.
        const modalSelect = $("p-category");
        if (modalSelect) {
            const usableIds = new Set(categories.filter(c => c.is_active && c.approval_status === 'approved').map(c => String(c.id)));
            modalSelect.replaceChildren(modalSelect.options[0], ...options.filter(o => usableIds.has(o.value)));
        }
    }

    async function loadProducts() {
        const tbody = $("products-tbody");
        showLoading(tbody);
        $("products-count").textContent = "Cargando…";

        const filters = getFilters();
        try {
            const data = await apiFetch("/api/products", {
                params: {
                    search: filters.search,
                    category_id: filters.category_id,
                    low_stock: filters.low_stock,
                    include_inactive: filters.include_inactive,
                },
            });

            let items = data.items || [];
            if (filters._status === "inactive") {
                items = items.filter((p) => !p.is_active);
            }

            renderProducts(items);
            $("products-count").textContent =
                items.length === 1 ? "1 producto" : `${fmtInt(items.length)} productos`;
        } catch (error) {
            console.error("Error al cargar productos:", error);
            $("products-count").textContent = "Error al cargar";
            tbody.replaceChildren(stateRow(errorMessage(error)));
            handlePageError(error);
        }
    }

    // ------------------------------------------------------------------
    // Imagen del producto (preview y validación en el modal)
    // ------------------------------------------------------------------
    let currentImageUrl = null; // imagen ya guardada del producto en edición

    function setPreview(src) {
        const holder = $("p-image-preview");
        holder.replaceChildren();
        if (src) {
            const img = document.createElement("img");
            img.src = src;
            img.alt = "Vista previa de la imagen";
            holder.appendChild(img);
        } else {
            const icon = document.createElement("i");
            icon.className = "ti ti-photo";
            holder.appendChild(icon);
        }
    }

    function revokePreview() {
        if (previewObjectUrl) {
            URL.revokeObjectURL(previewObjectUrl);
            previewObjectUrl = null;
        }
    }

    /* Validación rápida en frontend; el backend revalida (extensión,
       contenido real y tamaño). Devuelve mensaje de error o null. */
    function validateImageFile(file) {
        const ext = (file.name.split(".").pop() || "").toLowerCase();
        if (!IMAGE_EXTENSIONS.includes(ext)) {
            return `Formato '.${ext}' no permitido. Use jpg, jpeg, png o webp.`;
        }
        if (file.size > IMAGE_MAX_BYTES) {
            const mb = (file.size / (1024 * 1024)).toFixed(1);
            return `La imagen pesa ${mb} MB y el máximo permitido es 2 MB.`;
        }
        return null;
    }

    function resetImageState() {
        pendingImageFile = null;
        removeImageFlag = false;
        currentImageUrl = null;
        revokePreview();
        $("p-image").value = "";
        setPreview(null);
        $("p-image-remove").classList.add("d-none");
    }

    // ------------------------------------------------------------------
    // Crear / editar
    // ------------------------------------------------------------------
    function resetForm() {
        hideFormError();
        $("p-id").value = "";
        $("p-code").value = "";
        $("p-name").value = "";
        $("p-category").value = "";
        $("p-unit").value = "unidad";
        $("p-minimum-stock").value = "0";
        $("p-purchase-price").value = "0";
        $("p-sale-price").value = "0";
        $("p-current-stock").value = "0";
        $("p-description").value = "";
        $("p-is-active").checked = true;
        resetImageState();
    }

    function openCreate() {
        editingId = null;
        resetForm();
        $("product-modal-title").textContent = "Nuevo producto";
        $("p-stock-group").classList.remove("d-none");
        openModal();
    }

    function openEdit(product) {
        editingId = product.id;
        resetForm();
        $("product-modal-title").textContent = `Editar producto — ${product.code}`;
        $("p-id").value = String(product.id);
        $("p-code").value = product.code || "";
        $("p-name").value = product.name || "";
        $("p-category").value = product.category_id ? String(product.category_id) : "";
        $("p-unit").value = product.unit || "unidad";
        $("p-minimum-stock").value = String(product.minimum_stock ?? 0);
        $("p-purchase-price").value = String(product.purchase_price ?? 0);
        $("p-sale-price").value = String(product.sale_price ?? 0);
        $("p-description").value = product.description || "";
        $("p-is-active").checked = Boolean(product.is_active);
        // El stock no se edita: se mueve por inventario (regla del backend).
        $("p-stock-group").classList.add("d-none");
        // Imagen actual del producto (si tiene).
        currentImageUrl = product.image_url || null;
        if (currentImageUrl) {
            setPreview(currentImageUrl);
            $("p-image-remove").classList.remove("d-none");
        }
        openModal();
    }

    function validateForm() {
        const errors = [];
        if (!$("p-code").value.trim()) errors.push("El código es obligatorio.");
        if (!$("p-name").value.trim()) errors.push("El nombre es obligatorio.");
        if (!$("p-category").value) errors.push("Debe seleccionar una categoría.");
        return errors;
    }

    async function submitForm(event) {
        event.preventDefault();
        hideFormError();

        const errors = validateForm();
        if (errors.length) {
            showFormError(errors.join(" "));
            return;
        }

        const payload = {
            code: $("p-code").value.trim(),
            name: $("p-name").value.trim(),
            description: $("p-description").value.trim(),
            category_id: Number($("p-category").value),
            unit: $("p-unit").value.trim() || "unidad",
            minimum_stock: Number($("p-minimum-stock").value || 0),
            purchase_price: Number($("p-purchase-price").value || 0),
            sale_price: Number($("p-sale-price").value || 0),
            is_active: $("p-is-active").checked,
        };

        const saveBtn = $("product-save-btn");
        saveBtn.disabled = true;

        // Paso 1: guardar el producto (JSON). Si falla, el modal sigue abierto.
        let saved;
        try {
            if (editingId === null) {
                // El stock inicial solo se acepta al crear.
                payload.current_stock = Number($("p-current-stock").value || 0);
                saved = await apiFetch("/api/products", { method: "POST", body: payload });
            } else {
                saved = await apiFetch(`/api/products/${editingId}`, {
                    method: "PUT",
                    body: payload,
                });
            }
        } catch (error) {
            console.error("Error al guardar producto:", error);
            if (error.status === 401) {
                showFormError("Su sesión expiró. Vuelva a iniciar sesión en /login.");
            } else {
                showFormError(errorMessage(error));
            }
            saveBtn.disabled = false;
            return;
        }

        // Paso 2: imagen (subir nueva o quitar la actual), sin repetir el guardado.
        let alertKind = "success";
        let alertMsg = editingId === null
            ? `Producto '${saved.name}' creado correctamente.`
            : `Producto '${saved.name}' actualizado correctamente.`;
        try {
            if (pendingImageFile) {
                const formData = new FormData();
                formData.append("image", pendingImageFile);
                await apiFetch(`/api/products/${saved.id}/image`, {
                    method: "POST",
                    body: formData,
                });
                alertMsg += " Imagen guardada.";
            } else if (removeImageFlag && currentImageUrl) {
                await apiFetch(`/api/products/${saved.id}/image`, { method: "DELETE" });
                alertMsg += " Imagen eliminada.";
            }
        } catch (error) {
            console.error("Error con la imagen del producto:", error);
            alertKind = "warning";
            alertMsg += ` El producto se guardó, pero la imagen no: ${errorMessage(error)}`;
        }

        saveBtn.disabled = false;
        closeModal();
        showAlert(alertKind, alertMsg);
        await loadProducts();
    }

    // ------------------------------------------------------------------
    // Desactivar / reactivar
    // ------------------------------------------------------------------
    async function deactivateProduct(product) {
        const ok = window.confirm(
            `¿Desactivar el producto '${product.name}' (${product.code})?\n` +
            "No se elimina: deja de aparecer en listados activos."
        );
        if (!ok) return;

        hideAlert();
        try {
            const data = await apiFetch(`/api/products/${product.id}`, { method: "DELETE" });
            showAlert("success", data.message || "Producto desactivado correctamente.");
            await loadProducts();
        } catch (error) {
            console.error("Error al desactivar producto:", error);
            handlePageError(error);
        }
    }

    async function reactivateProduct(product) {
        hideAlert();
        try {
            const data = await apiFetch(`/api/products/${product.id}`, {
                method: "PUT",
                body: { is_active: true },
            });
            showAlert("success", `Producto '${data.name}' reactivado correctamente.`);
            await loadProducts();
        } catch (error) {
            console.error("Error al reactivar producto:", error);
            handlePageError(error);
        }
    }

    // ------------------------------------------------------------------
    // Eventos e inicio
    // ------------------------------------------------------------------
    $("filters-form").addEventListener("submit", (event) => {
        event.preventDefault();
        hideAlert();
        loadProducts();
    });

    $("btn-clear-filters").addEventListener("click", () => {
        $("f-search").value = "";
        $("f-category").value = "";
        $("f-status").value = "active";
        $("f-low-stock").checked = false;
        hideAlert();
        loadProducts();
    });

    // Selects y checkbox aplican el filtro al cambiar (sin pulsar Aplicar).
    ["f-category", "f-status", "f-low-stock"].forEach((id) => {
        $(id).addEventListener("change", () => {
            hideAlert();
            loadProducts();
        });
    });

    if (CAN_WRITE) {
        $("btn-new-product").addEventListener("click", openCreate);
        $("product-form").addEventListener("submit", submitForm);

        // Selección de archivo: valida y muestra preview local.
        $("p-image").addEventListener("change", () => {
            hideFormError();
            revokePreview();
            const file = $("p-image").files[0] || null;
            if (!file) {
                pendingImageFile = null;
                setPreview(removeImageFlag ? null : currentImageUrl);
                return;
            }
            const error = validateImageFile(file);
            if (error) {
                showFormError(error);
                $("p-image").value = "";
                pendingImageFile = null;
                setPreview(removeImageFlag ? null : currentImageUrl);
                return;
            }
            pendingImageFile = file;
            removeImageFlag = false;
            previewObjectUrl = URL.createObjectURL(file);
            setPreview(previewObjectUrl);
        });

        // Quitar la imagen actual (se aplica al guardar).
        $("p-image-remove").addEventListener("click", () => {
            removeImageFlag = true;
            pendingImageFile = null;
            $("p-image").value = "";
            revokePreview();
            setPreview(null);
            $("p-image-remove").classList.add("d-none");
        });
    }

    async function init() {
        try {
            await loadCategories();
        } catch (error) {
            console.error("Error al cargar categorías:", error);
            handlePageError(error);
        }
        await loadProducts();
    }

    init();
})();
