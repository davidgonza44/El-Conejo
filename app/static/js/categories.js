/* Página de categorías: listar, crear y editar contra /api/categories.

   Reglas:
   - fetch con credentials: "same-origin" (sesión de Flask-Login).
   - window.CAN_WRITE (inyectado por Jinja) decide si se muestran acciones de
     escritura; la API valida permisos igualmente.
   - Errores 400 (validación) y 409 (nombre duplicado) se muestran en el modal.
   - Render con textContent / createElement para evitar XSS. */

(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);
    const CAN_WRITE = window.CAN_WRITE === true;
    const TABLE_COLS = 5;

    let editingId = null; // null = creando; número = editando

    // ------------------------------------------------------------------
    // Helper de API
    // ------------------------------------------------------------------
    class ApiError extends Error {
        constructor(status, message) {
            super(message);
            this.status = status;
        }
    }

    async function apiFetch(path, { method = "GET", body = null } = {}) {
        const options = { method, credentials: "same-origin" };
        if (body !== null) {
            options.headers = { "Content-Type": "application/json" };
            options.body = JSON.stringify(body);
        }

        let response;
        try {
            response = await fetch(path, options);
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

    function handlePageError(error) {
        if (error.status === 401) {
            showAlert("warning", "Su sesión expiró.", true);
        } else {
            showAlert("danger", errorMessage(error));
        }
    }

    // ------------------------------------------------------------------
    // Modal
    // ------------------------------------------------------------------
    let modalInstance = null;

    function getModal() {
        if (!modalInstance) {
            modalInstance = new bootstrap.Modal($("category-modal"));
        }
        return modalInstance;
    }

    const openModal = () => getModal().show();
    const closeModal = () => getModal().hide();

    function showFormError(message) {
        const box = $("category-form-error");
        box.textContent = message;
        box.classList.remove("d-none");
    }

    function hideFormError() {
        $("category-form-error").classList.add("d-none");
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
        wrap.append("Cargando categorías…");
        tbody.replaceChildren(stateRow(wrap));
    }

    /* 'YYYY-MM-DDTHH:MM:SS' -> 'DD/MM/YYYY' sin desfases de zona horaria. */
    function fmtDate(value) {
        if (!value) return "—";
        const parts = String(value).split("T")[0].split("-");
        if (parts.length !== 3) return String(value);
        return `${parts[2]}/${parts[1]}/${parts[0]}`;
    }

    function renderCategories(items) {
        const tbody = $("categories-tbody");

        if (!items.length) {
            tbody.replaceChildren(stateRow(
                "No hay categorías registradas todavía."
            ));
            return;
        }

        const rows = items.map((c) => {
            const tr = document.createElement("tr");

            const tdId = document.createElement("td");
            tdId.textContent = String(c.id);
            tr.appendChild(tdId);

            const tdName = document.createElement("td");
            tdName.className = "cell-title";
            tdName.textContent = c.name || "";
            tr.appendChild(tdName);

            const tdDesc = document.createElement("td");
            tdDesc.textContent = c.description || "—";
            if (c.description) tdDesc.title = c.description;
            tr.appendChild(tdDesc);

            const tdDate = document.createElement("td");
            tdDate.textContent = fmtDate(c.created_at);
            tr.appendChild(tdDate);

            const tdActions = document.createElement("td");
            tdActions.className = "text-nowrap";
            if (CAN_WRITE) {
                const btn = document.createElement("button");
                btn.type = "button";
                btn.className = "btn btn-sm btn-outline-primary";
                btn.title = "Editar categoría";
                btn.setAttribute("aria-label", "Editar categoría");
                const icon = document.createElement("i");
                icon.className = "ti ti-edit";
                btn.appendChild(icon);
                btn.addEventListener("click", () => openEdit(c));
                tdActions.appendChild(btn);
            } else {
                tdActions.textContent = "—";
            }
            tr.appendChild(tdActions);

            return tr;
        });

        tbody.replaceChildren(...rows);
    }

    // ------------------------------------------------------------------
    // Carga
    // ------------------------------------------------------------------
    async function loadCategories() {
        const tbody = $("categories-tbody");
        showLoading(tbody);
        $("categories-count").textContent = "Cargando…";

        try {
            const data = await apiFetch("/api/categories");
            const items = data.items || [];
            renderCategories(items);
            $("categories-count").textContent =
                items.length === 1 ? "1 categoría" : `${items.length} categorías`;
        } catch (error) {
            console.error("Error al cargar categorías:", error);
            $("categories-count").textContent = "Error al cargar";
            tbody.replaceChildren(stateRow(errorMessage(error)));
            handlePageError(error);
        }
    }

    // ------------------------------------------------------------------
    // Crear / editar
    // ------------------------------------------------------------------
    function openCreate() {
        editingId = null;
        hideFormError();
        $("c-id").value = "";
        $("c-name").value = "";
        $("c-description").value = "";
        $("category-modal-title").textContent = "Nueva categoría";
        openModal();
    }

    function openEdit(category) {
        editingId = category.id;
        hideFormError();
        $("c-id").value = String(category.id);
        $("c-name").value = category.name || "";
        $("c-description").value = category.description || "";
        $("category-modal-title").textContent = `Editar categoría — ${category.name}`;
        openModal();
    }

    async function submitForm(event) {
        event.preventDefault();
        hideFormError();

        const name = $("c-name").value.trim();
        if (!name) {
            showFormError("El nombre de la categoría es obligatorio.");
            return;
        }

        const payload = {
            name,
            description: $("c-description").value.trim(),
        };

        const saveBtn = $("category-save-btn");
        saveBtn.disabled = true;
        try {
            if (editingId === null) {
                const created = await apiFetch("/api/categories", { method: "POST", body: payload });
                showAlert("success", `Categoría '${created.name}' creada correctamente.`);
            } else {
                const updated = await apiFetch(`/api/categories/${editingId}`, {
                    method: "PUT",
                    body: payload,
                });
                showAlert("success", `Categoría '${updated.name}' actualizada correctamente.`);
            }
            closeModal();
            await loadCategories();
        } catch (error) {
            console.error("Error al guardar categoría:", error);
            if (error.status === 401) {
                showFormError("Su sesión expiró. Vuelva a iniciar sesión en /login.");
            } else {
                showFormError(errorMessage(error)); // incluye 400 y 409 con texto claro
            }
        } finally {
            saveBtn.disabled = false;
        }
    }

    // ------------------------------------------------------------------
    // Eventos e inicio
    // ------------------------------------------------------------------
    if (CAN_WRITE) {
        $("btn-new-category").addEventListener("click", openCreate);
        $("category-form").addEventListener("submit", submitForm);
    }

    loadCategories();
})();
