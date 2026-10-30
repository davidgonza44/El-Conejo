/* Configuración en vivo (núcleo): toast, fetch JSON, formato, diálogo de confirmación, menú (...),
   guardar/restablecer por pestaña. Cada pestaña compleja carga su propio ix_settings_<tab>.js y usa
   window.stLive. No corre en ?ref=1 (ahí carga ix_settings.js, sin escrituras). */
(() => {
    "use strict";
    const form = document.getElementById("st-live-form");
    if (!form || document.documentElement.dataset.referenceMode === "1") return;

    const tab = form.dataset.tab;
    const toastEl = document.querySelector(".st-toast");
    let toastTimer;
    function toast(message, isError = false) {
        toastEl.textContent = message;
        toastEl.classList.toggle("is-error", isError);
        toastEl.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { toastEl.hidden = true; }, isError ? 6000 : 3500);
    }
    async function api(url, options = {}) {
        const headers = { Accept: "application/json", ...(options.headers || {}) };
        headers["X-CSRFToken"] = form.dataset.csrf;
        if (options.body && !(options.body instanceof FormData) && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
        const response = await fetch(url, { credentials: "same-origin", ...options, headers });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(data.error || { 401: "Sesión expirada. Inicie sesión nuevamente.", 403: "No tiene permiso para esta acción." }[response.status] || `Error ${response.status}`);
            error.status = response.status;
            error.data = data;
            throw error;
        }
        return data;
    }
    /* El servidor guarda UTC sin desfase explícito: se interpreta como UTC y se muestra en la zona fija de la
       aplicación (America/Caracas), no en la del navegador. */
    const formatDate = (iso) => {
        const text = typeof iso === "string" && /T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(iso) ? `${iso}Z` : iso;
        const date = text ? new Date(text) : null;
        return date && !Number.isNaN(date.getTime())
            ? date.toLocaleString("es-VE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "America/Caracas" }) : "—";
    };
    const formatBytes = (bytes) => {
        if (!bytes) return "0 B";
        const units = ["B", "KB", "MB", "GB", "TB"];
        const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
        return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
    };
    function node(tag, className, text) {
        const el = document.createElement(tag);
        if (className) el.className = className;
        if (text !== undefined) el.textContent = text;
        return el;
    }
    const plural = (count, singular, pluralForm) => `${count} ${count === 1 ? singular : pluralForm}`;

    /* ---- Diálogos: cierre por [data-dialog-close]; error inline por .st-dialog-error ---- */
    document.querySelectorAll(".st-dialog").forEach((dialog) => {
        dialog.querySelectorAll("[data-dialog-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
    });
    function dialogError(dialog, message) {
        const el = dialog.querySelector(".st-dialog-error");
        if (!el) return;
        el.textContent = message || "";
        el.hidden = !message;
    }
    function openDialog(dialog) {
        dialogError(dialog, "");
        if (!dialog.open) dialog.showModal();
    }
    /* Confirmación genérica (Promise<boolean>); con typed= exige escribir el texto (p. ej. RESTAURAR). */
    const confirmDialog = document.getElementById("st-confirm");
    function confirm(title, text, { danger = false, accept = "Confirmar" } = {}) {
        return new Promise((resolve) => {
            document.getElementById("st-confirm-title").textContent = title;
            document.getElementById("st-confirm-text").textContent = text;
            const button = document.getElementById("st-confirm-accept");
            button.textContent = accept;
            button.classList.toggle("st-danger", danger);
            const formEl = document.getElementById("st-confirm-form");
            const done = (value) => { formEl.onsubmit = null; confirmDialog.onclose = null; resolve(value); };
            formEl.onsubmit = (event) => { event.preventDefault(); confirmDialog.close(); done(true); };
            confirmDialog.onclose = () => done(false);
            openDialog(confirmDialog);
        });
    }

    /* ---- Menú contextual (...): items [{label, action, danger, disabled}] ---- */
    const menuEl = document.getElementById("st-menu");
    function closeMenu() { menuEl.hidden = true; menuEl.replaceChildren(); }
    function openMenu(anchor, items) {
        if (!menuEl.hidden && menuEl.dataset.anchor === anchor.id) { closeMenu(); return; }
        menuEl.replaceChildren(...items.map((item) => {
            const button = node("button", `st-menu-item${item.danger ? " is-danger" : ""}`, item.label);
            button.type = "button"; button.setAttribute("role", "menuitem"); button.disabled = Boolean(item.disabled);
            if (item.title) button.title = item.title;
            button.addEventListener("click", () => { closeMenu(); item.action(); });
            return button;
        }));
        menuEl.dataset.anchor = anchor.id || "";
        menuEl.hidden = false;
        const rect = anchor.getBoundingClientRect();
        const width = menuEl.offsetWidth;
        menuEl.style.top = `${rect.bottom + window.scrollY + 4}px`;
        menuEl.style.left = `${Math.max(8, Math.min(rect.right + window.scrollX - width, window.innerWidth - width - 8))}px`;
        menuEl.querySelector("button:not(:disabled)")?.focus();
    }
    document.addEventListener("click", (event) => { if (!menuEl.hidden && !menuEl.contains(event.target) && !event.target.closest("[data-menu]")) closeMenu(); });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMenu(); });

    /* ---- Guardar / restablecer: inputs con nombre "modulo.clave" (claves soportadas en el servidor) ---- */
    const SETTING_NAME = /^[a-z_]+\.[a-z_]+$/;
    const settingInputs = () => [...form.querySelectorAll("[name]")].filter((input) => SETTING_NAME.test(input.name) && !input.disabled);
    /* Sin settings:write los ajustes se muestran de solo lectura: no hay botón para guardarlos. */
    if (form.dataset.canWrite !== "1") settingInputs().forEach((input) => { input.disabled = true; input.title = "Solo lectura: su rol no puede modificar este ajuste."; });
    function readValue(input) {
        if (input.type === "checkbox") return input.checked;
        if (input.type === "number") return Number.parseInt(input.value, 10);
        return input.value.trim();
    }
    const paintHooks = [];
    function paint(settings) {
        settingInputs().forEach((input) => {
            if (!(input.name in settings)) return;
            if (input.type === "checkbox") input.checked = settings[input.name] === true;
            else input.value = String(settings[input.name]);
            if (input.name === "company.name") input.title = settings[input.name];
        });
        paintHooks.forEach((hook) => hook(settings));
    }
    async function saveSettings(extra = {}) {
        const payload = { ...Object.fromEntries(settingInputs().map((input) => [input.name, readValue(input)])), ...extra };
        if (!Object.keys(payload).length) return null;
        if (form.dataset.settingsAvailable !== "1") { toast("La tabla app_settings no existe: ejecute scripts/migrate_app_settings.py.", true); return null; }
        const body = window.stCompanyLogo ? window.stCompanyLogo.body(payload) : JSON.stringify(payload);
        const data = await api("/api/settings", { method: "PUT", body });
        paint(data.settings);
        return data;
    }
    const saveHandlers = [];
    const resetHandlers = [];
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
            if (saveHandlers.length) { for (const handler of saveHandlers) await handler(); return; }
            const data = await saveSettings();
            if (data) toast(data.message || "Configuración guardada.");
        } catch (error) { toast(error.message, true); }
    });
    document.getElementById("st-reset")?.addEventListener("click", async () => {
        try {
            if (resetHandlers.length) { for (const handler of resetHandlers) await handler(); return; }
            const ok = await confirm("Restablecer valores", "Se devolverán a su valor por defecto los ajustes de esta pestaña. No afecta usuarios, roles, credenciales ni respaldos.", { accept: "Restablecer" });
            if (!ok) return;
            const data = await api("/api/settings/reset", { method: "POST", body: JSON.stringify({ tab }) });
            paint(data.settings);
            toast(data.message || "Valores restablecidos.");
        } catch (error) { toast(error.message, true); }
    });

    window.stLive = { tab, toast, api, formatDate, formatBytes, node, plural, openDialog, dialogError, confirm, openMenu, closeMenu,
        paint, saveSettings, onPaint: (hook) => paintHooks.push(hook), onSave: (handler) => saveHandlers.push(handler),
        onReset: (handler) => resetHandlers.push(handler) };
})();
