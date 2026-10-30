/* Configuración › Seguridad: contraseña propia, política (7 reglas), 2FA TOTP (enrolar, códigos, desactivar),
   sesiones del servidor (cerrar una / otras / todas / inactivas), historial de accesos y dispositivos confiables. */
(() => {
    "use strict";
    const L = window.stLive;
    const root = document.getElementById("st-security");
    if (!L || !root) return;
    const { api, toast, node, formatDate, plural, confirm, openMenu, openDialog, dialogError } = L;
    const $ = (id) => document.getElementById(id);
    const state = JSON.parse(root.dataset.security);
    const localize = (scope) => scope.querySelectorAll("[data-iso]").forEach((el) => { if (el.dataset.iso) el.textContent = formatDate(el.dataset.iso); });
    localize(root.parentElement);

    /* ---- Contraseña propia (cambiar / establecer) ---- */
    const passwordDialog = $("st-password-dialog");
    const passwordForm = $("st-password-form");
    $("st-change-password").addEventListener("click", () => { passwordForm.reset(); openDialog(passwordDialog); passwordForm.new_password.focus(); });
    passwordForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const payload = Object.fromEntries(new FormData(passwordForm).entries());
        try {
            const data = await api("/api/auth/change-password", { method: "POST", body: JSON.stringify(payload) });
            passwordDialog.close();
            toast(data.message);
            await refresh();
        } catch (error) { dialogError(passwordDialog, error.message); }
    });

    /* ---- Política de contraseñas (diálogo con las 7 reglas; se persiste en /api/settings) ---- */
    const policyDialog = $("st-policy-dialog");
    const policyForm = $("st-policy-form");
    const POLICY_LABELS = {
        min_length: (v) => `${v} caracteres`, require_uppercase: (v) => (v ? "Requeridas" : "Opcionales"),
        require_lowercase: (v) => (v ? "Requeridas" : "Opcionales"), require_number: (v) => (v ? "Requeridos" : "Opcionales"),
        require_special: (v) => (v ? "Requeridos" : "Opcionales"), expiration_days: (v) => (v ? `Cada ${v} días` : "Nunca expira"),
        history_count: (v) => (v ? `Últimas ${v} no reutilizables` : "Sin restricción"),
    };
    function paintPolicy(policy) {
        Object.entries(POLICY_LABELS).forEach(([key, label]) => {
            const cell = root.querySelector(`[data-policy="${key}"]`);
            cell.textContent = label(policy[key]);
            const row = cell.parentElement;
            const active = key === "min_length" || Boolean(policy[key]);
            row.classList.toggle("is-unavailable", !active);
        });
        $("st-password-hint-min").textContent = policy.min_length;
        passwordForm.new_password.minLength = policy.min_length;
        policyForm["security.password_min_length"].value = policy.min_length;
        policyForm["security.password_expiration_days"].value = policy.expiration_days;
        policyForm["security.password_history_count"].value = policy.history_count;
        ["uppercase", "lowercase", "number", "special"].forEach((rule) => { policyForm[`security.password_require_${rule}`].checked = Boolean(policy[`require_${rule}`]); });
    }
    const policyPayload = () => ({
        "security.password_min_length": Number.parseInt(policyForm["security.password_min_length"].value, 10),
        "security.password_require_uppercase": policyForm["security.password_require_uppercase"].checked,
        "security.password_require_lowercase": policyForm["security.password_require_lowercase"].checked,
        "security.password_require_number": policyForm["security.password_require_number"].checked,
        "security.password_require_special": policyForm["security.password_require_special"].checked,
        "security.password_expiration_days": Number.parseInt(policyForm["security.password_expiration_days"].value, 10),
        "security.password_history_count": Number.parseInt(policyForm["security.password_history_count"].value, 10),
    });
    function policyFromSettings(settings) {
        return { min_length: settings["security.password_min_length"], require_uppercase: settings["security.password_require_uppercase"],
            require_lowercase: settings["security.password_require_lowercase"], require_number: settings["security.password_require_number"],
            require_special: settings["security.password_require_special"], expiration_days: settings["security.password_expiration_days"],
            history_count: settings["security.password_history_count"] };
    }
    L.onPaint((settings) => { if ("security.password_min_length" in settings) paintPolicy(policyFromSettings(settings)); });
    $("st-edit-policy").addEventListener("click", () => openDialog(policyDialog));
    async function savePolicy() {
        const data = await api("/api/settings", { method: "PUT", body: JSON.stringify(policyPayload()) });
        L.paint(data.settings);
        return data;
    }
    policyForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        try { const data = await savePolicy(); policyDialog.close(); toast(data.message || "Política guardada."); }
        catch (error) { dialogError(policyDialog, error.message); }
    });
    L.onSave(async () => { const data = await savePolicy(); toast(data.message || "Política guardada."); });

    /* ---- Re-autenticación fuerte reutilizable (código 2FA + contraseña si existe) ---- */
    const reauthDialog = $("st-reauth-dialog");
    const reauthForm = $("st-reauth-form");
    let reauthHandler = null;
    function reauth(title, text, handler, { label = false, accept = "Confirmar" } = {}) {
        reauthForm.reset();
        $("st-reauth-title").textContent = title;
        $("st-reauth-text").textContent = text;
        $("st-reauth-label-wrap").hidden = !label;
        $("st-reauth-password-wrap").hidden = !state.has_password;
        $("st-reauth-submit").textContent = accept;
        reauthHandler = handler;
        openDialog(reauthDialog);
        reauthForm.code.focus();
    }
    reauthForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const payload = Object.fromEntries(new FormData(reauthForm).entries());
        try { await reauthHandler(payload); reauthDialog.close(); }
        catch (error) { dialogError(reauthDialog, error.message); }
    });

    /* ---- 2FA ---- */
    const mfaDialog = $("st-mfa-dialog");
    const mfaForm = $("st-mfa-form");
    function showRecoveryCodes(codes, title) {
        $("st-mfa-title").textContent = title;
        $("st-mfa-enroll-step").hidden = true;
        $("st-mfa-codes-step").hidden = false;
        $("st-mfa-code-list").replaceChildren(...codes.map((code) => node("li", "", code)));
        $("st-mfa-submit").hidden = true;
        openDialog(mfaDialog);
    }
    /* Sin payload: primer enrolamiento. Con payload (código actual + contraseña): reemplazo del factor activo. */
    async function startEnrollment(payload) {
        const data = await api("/api/auth/mfa/enroll", { method: "POST", body: payload ? JSON.stringify(payload) : undefined });
        $("st-mfa-title").textContent = payload ? "Reemplazar aplicación de autenticación" : "Activar verificación en dos pasos";
        $("st-mfa-submit").textContent = payload ? "Verificar y reemplazar" : "Verificar y activar";
        $("st-mfa-enroll-step").hidden = false;
        $("st-mfa-codes-step").hidden = true;
        $("st-mfa-submit").hidden = false;
        $("st-mfa-qr").innerHTML = data.qr_svg; // SVG generado por el servidor (qrcode), no HTML de usuario
        $("st-mfa-setup-key").textContent = data.setup_key;
        mfaForm.reset();
        openDialog(mfaDialog);
        mfaForm.code.focus();
    }
    mfaForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!$("st-mfa-codes-step").hidden) { mfaDialog.close(); return; }
        try {
            const data = await api("/api/auth/mfa/confirm", { method: "POST", body: JSON.stringify({ code: mfaForm.code.value }) });
            toast(data.message);
            showRecoveryCodes(data.recovery_codes, data.replaced ? "Factor reemplazado: nuevos códigos de recuperación" : "2FA activado: códigos de recuperación");
            await refresh();
        } catch (error) { dialogError(mfaDialog, error.message); }
    });
    function disableMfa() {
        reauth("Desactivar verificación en dos pasos", "Esta acción reduce la seguridad de tu cuenta y revoca los dispositivos confiables.", async (payload) => {
            const data = await api("/api/auth/mfa/disable", { method: "POST", body: JSON.stringify(payload) });
            toast(data.message);
            await refresh();
        }, { accept: "Desactivar 2FA" });
    }
    const enroll = () => startEnrollment().catch((error) => toast(error.message, true));
    $("st-mfa-toggle").addEventListener("click", () => (state.mfa.enabled ? disableMfa() : enroll()));
    $("st-mfa-method-button").addEventListener("click", () => (state.mfa.enabled
        ? reauth("Cambiar aplicación de autenticación", "Confirma tu identidad con el código actual y tu contraseña. El factor actual sigue vigente hasta verificar el nuevo.", startEnrollment, { accept: "Continuar" })
        : enroll()));
    $("st-mfa-codes-button").addEventListener("click", () => reauth("Regenerar códigos de recuperación", "Los códigos anteriores dejarán de ser válidos.", async (payload) => {
        const data = await api("/api/auth/mfa/recovery-codes", { method: "POST", body: JSON.stringify(payload) });
        toast(data.message);
        showRecoveryCodes(data.recovery_codes, "Nuevos códigos de recuperación");
        await refresh();
    }, { accept: "Regenerar" }));

    /* ---- Sesiones ---- */
    const statusBadge = (status) => node("span", `st-security-status${status === "inactive" ? " is-inactive" : ""}`, status === "active" ? "Activa" : "Inactiva");
    function sessionRow(item, compact) {
        const row = node("tr"); row.dataset.sessionId = item.id;
        const device = node("td", "", item.device); device.title = item.device;
        if (item.current) { device.append(document.createTextNode(" ")); device.append(node("span", "st-badge", "Actual")); }
        const status = node("td"); status.append(statusBadge(item.status));
        const actions = node("td");
        if (!item.current) { const button = node("button", "st-security-link", "Cerrar"); button.type = "button"; button.dataset.revokeSession = item.id; actions.append(button); }
        row.append(device, node("td", "", item.location), node("td", "", item.ip_address), node("td", "", formatDate(item.created_at)));
        if (!compact) row.append(node("td", "", formatDate(item.last_seen_at)));
        row.append(status, actions);
        return row;
    }
    function emptyRow(text, span) { const row = node("tr", "st-security-empty"); const cell = node("td", "", text); cell.colSpan = span; row.append(cell); return row; }
    function renderSessions() {
        const items = state.sessions.items;
        $("st-sessions-body").replaceChildren(...(items.length ? items.slice(0, 4).map((item) => sessionRow(item, true)) : [emptyRow("Sin sesiones registradas.", 6)]));
    }
    async function revokeSession(id) {
        const data = await api(`/api/settings/security/sessions/${id}`, { method: "DELETE" });
        toast(data.message);
        if (data.current_closed) { window.location.href = "/login"; return; }
        await refresh();
    }
    document.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-revoke-session]");
        if (!button) return;
        try { await revokeSession(button.dataset.revokeSession); if (listDialog.open) await listPager.reload(); }
        catch (error) { toast(error.message, true); }
    });
    async function revokeScope(scope, title, text, danger) {
        if (!(await confirm(title, text, { danger, accept: "Cerrar sesiones" }))) return;
        try {
            const data = await api("/api/settings/security/sessions/revoke", { method: "POST", body: JSON.stringify({ scope }) });
            toast(data.message);
            if (scope === "all") { window.location.href = "/login"; return; }
            await refresh();
        } catch (error) { toast(error.message, true); }
    }
    $("st-sessions-close-menu").addEventListener("click", (event) => openMenu(event.currentTarget, [
        { label: "Cerrar todas las demás sesiones", action: () => revokeScope("others", "Cerrar las demás sesiones", "Se cerrarán todas las sesiones excepto la actual.", false) },
        { label: "Cerrar todas (incluida esta)", danger: true, action: () => revokeScope("all", "Cerrar todas las sesiones", "Se cerrarán todas las sesiones, incluida la actual; deberás iniciar sesión de nuevo.", true) },
    ]));
    $("st-sessions-close-inactive").addEventListener("click", () => revokeScope("inactive", "Cerrar sesiones inactivas", "Se cerrarán las sesiones sin actividad en las últimas 24 horas.", false));

    /* ---- Diálogo de listas paginadas (sesiones completas / historial completo) ---- */
    const listDialog = $("st-list-dialog");
    const listPager = { page: 1, pages: 1, loader: null, reload: async () => listPager.loader(listPager.page) };
    function headRow(labels) { const row = node("tr"); labels.forEach((label) => { const th = node("th", "", label); th.scope = "col"; row.append(th); }); $("st-list-head").replaceChildren(row); }
    function openList(title, labels, loader) {
        $("st-list-title").textContent = title;
        headRow(labels);
        listPager.page = 1; listPager.loader = loader;
        openDialog(listDialog);
        loader(1).catch((error) => toast(error.message, true));
    }
    function paintPager(page, pages) {
        listPager.page = page; listPager.pages = Math.max(1, pages);
        $("st-list-page").textContent = `Página ${page} de ${listPager.pages}`;
        $("st-list-prev").disabled = page <= 1; $("st-list-next").disabled = page >= listPager.pages;
    }
    $("st-list-prev").addEventListener("click", () => listPager.loader(listPager.page - 1));
    $("st-list-next").addEventListener("click", () => listPager.loader(listPager.page + 1));
    $("st-sessions-all").addEventListener("click", () => openList("Todas las sesiones activas",
        ["Dispositivo / Navegador", "Ubicación", "IP observada", "Inicio de sesión", "Última actividad", "Estado", ""], async () => {
            const data = await api("/api/settings/security/sessions");
            state.sessions = data;
            renderSessions();
            $("st-list-body").replaceChildren(...(data.items.length ? data.items.map((item) => sessionRow(item, false)) : [emptyRow("Sin sesiones registradas.", 7)]));
            paintPager(1, 1);
        }));
    function historyRow(item) {
        const row = node("tr");
        const result = node("span", `st-security-status${item.success ? "" : " is-failed"}`, item.success ? "Exitoso" : "Fallido"); result.title = item.reason_label;
        const status = node("td"); status.append(result);
        row.append(node("td", "", formatDate(item.created_at)), node("td", "", item.ip_address), node("td", "", item.location), status);
        return row;
    }
    function renderHistory() {
        $("st-history-body").replaceChildren(...(state.history.items.length ? state.history.items.map(historyRow) : [emptyRow("Sin intentos registrados todavía.", 4)]));
    }
    $("st-history-all").addEventListener("click", () => openList("Historial completo de inicios de sesión",
        ["Fecha y hora", "IP observada", "Ubicación", "Dispositivo", "Método", "Resultado"], async (page) => {
            const data = await api(`/api/settings/security/login-history?page=${page}&per_page=10`);
            $("st-list-body").replaceChildren(...(data.items.length ? data.items.map((item) => {
                const row = historyRow(item);
                row.insertBefore(node("td", "", item.device), row.children[3]);
                row.insertBefore(node("td", "", { local: "Contraseña", passwordless: "Enlace mágico", google: "Google" }[item.provider] || item.provider), row.children[4]);
                return row;
            }) : [emptyRow("Sin intentos registrados.", 6)]));
            paintPager(data.page, data.pages);
        }));

    /* ---- Dispositivos confiables ---- */
    function renderTrusted() {
        const items = state.mfa.trusted_devices || [];
        const list = $("st-trusted-list");
        const enabled = state.mfa.enabled;
        if (!items.length) {
            const art = list.querySelector(".st-trusted-art");
            const empty = node("div", "st-trusted-empty");
            if (art) empty.append(art);
            empty.append(node("strong", "", enabled ? "Sin dispositivos confiables" : "2FA desactivado"),
                node("p", "", enabled ? "Marca este navegador como confiable para omitir el código durante 30 días." : "Activa 2FA para poder registrar dispositivos confiables."));
            list.replaceChildren(empty);
        } else {
            list.replaceChildren(node("ul", "st-trusted-rows"));
            list.firstChild.append(...items.map((device) => {
                const li = node("li");
                const icon = node("span", "st-notification-icon st-tone-blue"); icon.innerHTML = root.querySelector(".st-security-access .st-notification-icon").innerHTML;
                const copy = node("div"); const strong = node("strong", "", device.label);
                if (device.current) { strong.append(document.createTextNode(" ")); strong.append(node("span", "st-badge", "Este")); }
                copy.append(strong, node("p", "", `IP ${device.ip_address} · vence ${formatDate(device.expires_at)}`));
                const revoke = node("button", "st-security-link", "Revocar"); revoke.type = "button"; revoke.dataset.revokeDevice = device.id;
                li.append(icon, copy, revoke);
                return li;
            }));
        }
        $("st-trusted-add").disabled = !enabled;
        const revokeAll = $("st-trusted-revoke-all");
        if (revokeAll) revokeAll.hidden = !items.length;
    }
    document.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-revoke-device]");
        if (!button) return;
        try { const data = await api(`/api/auth/mfa/devices/${button.dataset.revokeDevice}`, { method: "DELETE" }); toast(data.message); state.mfa.trusted_devices = data.items; renderTrusted(); }
        catch (error) { toast(error.message, true); }
    });
    $("st-trusted-add").addEventListener("click", () => reauth("Agregar dispositivo confiable", "Este navegador omitirá el código 2FA durante 30 días.", async (payload) => {
        const data = await api("/api/auth/mfa/devices", { method: "POST", body: JSON.stringify(payload) });
        toast(data.message);
        state.mfa.trusted_devices = data.items;
        renderTrusted();
    }, { label: true, accept: "Confiar en este dispositivo" }));
    $("st-trusted-revoke-all")?.addEventListener("click", async () => {
        if (!(await confirm("Revocar dispositivos confiables", "Todos los dispositivos volverán a pedir el código 2FA.", { danger: true, accept: "Revocar todos" }))) return;
        try { const data = await api("/api/auth/mfa/devices", { method: "DELETE" }); toast(data.message); state.mfa.trusted_devices = []; renderTrusted(); }
        catch (error) { toast(error.message, true); }
    });

    /* ---- Refresco completo desde /api/settings/security ---- */
    function renderMfa() {
        const mfa = state.mfa;
        $("st-mfa-badge").textContent = mfa.enabled ? "Activo" : "Inactivo";
        $("st-mfa-badge").classList.toggle("is-muted", !mfa.enabled);
        $("st-mfa-toggle").setAttribute("aria-label", mfa.enabled ? "Desactivar autenticación en dos pasos" : "Activar autenticación en dos pasos");
        $("st-mfa-row-text").textContent = mfa.enabled ? "Activa con aplicación de autenticación (TOTP)." : "Protege tu cuenta con un código temporal (TOTP).";
        const summary = $("st-mfa-summary");
        summary.classList.toggle("is-muted", !mfa.enabled);
        summary.querySelector("strong").textContent = mfa.enabled ? "2FA activado" : "2FA desactivado";
        summary.querySelector("span").textContent = mfa.enabled ? `Activo desde ${formatDate(mfa.enabled_at)}.` : "Activa la verificación con una aplicación de autenticación.";
        $("st-mfa-method").textContent = mfa.enabled ? "Aplicación de autenticación (TOTP)" : "Sin configurar";
        $("st-mfa-method-button").textContent = mfa.enabled ? "Cambiar" : "Configurar";
        $("st-mfa-codes-count").textContent = mfa.enabled ? plural(mfa.recovery_codes_remaining, "disponible", "disponibles") : "No generados";
        $("st-mfa-codes-button").disabled = !mfa.enabled;
    }
    function renderPassword() {
        const text = $("st-password-status");
        if (!state.has_password) text.textContent = "Tu cuenta entra con Google o enlace mágico: aún no tiene contraseña.";
        else text.textContent = state.password_expires_at ? `Vence el ${formatDate(state.password_expires_at)}.` : "Cambia tu contraseña verificando la actual.";
        const label = state.has_password ? "Cambiar contraseña" : "Establecer contraseña";
        $("st-change-password").textContent = label;
        $("st-password-title").textContent = label;
        passwordForm.querySelector("[type=submit]").textContent = state.has_password ? "Actualizar contraseña" : "Establecer contraseña";
        $("st-password-current-wrap").hidden = !state.has_password;
        passwordForm.current_password.required = state.has_password;
    }
    async function refresh() {
        Object.assign(state, await api("/api/settings/security"));
        paintPolicy(state.policy); renderPassword(); renderMfa(); renderSessions(); renderHistory(); renderTrusted();
    }
})();
