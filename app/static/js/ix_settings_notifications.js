/* Configuración › Notificaciones: canales (estado real del proveedor, destino, activación), matriz evento×canal,
   bandeja paginada con detalle/lectura y prueba real por canal. Persistencia vía /api/settings y /api/settings/notifications. */
(() => {
    "use strict";
    const L = window.stLive;
    const root = document.getElementById("st-notifications");
    if (!L || !root) return;
    const { api, toast, node, formatDate, openDialog, dialogError } = L;
    const $ = (id) => document.getElementById(id);
    const form = document.getElementById("st-live-form");
    let channels = JSON.parse(root.dataset.channels);
    const channelInfo = (key) => channels.find((c) => c.channel === key);
    const DESTINATION_KEY = { email: "notifications.email_to", whatsapp: "notifications.whatsapp_to", sms: "notifications.sms_to" };
    const DESTINATION_LABEL = { email: "Correo de destino", whatsapp: "Número de WhatsApp (formato internacional, p. ej. +584121234567)", sms: "Número SMS (formato internacional)" };
    const HELP = {
        email: "Defina en el servidor (.env): MAIL_ENABLED=true, MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD y MAIL_DEFAULT_SENDER. Reinicie la aplicación.",
        whatsapp: "Cree una app de WhatsApp Business en Meta for Developers y defina en el servidor: WHATSAPP_ENABLED=true, WHATSAPP_PHONE_NUMBER_ID y WHATSAPP_ACCESS_TOKEN (token permanente del sistema). El destinatario debe estar habilitado en la cuenta.",
        sms: "Cree una cuenta Twilio y defina en el servidor: SMS_ENABLED=true, SMS_ACCOUNT_SID, SMS_AUTH_TOKEN y SMS_FROM_NUMBER. Las credenciales nunca se muestran en la aplicación.",
    };
    localizeTimes();
    function localizeTimes() { root.querySelectorAll("[data-iso]").forEach((el) => { if (el.dataset.iso) el.textContent = formatDate(el.dataset.iso); }); }

    /* ---- Canal desactivado => sus celdas de la matriz se apagan y bloquean (misma regla que el servidor) ---- */
    /* Celdas externas del canal: notify.<evento>_<canal>. (La app siempre puede recibir eventos; la campana respeta notifications.app_enabled.)
       Los interruptores nunca se deshabilitan (así el guardado siempre envía false y nunca queda un valor obsoleto en true);
       un canal bloqueado revierte el cambio y explica el motivo. */
    const EXTERNAL = ["email", "whatsapp", "sms"];
    const matrixCells = (channel) => [...form.querySelectorAll(`.st-notification-matrix input[name$="_${channel}"]`)];
    const channelToggle = (channel) => form.querySelector(`[name="notifications.${channel}_enabled"]`);
    function blockReason(channel) {
        const info = channelInfo(channel);
        if (!info?.configured) return `Proveedor no configurado (${(info?.missing || []).join(", ")}). Use “Configurar”.`;
        return channelToggle(channel).checked ? "" : "Active el canal para asignarle eventos.";
    }
    function syncMatrix(channel) {
        const info = channelInfo(channel);
        const toggle = channelToggle(channel);
        if (!info?.configured) toggle.checked = false;
        toggle.title = info?.configured ? "" : `Proveedor no configurado (${(info?.missing || []).join(", ")}).`;
        const reason = blockReason(channel);
        matrixCells(channel).forEach((cell) => {
            cell.classList.toggle("is-blocked", Boolean(reason));
            cell.setAttribute("aria-disabled", reason ? "true" : "false");
            if (reason) cell.checked = false;
            cell.title = reason;
        });
    }
    EXTERNAL.forEach((channel) => {
        syncMatrix(channel);
        channelToggle(channel).addEventListener("change", () => {
            const info = channelInfo(channel);
            if (channelToggle(channel).checked && !info.configured) { channelToggle(channel).checked = false; toast(`${info.label}: proveedor no configurado (${info.missing.join(", ")}).`, true); }
            syncMatrix(channel);
        });
        matrixCells(channel).forEach((cell) => cell.addEventListener("change", () => {
            const reason = blockReason(channel);
            if (cell.checked && reason) { cell.checked = false; toast(`${channelInfo(channel).label}: ${reason}`, true); }
        }));
    });
    L.onPaint(() => EXTERNAL.forEach(syncMatrix));

    /* ---- Diálogo "Configurar" por canal externo ---- */
    const channelDialog = $("st-channel-dialog");
    const channelForm = $("st-channel-form");
    let channelBeingEdited = null;
    root.querySelectorAll("[data-channel-config]").forEach((button) => button.addEventListener("click", (event) => {
        event.preventDefault();
        const channel = button.dataset.channelConfig;
        const info = channelInfo(channel);
        channelBeingEdited = channel;
        $("st-channel-title").textContent = `Configurar canal: ${info.label}`;
        const status = $("st-channel-status");
        status.className = `st-dialog-status ${info.configured ? "is-ok" : "is-missing"}`;
        status.textContent = info.configured ? "Proveedor configurado en el servidor." : `Proveedor no configurado. Falta en el servidor: ${info.missing.join(", ")}.`;
        $("st-channel-destination-label").textContent = DESTINATION_LABEL[channel];
        channelForm.destination.value = form.querySelector(`[name="${DESTINATION_KEY[channel]}"]`)?.value ?? info.destination ?? "";
        channelForm.destination.type = channel === "email" ? "email" : "tel";
        channelForm.enabled.checked = form.querySelector(`[name="notifications.${channel}_enabled"]`).checked;
        channelForm.enabled.disabled = !info.configured;
        $("st-channel-help").textContent = HELP[channel];
        openDialog(channelDialog);
    }));
    channelForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const channel = channelBeingEdited;
        const payload = { [DESTINATION_KEY[channel]]: channelForm.destination.value.trim() };
        if (!channelForm.enabled.disabled) payload[`notifications.${channel}_enabled`] = channelForm.enabled.checked;
        if (payload[`notifications.${channel}_enabled`] === false) {
            [...form.querySelectorAll(`.st-notification-matrix input[name$="_${channel}"]`)].forEach((cell) => { payload[cell.name] = false; });
        }
        try {
            const data = await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
            L.paint(data.settings);
            const destinationInput = form.querySelector(`[name="${DESTINATION_KEY[channel]}"]`);
            if (destinationInput) destinationInput.value = data.settings[DESTINATION_KEY[channel]];
            channelDialog.close();
            toast(`Canal ${channelInfo(channel).label} guardado.`);
            await refreshChannels();
        } catch (error) { dialogError(channelDialog, error.message); }
    });
    async function refreshChannels() {
        const data = await api("/api/settings/notifications/overview");
        channels = data.channels;
        channels.forEach((info) => {
            const row = root.querySelector(`[data-channel-row="${info.channel}"]`);
            if (!row) return;
            const copy = row.querySelector(".st-channel-copy > span"); // title conserva la descripción base
            if (info.channel !== "app") copy.textContent = info.configured ? `${copy.title} Destino: ${info.destination || "sin definir"}.` : `Proveedor no configurado: falta ${info.missing.join(", ")}.`;
            syncMatrix(info.channel);
        });
    }

    /* ---- Probar notificaciones: selección de canales y resultado real por canal ---- */
    const testDialog = $("st-test-dialog");
    const testForm = $("st-test-form");
    document.getElementById("st-test-notifications")?.addEventListener("click", () => {
        $("st-test-channels").replaceChildren(...channels.map((info) => {
            const label = node("label", "st-dialog-check");
            const input = node("input"); input.type = "checkbox"; input.name = "channels"; input.value = info.channel;
            input.checked = info.enabled && info.configured; input.disabled = !(info.enabled && info.configured);
            label.append(input, node("span", "", `${info.label}${input.disabled ? (info.configured ? " (canal desactivado)" : " (proveedor no configurado)") : ""}`));
            return label;
        }));
        $("st-test-results").hidden = true; $("st-test-results").replaceChildren();
        $("st-test-submit").hidden = false;
        openDialog(testDialog);
    });
    testForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const selected = [...testForm.querySelectorAll("[name=channels]:checked")].map((input) => input.value);
        if (!selected.length) { dialogError(testDialog, "Seleccione al menos un canal."); return; }
        try {
            const data = await api("/api/settings/notifications/test", { method: "POST", body: JSON.stringify({ channels: selected }) });
            const list = $("st-test-results");
            list.replaceChildren(...data.results.map((result) => {
                const item = node("li", result.ok ? "is-ok" : "is-failed");
                item.append(node("strong", "", channelInfo(result.channel)?.label || result.channel), node("span", "", ` — ${result.detail}`));
                return item;
            }));
            list.hidden = false;
            dialogError(testDialog, "");
            toast(data.ok ? "Prueba enviada por todos los canales seleccionados." : "La prueba falló en al menos un canal; revise el detalle.", !data.ok);
            await loadInbox(1);
        } catch (error) { dialogError(testDialog, error.message); }
    });

    /* ---- Bandeja: paginación, detalle (marca leída) y marcar todas ---- */
    const inbox = { page: 1, pages: 1 };
    const ICON_HTML = Object.fromEntries([...root.querySelectorAll(".st-notification-matrix .st-event")].map((el, index) => [["low_stock", "replenishment", "sales", "critical", "maintenance", "security"][index], el.querySelector("svg").outerHTML]));
    function renderInbox(data) {
        inbox.page = data.page; inbox.pages = data.pages;
        const list = $("st-recent-list");
        list.replaceChildren(...(data.items.length ? data.items.map((item) => {
            const li = node("li", `st-recent-row${item.read ? "" : " is-unread"}`); li.dataset.notificationId = item.id;
            const icon = node("span", `st-notification-icon st-tone-${{ critical: "red", warning: "orange" }[item.severity] || "blue"}`);
            icon.innerHTML = ICON_HTML[item.event] || "";
            const copy = node("div", "st-recent-copy");
            const title = node("strong", "", item.title); title.title = item.title;
            const message = node("p", "", item.message); message.title = item.message;
            copy.append(title, message);
            const button = node("button", "st-button", "Ver detalle"); button.type = "button"; button.dataset.notificationDetail = item.id; button.setAttribute("aria-label", `Ver detalle: ${item.title}`);
            li.append(icon, copy, node("span", "st-recent-time", formatDate(item.created_at)), node("span", "st-delivery-badge", item.channels.map((c) => c[0].toUpperCase() + c.slice(1)).join(" · ")), button);
            return li;
        }) : [node("li", "st-recent-row st-recent-empty", "Sin notificaciones en tu bandeja.")]));
        $("st-recent-summary").textContent = data.available ? `${data.total} en tu bandeja · ${data.unread} sin leer.` : "Bandeja no migrada: ejecute scripts/migrate_settings_full.py.";
        $("st-recent-read-all").disabled = !data.unread;
        $("st-recent-page-text").textContent = data.pages ? `Página ${data.page} de ${data.pages}` : "";
        $("st-recent-prev").disabled = data.page <= 1;
        $("st-recent-next").disabled = data.page >= data.pages;
        document.dispatchEvent(new CustomEvent("ix:notifications-changed"));
    }
    async function loadInbox(page) {
        try { renderInbox(await api(`/api/settings/notifications?page=${page}&per_page=4`)); }
        catch (error) { toast(error.message, true); }
    }
    $("st-recent-prev").addEventListener("click", () => loadInbox(inbox.page - 1));
    $("st-recent-next").addEventListener("click", () => loadInbox(inbox.page + 1));
    $("st-recent-read-all").addEventListener("click", async () => {
        try { const data = await api("/api/settings/notifications/read-all", { method: "POST" }); toast(`Marcadas como leídas: ${data.marked}.`); await loadInbox(inbox.page); }
        catch (error) { toast(error.message, true); }
    });
    const detailDialog = $("st-notification-dialog");
    root.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-notification-detail]");
        if (!button) return;
        try {
            const id = button.dataset.notificationDetail;
            const [data] = await Promise.all([api(`/api/settings/notifications/${id}`), api(`/api/settings/notifications/${id}/read`, { method: "POST" })]);
            $("st-notification-title").textContent = data.title;
            $("st-notification-message").textContent = data.message;
            $("st-notification-meta").replaceChildren(...[["Evento", data.event_label], ["Severidad", { critical: "Crítica", warning: "Advertencia", info: "Informativa" }[data.severity] || data.severity],
                ["Fecha", formatDate(data.created_at)], ["Estado", data.read ? "Leída" : "Sin leer (ahora leída)"]].flatMap(([k, v]) => [node("dt", "", k), node("dd", "", v)]));
            const deliveries = data.deliveries.length ? data.deliveries.map((d) => {
                const li = node("li", d.status === "sent" ? "is-ok" : d.status === "failed" ? "is-failed" : "");
                li.append(node("strong", "", { app: "App", email: "Correo", whatsapp: "WhatsApp", sms: "SMS" }[d.channel] || d.channel),
                    node("span", "", ` — ${{ sent: "enviada", failed: "fallida", queued: "en cola de resumen" }[d.status] || d.status}${d.detail ? `: ${d.detail}` : ""}`));
                return li;
            }) : [node("li", "", "Solo en la app.")];
            $("st-notification-deliveries").replaceChildren(...deliveries);
            const link = $("st-notification-link");
            link.hidden = !data.href; if (data.href) link.href = data.href;
            openDialog(detailDialog);
            await loadInbox(inbox.page);
        } catch (error) { toast(error.message, true); }
    });
})();
