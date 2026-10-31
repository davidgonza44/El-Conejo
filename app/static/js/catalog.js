/* Catálogo visual de productos.
   Módulo estrictamente de consulta: solo usa GET /api/products,
   GET /api/categories y GET /api/products/<id>.
   Búsqueda, filtros, orden y paginación los resuelve el servidor: cada página
   pide solo PAGE_SIZE productos, sin descargar el catálogo completo. */
(function () {
    "use strict";

    const PAGE_SIZE = 12;
    // Filtro de existencia de la UI -> parámetro status de /api/products.
    const STOCK_STATUS = { available: "disponible", low: "bajo_stock", out: "agotado" };
    const SEARCH_DEBOUNCE_MS = 300;
    const $ = (id) => document.getElementById(id);
    const hasField = (object, field) =>
        Object.prototype.hasOwnProperty.call(object || {}, field);

    const elements = {
        alert: $("catalog-alert"),
        count: $("catalog-count"),
        state: $("catalog-state"),
        grid: $("catalog-grid"),
        pagination: $("catalog-pagination"),
        previous: $("catalog-prev"),
        next: $("catalog-next"),
        pageIndicator: $("catalog-page-indicator"),
        search: $("catalog-search"),
        category: $("catalog-category"),
        stockStatus: $("catalog-stock-status"),
        sort: $("catalog-sort"),
        clear: $("catalog-clear-filters"),
        detailModal: $("catalog-detail-modal"),
    };

    const state = {
        page: 1,
        totalPages: 1,
        request: 0,
        priceChecked: false,
        searchTimer: null,
        detailModal: null,
    };

    const numberFormat = new Intl.NumberFormat("es-VE", {
        maximumFractionDigits: 0,
    });
    const priceFormat = new Intl.NumberFormat("es-VE", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
    const dateFormat = new Intl.DateTimeFormat("es-VE", {
        dateStyle: "medium",
        timeStyle: "short",
    });

    class ApiError extends Error {
        constructor(status, message) {
            super(message);
            this.name = "ApiError";
            this.status = status;
        }
    }

    async function apiGet(path) {
        let response;
        try {
            response = await fetch(path, {
                method: "GET",
                credentials: "same-origin",
                headers: { Accept: "application/json" },
            });
        } catch (_error) {
            throw new ApiError(0, "No fue posible conectar con el servidor.");
        }

        let data = null;
        try {
            data = await response.json();
        } catch (_error) {
            if (response.ok) {
                throw new ApiError(500, "El servidor devolvió una respuesta no válida.");
            }
        }

        if (!response.ok) {
            const apiMessage = data && typeof data.error === "string" ? data.error : "";
            throw new ApiError(response.status, apiMessage);
        }
        return data;
    }

    function errorMessage(error) {
        if (!(error instanceof ApiError)) {
            return "Ocurrió un error inesperado al cargar el catálogo.";
        }
        switch (error.status) {
            case 0:
                return "Error de red. Verifique su conexión e intente nuevamente.";
            case 400:
                return error.message || "La solicitud del catálogo no es válida.";
            case 401:
                return "Su sesión expiró. Inicie sesión nuevamente.";
            case 403:
                return "No tiene permisos para consultar el catálogo.";
            case 404:
                return error.message || "El producto solicitado no fue encontrado.";
            case 409:
                return error.message || "No fue posible completar la consulta.";
            case 500:
                return "Error interno del servidor. Intente nuevamente más tarde.";
            default:
                return error.message || `No fue posible cargar la información (${error.status}).`;
        }
    }

    function showAlert(message, kind = "danger") {
        elements.alert.className = `alert alert-${kind}`;
        elements.alert.textContent = message;
        elements.alert.classList.remove("d-none");
    }

    function hideAlert() {
        elements.alert.classList.add("d-none");
        elements.alert.textContent = "";
    }

    function setCount(message, loading = false) {
        elements.count.replaceChildren();
        if (loading) {
            const spinner = document.createElement("span");
            spinner.className = "spinner-border spinner-border-sm";
            spinner.setAttribute("aria-hidden", "true");
            elements.count.appendChild(spinner);
        }
        const text = document.createElement("span");
        text.textContent = message;
        elements.count.appendChild(text);
    }

    function createStateIcon(iconName) {
        const holder = document.createElement("span");
        holder.className = "catalog-state__icon";
        holder.setAttribute("aria-hidden", "true");
        const icon = document.createElement("i");
        icon.className = `ti ${iconName}`;
        holder.appendChild(icon);
        return holder;
    }

    function showLoading() {
        elements.grid.classList.add("d-none");
        elements.pagination.classList.add("d-none");
        elements.state.classList.remove("d-none");
        elements.state.replaceChildren();

        const spinner = document.createElement("span");
        spinner.className = "spinner-border text-primary";
        spinner.setAttribute("aria-hidden", "true");
        const label = document.createElement("span");
        label.textContent = "Cargando catálogo…";
        elements.state.append(spinner, label);
        setCount("Cargando productos…", true);
    }

    function showEmpty() {
        elements.grid.classList.add("d-none");
        elements.pagination.classList.add("d-none");
        elements.state.classList.remove("d-none");
        elements.state.replaceChildren();

        const title = document.createElement("div");
        title.className = "catalog-state__title";
        title.textContent = "Sin productos encontrados";
        const help = document.createElement("div");
        help.textContent = "Pruebe con otros términos o limpie los filtros.";
        elements.state.append(createStateIcon("ti-package-off"), title, help);
    }

    function showCatalogError(message) {
        elements.grid.classList.add("d-none");
        elements.pagination.classList.add("d-none");
        elements.state.classList.remove("d-none");
        elements.state.replaceChildren();

        const title = document.createElement("div");
        title.className = "catalog-state__title";
        title.textContent = "No se pudo cargar el catálogo";
        const text = document.createElement("div");
        text.textContent = message;
        elements.state.append(createStateIcon("ti-alert-triangle"), title, text);
        setCount("Error al cargar");
    }

    function numericValue(value) {
        const number = Number(value);
        return Number.isFinite(number) ? number : 0;
    }

    function stockState(product) {
        const current = numericValue(product.current_stock);
        const minimum = numericValue(product.minimum_stock);
        if (current <= 0) {
            return {
                key: "out",
                label: "Sin existencia",
                className: "catalog-stock-badge--out",
                icon: "ti-package-off",
            };
        }
        if (product.is_low_stock === true || current <= minimum) {
            return {
                key: "low",
                label: "Bajo stock",
                className: "catalog-stock-badge--low",
                icon: "ti-alert-triangle",
            };
        }
        return {
            key: "available",
            label: "Disponible",
            className: "catalog-stock-badge--available",
            icon: "ti-circle-check",
        };
    }

    function createStockBadge(product) {
        const productState = stockState(product);
        const badge = document.createElement("span");
        badge.className = `catalog-stock-badge ${productState.className}`;
        const icon = document.createElement("i");
        icon.className = `ti ${productState.icon}`;
        icon.setAttribute("aria-hidden", "true");
        const label = document.createElement("span");
        label.textContent = productState.label;
        badge.append(icon, label);
        return badge;
    }

    function renderImagePlaceholder(holder) {
        holder.replaceChildren();
        const placeholder = document.createElement("div");
        placeholder.className = "catalog-image-placeholder";
        const icon = document.createElement("i");
        icon.className = "ti ti-photo-off";
        icon.setAttribute("aria-hidden", "true");
        const label = document.createElement("span");
        label.textContent = "Imagen no disponible";
        placeholder.append(icon, label);
        holder.appendChild(placeholder);
    }

    function validImageUrl(value) {
        if (typeof value !== "string" || !value.trim()) {
            return null;
        }
        try {
            const parsed = new URL(value, window.location.origin);
            return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : null;
        } catch (_error) {
            return null;
        }
    }

    function renderProductImage(holder, product) {
        const imageUrl = validImageUrl(product.image_url);
        if (!imageUrl) {
            renderImagePlaceholder(holder);
            return;
        }

        holder.replaceChildren();
        const image = document.createElement("img");
        image.src = imageUrl;
        image.alt = "Imagen del producto";
        image.loading = "lazy";
        image.decoding = "async";
        image.addEventListener("error", () => renderImagePlaceholder(holder), {
            once: true,
        });
        holder.appendChild(image);
    }

    // Sin products:view_price la API omite sale_price: no se muestra un precio inventado.
    const hasPrice = (product) => product.sale_price !== undefined && product.sale_price !== null;

    function createFact(labelText, valueText) {
        const fact = document.createElement("div");
        fact.className = "catalog-card-fact";
        const label = document.createElement("span");
        label.textContent = labelText;
        const value = document.createElement("strong");
        value.textContent = valueText;
        fact.append(label, value);
        return fact;
    }

    function createProductCard(product) {
        const column = document.createElement("div");
        column.className = "col-12 col-sm-6 col-lg-4 col-xl-3";

        const card = document.createElement("article");
        card.className = "card catalog-product-card";

        const visual = document.createElement("div");
        visual.className = "catalog-product-visual";
        renderProductImage(visual, product);

        const body = document.createElement("div");
        body.className = "card-body";

        const meta = document.createElement("div");
        meta.className = "catalog-card-meta";
        const code = document.createElement("span");
        code.className = "catalog-code";
        code.textContent = product.code || "Sin código";
        meta.append(code, createStockBadge(product));

        const name = document.createElement("h2");
        name.className = "catalog-product-name";
        name.textContent = product.name || "Producto sin nombre";

        const category = document.createElement("p");
        category.className = "catalog-product-category";
        category.textContent = product.category || "Sin categoría";

        const facts = document.createElement("div");
        facts.className = "catalog-card-facts";
        facts.append(
            ...(hasPrice(product) ? [createFact("Precio", priceFormat.format(numericValue(product.sale_price)))] : []),
            createFact(
                "Stock",
                `${numberFormat.format(numericValue(product.current_stock))} ${product.unit || ""}`.trim()
            )
        );

        const detailButton = document.createElement("button");
        detailButton.type = "button";
        detailButton.className = "btn btn-outline-primary w-100 catalog-detail-button";
        detailButton.setAttribute("aria-label", "Ver detalles del producto");
        const buttonIcon = document.createElement("i");
        buttonIcon.className = "ti ti-eye me-1";
        buttonIcon.setAttribute("aria-hidden", "true");
        const buttonText = document.createElement("span");
        buttonText.textContent = "Ver detalles";
        detailButton.append(buttonIcon, buttonText);
        detailButton.addEventListener("click", () => openProductDetail(product.id));

        body.append(meta, name, category, facts, detailButton);
        card.append(visual, body);
        column.appendChild(card);
        return column;
    }

    function currentFilters() {
        return {
            search: elements.search.value.trim(),
            category: elements.category.value,
            stock: elements.stockStatus.value,
            sort: elements.sort.value,
        };
    }

    function pageQuery() {
        const filters = currentFilters();
        const params = new URLSearchParams({
            page: String(state.page),
            per_page: String(PAGE_SIZE),
            sort: filters.sort,
        });
        if (filters.search) params.set("search", filters.search);
        if (filters.category) params.set("category_id", filters.category);
        if (STOCK_STATUS[filters.stock]) params.set("status", STOCK_STATUS[filters.stock]);
        return params;
    }

    function updateUrl() {
        const filters = currentFilters();
        const params = new URLSearchParams();
        if (filters.search) params.set("q", filters.search);
        if (filters.category) params.set("category", filters.category);
        if (filters.stock) params.set("stock", filters.stock);
        if (filters.sort !== "name-asc") params.set("sort", filters.sort);
        if (state.page > 1) params.set("page", String(state.page));
        const query = params.toString();
        const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}`;
        window.history.replaceState(null, "", nextUrl);
    }

    function renderPage(items, total) {
        state.totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
        setCount(
            total === 1 ? "1 producto encontrado" : `${numberFormat.format(total)} productos encontrados`
        );
        if (total === 0) {
            showEmpty();
            updateUrl();
            return;
        }
        elements.grid.replaceChildren(...items.map(createProductCard));
        elements.grid.classList.remove("d-none");
        elements.state.classList.add("d-none");
        elements.pageIndicator.textContent = `Página ${state.page} de ${state.totalPages}`;
        elements.previous.disabled = state.page <= 1;
        elements.next.disabled = state.page >= state.totalPages;
        elements.pagination.classList.remove("d-none");
        updateUrl();
    }

    // Sin products:view_price la API omite sale_price: el orden por precio no se ofrece.
    function dropPriceSortIfHidden(items) {
        if (state.priceChecked || !items.length) return false;
        state.priceChecked = true;
        if (items.some(hasPrice)) return false;
        const sortedByPrice = elements.sort.value.startsWith("price-");
        elements.sort.querySelectorAll('option[value^="price-"]').forEach((option) => option.remove());
        if (sortedByPrice) elements.sort.value = "name-asc";
        return sortedByPrice;
    }

    async function loadPage() {
        const request = ++state.request;
        showLoading();
        try {
            const data = await apiGet(`/api/products?${pageQuery()}`);
            if (request !== state.request) return; // una respuesta vieja no pisa la consulta vigente
            const items = data && Array.isArray(data.items) ? data.items : [];
            const total = Number(data?.pagination?.total ?? data?.count ?? items.length);
            const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
            if (state.page > pages || dropPriceSortIfHidden(items)) {
                state.page = Math.min(state.page, pages);
                await loadPage();
                return;
            }
            renderPage(items, total);
        } catch (error) {
            if (request !== state.request) return;
            const message = errorMessage(error);
            showCatalogError(message);
            showAlert(message, error instanceof ApiError && error.status === 401 ? "warning" : "danger");
        }
    }

    function applyFilters(resetPage = true) {
        hideAlert();
        if (resetPage) {
            state.page = 1;
        }
        return loadPage();
    }

    function populateCategories(categories) {
        const firstOption = elements.category.options[0];
        const options = categories.map((category) => {
            const option = document.createElement("option");
            option.value = String(category.id);
            option.textContent = category.name || "Categoría sin nombre";
            return option;
        });
        elements.category.replaceChildren(firstOption, ...options);
    }

    function restoreFiltersFromUrl() {
        const params = new URLSearchParams(window.location.search);
        const validStock = ["", "available", "low", "out"];
        const validSort = [
            "name-asc",
            "name-desc",
            "price-asc",
            "price-desc",
            "stock-asc",
            "stock-desc",
        ];

        elements.search.value = params.get("q") ?? elements.search.value;
        const category = params.get("category") || "";
        if ([...elements.category.options].some((option) => option.value === category)) {
            elements.category.value = category;
        }
        const stock = params.get("stock") || "";
        elements.stockStatus.value = validStock.includes(stock) ? stock : "";
        const sort = params.get("sort") || "name-asc";
        const offered = [...elements.sort.options].some((option) => option.value === sort);
        elements.sort.value = validSort.includes(sort) && offered ? sort : "name-asc";

        const page = Number.parseInt(params.get("page") || "1", 10);
        state.page = Number.isInteger(page) && page > 0 ? page : 1;
    }

    function formatDate(value) {
        if (!value) return "—";
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? String(value) : dateFormat.format(date);
    }

    function setOptionalRow(rowId, valueId, product, field, formatter = String) {
        const row = $(rowId);
        const target = $(valueId);
        if (hasField(product, field)) {
            row.classList.remove("d-none");
            target.textContent = formatter(product[field]);
            return true;
        }
        row.classList.add("d-none");
        target.textContent = "—";
        return false;
    }

    function resetDetailState() {
        $("catalog-detail-loading").classList.remove("d-none");
        $("catalog-detail-content").classList.add("d-none");
        $("catalog-detail-error").classList.add("d-none");
        $("catalog-detail-error").textContent = "";
        $("catalog-detail-title").textContent = "Producto";
    }

    function renderProductDetail(product) {
        $("catalog-detail-title").textContent = product.name || "Detalle del producto";
        $("catalog-detail-name").textContent = product.name || "Producto sin nombre";
        $("catalog-detail-code").textContent = product.code || "Sin código";
        $("catalog-detail-category").textContent = product.category || "Sin categoría";
        $("catalog-detail-price").closest(".catalog-detail__price-block").hidden = !hasPrice(product);
        $("catalog-detail-price").textContent = hasPrice(product)
            ? priceFormat.format(numericValue(product.sale_price))
            : "—";
        $("catalog-detail-stock").textContent = numberFormat.format(
            numericValue(product.current_stock)
        );

        const status = $("catalog-detail-status");
        status.replaceChildren(createStockBadge(product));
        renderProductImage($("catalog-detail-visual"), product);

        setOptionalRow(
            "catalog-detail-minimum-row",
            "catalog-detail-minimum",
            product,
            "minimum_stock",
            (value) => numberFormat.format(numericValue(value))
        );
        setOptionalRow(
            "catalog-detail-unit-row",
            "catalog-detail-unit",
            product,
            "unit",
            (value) => String(value || "—")
        );

        const descriptionBlock = $("catalog-detail-description-block");
        if (hasField(product, "description")) {
            descriptionBlock.classList.remove("d-none");
            $("catalog-detail-description").textContent =
                product.description || "Sin descripción disponible.";
        } else {
            descriptionBlock.classList.add("d-none");
            $("catalog-detail-description").textContent = "";
        }

        const hasCreated = setOptionalRow(
            "catalog-detail-created-row",
            "catalog-detail-created",
            product,
            "created_at",
            formatDate
        );
        const hasUpdated = setOptionalRow(
            "catalog-detail-updated-row",
            "catalog-detail-updated",
            product,
            "updated_at",
            formatDate
        );
        $("catalog-detail-dates-block").classList.toggle(
            "d-none",
            !hasCreated && !hasUpdated
        );

        $("catalog-detail-loading").classList.add("d-none");
        $("catalog-detail-content").classList.remove("d-none");
    }

    async function openProductDetail(productId) {
        resetDetailState();
        if (!state.detailModal) {
            state.detailModal = new window.bootstrap.Modal(elements.detailModal);
        }
        state.detailModal.show();

        try {
            const product = await apiGet(`/api/products/${encodeURIComponent(productId)}`);
            renderProductDetail(product);
        } catch (error) {
            $("catalog-detail-loading").classList.add("d-none");
            const errorBox = $("catalog-detail-error");
            errorBox.textContent = errorMessage(error);
            errorBox.classList.remove("d-none");
        }
    }

    async function loadCatalog() {
        showLoading();
        hideAlert();
        try {
            const categoriesData = await apiGet("/api/categories");
            const categories = categoriesData && Array.isArray(categoriesData.items)
                ? categoriesData.items
                : [];

            populateCategories(categories);
            restoreFiltersFromUrl();
            await applyFilters(false);
        } catch (error) {
            const message = errorMessage(error);
            showCatalogError(message);
            showAlert(message, error instanceof ApiError && error.status === 401 ? "warning" : "danger");
        }
    }

    elements.search.addEventListener("input", () => {
        window.clearTimeout(state.searchTimer);
        state.searchTimer = window.setTimeout(() => {
            applyFilters(true);
        }, SEARCH_DEBOUNCE_MS);
    });

    [elements.category, elements.stockStatus, elements.sort].forEach((element) => {
        element.addEventListener("change", () => applyFilters(true));
    });

    $("catalog-filters-form").addEventListener("submit", (event) => {
        event.preventDefault();
        applyFilters(true);
    });

    elements.clear.addEventListener("click", () => {
        window.clearTimeout(state.searchTimer);
        elements.search.value = "";
        elements.category.value = "";
        elements.stockStatus.value = "";
        elements.sort.value = "name-asc";
        applyFilters(true);
        elements.search.focus();
    });

    elements.previous.addEventListener("click", () => {
        if (state.page <= 1) return;
        state.page -= 1;
        loadPage();
        window.scrollTo({ top: 0, behavior: "smooth" });
    });

    elements.next.addEventListener("click", () => {
        if (state.page >= state.totalPages) return;
        state.page += 1;
        loadPage();
        window.scrollTo({ top: 0, behavior: "smooth" });
    });

    loadCatalog();
})();
