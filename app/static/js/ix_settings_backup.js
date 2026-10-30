/* Configuración › Respaldos: creación por tipo, historial con acciones (detalle, descargar, verificar, Drive, eliminar),
   programación automática, ubicación, Google Drive y restauración con confirmación tipada. */
(() => {
    "use strict";
    const L = window.stLive;
    const root = document.getElementById("st-backup");
    if (!L || !root) return;
    const { api, toast, node, formatDate, formatBytes, plural, confirm, openMenu, openDialog, dialogError } = L;
    const $ = (id) => document.getElementById(id);
    let status = JSON.parse(root.dataset.status);
    const canManage = root.dataset.canManage === "1";
    const canWrite = root.dataset.canWrite === "1";
    const verifiedNow = new Set(); // verificados con SHA-256 en esta visita; "Registrado" = estado guardado
    const STATUS_LABELS = { completed: "Registrado", deleted: "Eliminado", missing: "Archivo no encontrado", corrupt: "Corrupto", failed: "Fallido" };
    const localize = () => root.parentElement.querySelectorAll("[data-iso]").forEach((el) => { if (el.dataset.iso) el.textContent = formatDate(el.dataset.iso); });
    localize();

    const historyToggle = $("st-backup-history-toggle");
    const history = $("st-backup-history");
    historyToggle.addEventListener("click", () => {
        history.hidden = !history.hidden;
        historyToggle.setAttribute("aria-expanded", String(!history.hidden));
        if (!history.hidden) history.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    const moreIcon = root.parentElement.querySelector(".st-more")?.innerHTML || "…";

    /* ---- Render del estado ---- */
    function statusCell(item) {
        const cell = node("td");
        const label = item.status === "completed" && verifiedNow.has(item.id) ? "Verificado ahora" : STATUS_LABELS[item.status] || item.status;
        cell.append(node("span", `st-security-status${item.status === "completed" ? "" : " is-failed"}`, label));
        if (item.remote_status === "uploaded") { cell.append(document.createTextNode(" ")); cell.append(node("span", "st-badge", "Drive")); }
        return cell;
    }
    function emptyRow(text, span) { const row = node("tr", "st-security-empty"); const cell = node("td", "", text); cell.colSpan = span; row.append(cell); return row; }
    function renderHistory(items) {
        $("st-backup-history-body").replaceChildren(...(items.length ? items.map((item) => {
            const row = node("tr"); row.dataset.backupId = item.id;
            const file = node("td", "st-backup-filename", item.filename); file.title = item.filename;
            const by = node("td", "", item.created_by || "—"); by.title = item.created_by || "";
            const actions = node("td");
            const menu = node("button", "st-button st-more"); menu.type = "button"; menu.id = `st-backup-menu-${item.id}`; menu.dataset.menu = ""; menu.dataset.backupMenu = item.id;
            menu.setAttribute("aria-label", `Acciones del respaldo ${item.filename}`); menu.setAttribute("aria-haspopup", "menu"); menu.innerHTML = moreIcon;
            actions.append(menu);
            row.append(node("td", "", formatDate(item.created_at)), file, node("td", "", item.kind_label), node("td", "", formatBytes(item.byte_size)), statusCell(item), by, actions);
            return row;
        }) : [emptyRow("Esta aplicación no ha registrado respaldos.", 7)]));
    }
    function renderRestores(items) {
        $("st-restore-history-body").replaceChildren(...(items.length ? items.map((item) => {
            const row = node("tr");
            const file = node("td", "st-backup-filename", item.backup_filename); file.title = item.backup_filename;
            const result = node("td"); const badge = node("span", `st-security-status${item.status === "completed" ? "" : " is-failed"}`, item.status === "completed" ? "Completada" : "Fallida"); badge.title = item.detail || ""; result.append(badge);
            row.append(node("td", "", formatDate(item.created_at)), file, node("td", "", item.kind_label), node("td", "", item.target_database), result, node("td", "", item.performed_by || "—"));
            return row;
        }) : [emptyRow("Sin restauraciones registradas.", 6)]));
    }
    function renderSchedule() {
        const task = status.schedule.task;
        $("st-backup-next").textContent = task.registered && task.next_run ? task.next_run : "No programado";
        $("st-schedule-summary").textContent = task.registered ? `Tarea «${status.schedule.task_name}» registrada (${task.state}).` : "Programa un respaldo periódico en este servidor.";
        $("st-schedule-task").textContent = !task.supported ? "El planificador no está disponible en este servidor."
            : task.registered ? `Próxima ejecución: ${task.next_run || "—"}${task.error ? ` · ${task.error}` : ""}`
                : canWrite ? "Guarda la configuración y registra la tarea para activar el respaldo automático." : "Registra la tarea con la configuración ya guardada.";
        const apply = $("st-schedule-apply");
        if (apply) apply.lastChild.textContent = task.registered ? "Actualizar tarea" : "Registrar tarea";
    }
    function renderSummary() {
        const last = status.last;
        $("st-backup-state").replaceChildren(node("span", `st-backup-success${last ? "" : " is-muted"}`, last ? "Último respaldo registrado" : status.available ? "Sin respaldos registrados por la aplicación" : "Respaldo no configurado"));
        $("st-backup-last").textContent = last ? formatDate(last.created_at) : "—";
        $("st-backup-count").textContent = String(status.count);
        $("st-backup-size").textContent = last ? formatBytes(last.byte_size) : "—";
        $("st-backup-usage").textContent = `${formatBytes(status.stored_bytes)} en ${plural(status.count, "archivo", "archivos")}`;
        $("st-backup-free").textContent = `Espacio disponible: ${formatBytes(status.free_bytes)} · vacío = carpeta por defecto (${status.default_directory}).`;
        $("st-backup-dir-badge").textContent = status.available ? "En uso" : "Inactiva";
        root.querySelector("[name='backup.directory']").title = status.directory;
        const select = $("st-restore-select");
        const keep = select.value;
        select.replaceChildren(new Option("Seleccione un respaldo…", ""), ...status.history.filter((item) => item.status === "completed")
            .map((item) => new Option(`${item.filename} · ${item.kind_label} · ${formatBytes(item.byte_size)}`, item.id)));
        select.value = keep;
        renderHistory(status.history);
        renderRestores(status.restores);
        renderSchedule();
    }
    async function refresh() { status = await api("/api/settings/backups"); renderSummary(); }

    /* ---- Crear respaldo ---- */
    const create = $("st-backup-create");
    create?.addEventListener("click", async () => {
        const kind = root.querySelector("[name=backup_type]:checked")?.value;
        if (!kind) { toast("Seleccione un tipo de respaldo.", true); return; }
        create.disabled = true;
        toast({ full: "Creando respaldo completo…", database: "Creando respaldo de la base de datos…", files: "Creando respaldo de archivos…" }[kind]);
        try {
            const data = await api("/api/settings/backups", { method: "POST", body: JSON.stringify({ kind }) });
            toast(`${data.message} ${data.backup.filename} (${formatBytes(data.backup.byte_size)}).`);
            await refresh();
        } catch (error) { toast(error.message, true); }
        finally { create.disabled = false; }
    });

    /* ---- Acciones del historial ---- */
    const inspectDialog = $("st-inspect-dialog");
    function meta(pairs) { return pairs.flatMap(([label, value]) => [node("dt", "", label), node("dd", "", value ?? "—")]); }
    async function inspect(id) {
        const data = await api(`/api/settings/backups/${id}`);
        $("st-inspect-title").textContent = data.filename;
        const pairs = [["Tipo", data.kind_label], ["Estado", STATUS_LABELS[data.status] || data.status], ["Archivo presente", data.exists ? "Sí" : "No"],
            ["Tamaño", formatBytes(data.byte_size)], ["SHA-256", data.sha256], ["Base de datos", data.database_name], ["Origen", data.source === "safety" ? "Seguridad previa a restauración" : data.source === "scheduled" ? "Automático" : "Manual"],
            ["Creado", formatDate(data.created_at)], ["Creado por", data.created_by], ["Google Drive", data.remote_status === "uploaded" ? "Copia subida" : "Sin copia"]];
        if (data.file_count != null) pairs.push(["Archivos incluidos", String(data.file_count)]);
        if (data.manifest) pairs.push(["Manifiesto", `formato v${data.manifest.format_version} · ${(data.manifest.components || []).join(", ") || "—"} · app ${data.manifest.app_version || "—"} · ${data.manifest.database_engine || ""} ${data.manifest.database_version || ""}`.trim()]);
        $("st-inspect-meta").replaceChildren(...meta(pairs));
        openDialog(inspectDialog);
    }
    history.addEventListener("click", (event) => {
        const button = event.target.closest("[data-backup-menu]");
        if (!button) return;
        const item = status.history.find((row) => String(row.id) === button.dataset.backupMenu);
        if (!item) return;
        const live = item.status !== "deleted" && canManage;
        const denied = canManage ? "" : "Requiere el permiso de administrar respaldos.";
        openMenu(button, [
            { label: "Ver detalle", action: () => inspect(item.id).catch((error) => toast(error.message, true)) },
            { label: "Descargar", disabled: !live, title: denied, action: () => { window.location.href = `/api/settings/backups/${item.id}/download`; } },
            { label: "Verificar integridad", disabled: !live, title: denied, action: async () => {
                try {
                    const data = await api(`/api/settings/backups/${item.id}/verify`, { method: "POST" });
                    if (data.ok) verifiedNow.add(item.id); else verifiedNow.delete(item.id);
                    toast(data.detail, !data.ok); await refresh();
                }
                catch (error) { toast(error.message, true); }
            } },
            { label: "Subir a Google Drive", disabled: !live || !status.drive.connected, title: denied || (status.drive.connected ? "" : "Google Drive no está conectado."), action: async () => {
                toast("Subiendo a Google Drive…");
                try { const data = await api(`/api/settings/backups/${item.id}/drive-upload`, { method: "POST" }); toast(data.message); await refresh(); }
                catch (error) { toast(error.message, true); }
            } },
            { label: "Eliminar", danger: true, disabled: !live, title: denied, action: async () => {
                if (!(await confirm("Eliminar respaldo", `Se borrará el archivo ${item.filename} de la carpeta de respaldos. El registro quedará marcado como eliminado.`, { danger: true, accept: "Eliminar" }))) return;
                try { const data = await api(`/api/settings/backups/${item.id}`, { method: "DELETE" }); toast(data.message); await refresh(); }
                catch (error) { toast(error.message, true); }
            } },
        ]);
    });

    /* ---- Programación automática ---- */
    $("st-schedule-apply")?.addEventListener("click", async () => {
        try {
            if (canWrite) await L.saveSettings(); // sin settings:write se registra la configuración ya guardada
            const data = await api("/api/settings/backups/schedule/apply", { method: "POST" });
            toast(data.message);
            await refresh();
        } catch (error) { toast(error.message, true); }
    });
    L.onPaint((settings) => { if ("backup.directory" in settings) refresh().catch(() => {}); });

    /* ---- Google Drive ---- */
    $("st-drive-disconnect")?.addEventListener("click", async () => {
        if (!(await confirm("Desconectar Google Drive", "Se revocará el acceso concedido y no se subirán más copias hasta reconectar.", { danger: true, accept: "Desconectar" }))) return;
        try { const data = await api("/api/settings/backups/drive", { method: "DELETE" }); toast(data.message); window.location.reload(); }
        catch (error) { toast(error.message, true); }
    });
    $("st-drive-help")?.addEventListener("click", () => { $("st-drive-redirect").textContent = `${window.location.origin}/api/settings/backups/drive/callback`; openDialog($("st-drive-help-dialog")); });
    if (new URLSearchParams(window.location.search).get("drive") === "connected") toast("Google Drive conectado correctamente.");
    $("st-backup-guide").addEventListener("click", () => openDialog($("st-guide-dialog")));

    /* ---- Restauración ---- */
    const restoreDialog = $("st-restore-dialog");
    const restoreForm = $("st-restore-form");
    let restoreTarget = null;
    restoreForm.confirmation.addEventListener("input", () => { $("st-restore-submit").disabled = restoreForm.confirmation.value.trim() !== "RESTAURAR"; });
    $("st-restore-button").addEventListener("click", async () => {
        const id = $("st-restore-select").value;
        if (!id) { toast("Seleccione un respaldo a restaurar.", true); return; }
        try {
            const preview = await api(`/api/settings/backups/${id}/restore-preview`);
            restoreTarget = preview.backup;
            $("st-restore-meta").replaceChildren(...meta([["Respaldo", preview.backup.filename], ["Tipo", preview.backup.kind_label], ["Fecha", formatDate(preview.backup.created_at)],
                ["Tamaño", formatBytes(preview.backup.byte_size)], ["Base de datos destino", preview.target_database],
                ["Carpetas de archivos", preview.file_roots.join(", ") || "ninguna"]]));
            const integrity = $("st-restore-integrity");
            integrity.className = `st-dialog-status ${preview.integrity.ok ? "is-ok" : "is-missing"}`;
            integrity.textContent = preview.integrity.detail;
            restoreForm.reset();
            $("st-restore-submit").disabled = true;
            dialogError(restoreDialog, preview.integrity.ok ? "" : "El respaldo no superó la verificación de integridad y no puede restaurarse.");
            restoreForm.confirmation.disabled = !preview.integrity.ok;
            openDialog(restoreDialog);
        } catch (error) { toast(error.message, true); }
    });
    restoreForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submit = $("st-restore-submit");
        submit.disabled = true;
        dialogError(restoreDialog, "");
        toast("Restaurando… no cierre esta página.");
        try {
            const data = await api(`/api/settings/backups/${restoreTarget.id}/restore`, { method: "POST", body: JSON.stringify({ confirmation: restoreForm.confirmation.value.trim() }) });
            restoreDialog.close();
            toast(`${data.message} ${data.restore.detail || ""}`.trim());
            await refresh();
        } catch (error) { dialogError(restoreDialog, error.message); submit.disabled = false; }
    });
})();
