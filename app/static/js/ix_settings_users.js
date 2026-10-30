/* Configuración › Usuarios y roles: lista/alta/edición de usuarios (sin borrado), roles dinámicos
   (crear, ver, editar, duplicar, activar/desactivar, eliminar) y matriz de permisos guardable. */
(() => {
    "use strict";
    const L = window.stLive;
    const usersRoot = document.getElementById("st-users");
    if (!L || !usersRoot) return;
    const { api, toast, node, formatDate, plural, confirm, openMenu, openDialog, dialogError } = L;
    const $ = (id) => document.getElementById(id);
    const PAGE_SIZE = 7; // ponytail: paginación en cliente sobre la lista completa (usuarios: decenas, no miles); subir a paginación de API si crece
    const TONES = { admin: "purple", inventario: "blue", vendedor: "green" };
    const AVATARS = ["blue", "teal", "purple", "orange", "pink"];
    const userCode = (id) => `U-${String(id).padStart(4, "0")}`;
    const initials = (name) => name.trim().split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toUpperCase() || "?";
    const rolesAvailable = usersRoot.dataset.rolesAvailable === "1";
    /* Acciones según permiso efectivo; el servidor sigue rechazando con 403 lo no autorizado. */
    const canUsers = usersRoot.dataset.canUsers === "1";
    const canRoles = usersRoot.dataset.canRoles === "1";
    const rolesEditable = rolesAvailable && canRoles;
    const state = { users: [], roles: JSON.parse(usersRoot.dataset.roles || "[]"), page: 1, selected: null, mode: "view",
        role: null, dirty: false, me: null };
    const roleBySlug = (slug) => state.roles.find((role) => role.slug === slug);
    const roleName = (slug) => roleBySlug(slug)?.name || slug;

    /* ---------------- Usuarios ---------------- */
    const els = { search: $("st-user-search"), filter: $("st-user-role-filter"), rows: $("st-user-rows"), count: $("st-user-count"),
        prev: $("st-user-prev"), page: $("st-user-page"), next: $("st-user-next"), card: $("st-user-card"), title: $("st-user-card-title"),
        id: $("st-user-id"), edit: $("st-user-edit"), menu: $("st-user-menu"), save: $("st-user-save"), cancel: $("st-user-cancel"),
        avatar: $("st-user-avatar"), name: $("st-user-name"), email: $("st-user-email"), username: $("st-user-username"),
        role: $("st-user-role"), active: $("st-user-active"), passwordWrap: $("st-user-password-wrap"), password: $("st-user-password"),
        lastAccess: $("st-user-last-access"), lastLogin: $("st-user-last-login"), created: $("st-user-created") };
    const visible = () => {
        const term = els.search.value.trim().toLowerCase();
        return state.users.filter((user) => (!els.filter.value || user.role === els.filter.value)
            && (!term || [user.name, user.email, user.username, roleName(user.role)].some((field) => (field || "").toLowerCase().includes(term))));
    };
    function renderList() {
        const rows = visible();
        const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
        state.page = Math.min(state.page, pages);
        const start = (state.page - 1) * PAGE_SIZE;
        const slice = rows.slice(start, start + PAGE_SIZE);
        els.rows.replaceChildren(...slice.map((user) => {
            const row = node("div", `st-user-row${user.id === state.selected?.id ? " is-selected" : ""}`);
            row.setAttribute("role", "option"); row.tabIndex = 0; row.dataset.userId = user.id;
            row.setAttribute("aria-selected", String(user.id === state.selected?.id));
            const avatar = node("span", `st-avatar st-avatar-${AVATARS[user.id % AVATARS.length]}`, initials(user.name));
            const copy = node("span", "st-user-copy");
            const name = node("strong", "", user.name); name.title = user.name;
            const email = node("span", "", user.email); email.title = user.email;
            copy.append(name, email);
            const badge = node("span", `st-role-badge st-tone-${TONES[user.role] || "blue"}`, roleName(user.role));
            const status = node("span", `st-user-status${user.is_active ? "" : " is-inactive"}`);
            status.append(node("i"), document.createTextNode(user.is_active ? "Activo" : "Inactivo"));
            row.append(avatar, copy, badge, status);
            row.addEventListener("click", () => select(user));
            row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(user); } });
            return row;
        }));
        if (!slice.length) els.rows.replaceChildren(node("div", "st-user-row st-user-empty", state.users.length ? "Ningún usuario coincide con la búsqueda." : "No hay usuarios registrados."));
        els.count.textContent = rows.length ? `Mostrando ${start + 1} a ${start + slice.length} de ${plural(rows.length, "usuario", "usuarios")}` : "0 usuarios";
        els.page.textContent = String(state.page);
        els.prev.disabled = state.page <= 1;
        els.next.disabled = state.page >= pages;
        state.roles.forEach((role) => {
            const count = state.users.filter((user) => user.role === role.slug).length;
            role.user_count = count;
            const target = usersRoot.querySelector(`[data-role-count="${role.slug}"]`);
            if (target) target.textContent = plural(count, "usuario", "usuarios");
        });
    }
    function setMode(mode) {
        state.mode = mode;
        els.card.dataset.mode = mode;
        const editing = mode !== "view";
        const creating = mode === "create";
        const isMe = state.selected?.id === state.me;
        els.name.readOnly = !editing;
        [els.email, els.username].forEach((input) => { input.readOnly = !creating; });
        els.role.disabled = !editing || (!creating && isMe);
        els.active.disabled = !editing || creating || isMe;
        els.passwordWrap.hidden = !editing;
        els.password.required = creating;
        els.password.placeholder = creating ? "Obligatoria" : "Dejar vacío para no cambiar";
        els.lastAccess.hidden = editing;
        els.edit.hidden = els.menu.hidden = editing;
        els.save.hidden = els.cancel.hidden = !editing;
        els.title.textContent = creating ? "Nuevo usuario" : "Información del usuario";
    }
    function fill(user) {
        els.id.textContent = user ? `ID: ${userCode(user.id)}` : "ID: —";
        els.avatar.textContent = user ? initials(user.name) : "—";
        els.name.value = user?.name || ""; els.email.value = user?.email || ""; els.username.value = user?.username || "";
        els.role.value = user?.role || els.role.options[0]?.value || ""; els.active.value = user ? (user.is_active ? "1" : "0") : "1";
        els.created.textContent = user ? formatDate(user.created_at) : "—";
        els.lastLogin.textContent = user?.last_login_at ? formatDate(user.last_login_at) : (user ? "Sin accesos registrados" : "—");
        els.password.value = "";
        els.edit.disabled = els.menu.disabled = !user || !canUsers;
    }
    function select(user) {
        state.selected = user;
        fill(user);
        setMode("view");
        renderList();
    }
    async function load(selectId) {
        if (!canUsers) { els.rows.replaceChildren(node("div", "st-user-row st-user-empty", "Su rol no administra usuarios.")); els.count.textContent = "—"; return; }
        try {
            const data = await api("/api/users");
            state.users = data.items;
            select(state.users.find((user) => user.id === (selectId ?? state.selected?.id)) || state.users[0] || null);
        } catch (error) { els.rows.replaceChildren(node("div", "st-user-row st-user-empty", error.message)); }
    }
    async function patchUser(user, payload, message) {
        try {
            const { user: updated } = await api(`/api/users/${user.id}`, { method: "PUT", body: JSON.stringify(payload) });
            toast(message || `Usuario ${updated.name} actualizado.`);
            await load(updated.id);
        } catch (error) { toast(error.message, true); }
    }
    els.search.addEventListener("input", () => { state.page = 1; renderList(); });
    els.filter.addEventListener("change", () => { state.page = 1; renderList(); });
    els.prev.addEventListener("click", () => { state.page -= 1; renderList(); });
    els.next.addEventListener("click", () => { state.page += 1; renderList(); });
    els.edit.addEventListener("click", () => { if (state.selected) { setMode("edit"); els.name.focus(); } });
    $("st-user-new")?.addEventListener("click", () => { state.selected = null; fill(null); setMode("create"); renderList(); els.name.focus(); });
    els.cancel.addEventListener("click", () => select(state.selected || state.users[0] || null));
    els.menu.addEventListener("click", () => {
        const user = state.selected;
        if (!user) return;
        const isMe = user.id === state.me;
        openMenu(els.menu, [
            { label: "Editar usuario", action: () => { setMode("edit"); els.name.focus(); } },
            { label: "Restablecer contraseña", action: () => { setMode("edit"); els.password.focus(); toast("Escriba la nueva contraseña y pulse Guardar."); } },
            { label: user.is_active ? "Desactivar usuario" : "Activar usuario", danger: user.is_active, disabled: isMe,
                title: isMe ? "No puede desactivar su propia cuenta." : "",
                action: async () => {
                    const ok = await confirm(user.is_active ? "Desactivar usuario" : "Activar usuario",
                        `${user.name} ${user.is_active ? "perderá el acceso de inmediato y sus sesiones quedarán rechazadas." : "recuperará el acceso al sistema."}`,
                        { danger: user.is_active, accept: user.is_active ? "Desactivar" : "Activar" });
                    if (ok) await patchUser(user, { is_active: !user.is_active }, `Usuario ${user.name} ${user.is_active ? "desactivado" : "activado"}.`);
                } },
        ]);
    });
    els.save.addEventListener("click", async () => {
        const payload = { name: els.name.value.trim(), role: els.role.value };
        if (els.password.value) payload.password = els.password.value;
        try {
            if (state.mode === "create") {
                Object.assign(payload, { email: els.email.value.trim(), username: els.username.value.trim(), password: els.password.value });
                // POST/PUT /api/users responden {message, user}.
                const { user: created } = await api("/api/users", { method: "POST", body: JSON.stringify(payload) });
                toast(`Usuario ${created.name} creado (${userCode(created.id)}).`);
                await load(created.id);
            } else {
                if (!els.active.disabled) payload.is_active = els.active.value === "1";
                if (els.role.disabled) delete payload.role;
                const { user: updated } = await api(`/api/users/${state.selected.id}`, { method: "PUT", body: JSON.stringify(payload) });
                toast(`Usuario ${updated.name} actualizado.`);
                await load(updated.id);
            }
        } catch (error) { toast(error.message, true); }
    });

    /* ---------------- Roles ---------------- */
    const roleRows = $("st-role-rows");
    const roleFilter = els.filter;
    function refreshRoleOptions() {
        const keep = roleFilter.value;
        roleFilter.replaceChildren(new Option("Todos los roles", ""), ...state.roles.map((role) => new Option(role.name, role.slug)));
        roleFilter.value = state.roles.some((role) => role.slug === keep) ? keep : "";
        const current = els.role.value;
        els.role.replaceChildren(...state.roles.filter((role) => role.is_active).map((role) => new Option(role.name, role.slug)));
        if (current) els.role.value = current;
    }
    function renderRoles() {
        roleRows.querySelectorAll(".st-role-row").forEach((row) => {
            const role = state.roles.find((item) => String(item.id) === row.dataset.roleId);
            if (!role) { row.remove(); return; }
            row.classList.toggle("is-inactive", !role.is_active);
            const strong = row.querySelector("strong");
            strong.replaceChildren(document.createTextNode(role.name));
            if (!role.is_active) { strong.append(document.createTextNode(" ")); strong.append(node("span", "st-badge is-muted", "Inactivo")); }
            const desc = row.querySelector("p"); desc.textContent = role.description; desc.title = role.description;
            row.querySelector("[data-role-count]").dataset.roleCount = role.slug;
            row.querySelector("[data-role-menu]").setAttribute("aria-pressed", String(role.id === state.role?.id));
        });
        state.roles.filter((role) => !roleRows.querySelector(`[data-role-id="${role.id}"]`)).forEach((role) => {
            const row = node("div", `st-role-row${role.is_active ? "" : " is-inactive"}`);
            row.dataset.roleId = role.id;
            const icon = node("span", "st-notification-icon st-tone-blue");
            icon.innerHTML = roleRows.querySelector(".st-notification-icon")?.innerHTML || "";
            const copy = node("div", "st-role-copy");
            const strong = node("strong", "", role.name);
            const desc = node("p", "", role.description); desc.title = role.description;
            copy.append(strong, desc);
            const count = node("span", "st-id", plural(role.user_count || 0, "usuario", "usuarios")); count.dataset.roleCount = role.slug;
            const menu = node("button", "st-button st-more"); menu.type = "button"; menu.id = `st-role-menu-${role.id}`;
            menu.dataset.menu = ""; menu.dataset.roleMenu = role.id; menu.setAttribute("aria-label", `Acciones del rol ${role.name}`);
            menu.setAttribute("aria-haspopup", "menu"); menu.setAttribute("aria-pressed", "false");
            menu.innerHTML = roleRows.querySelector(".st-more")?.innerHTML || "…";
            row.append(icon, copy, count, menu);
            roleRows.append(row);
        });
        refreshRoleOptions();
        renderList();
    }
    async function loadRoles(selectId) {
        if (!rolesAvailable) return;
        const data = await api("/api/settings/roles");
        state.roles = data.roles;
        renderRoles();
        showPermissions(state.roles.find((role) => role.id === (selectId ?? state.role?.id)) || state.roles[0] || null);
    }
    roleRows.addEventListener("click", (event) => {
        const button = event.target.closest("[data-role-menu]");
        if (!button) return;
        const role = state.roles.find((item) => String(item.id) === button.dataset.roleMenu);
        if (!role) return;
        const canDelete = rolesEditable && !role.is_system && role.user_count === 0;
        openMenu(button, [
            { label: "Ver detalles", action: () => openRoleDialog("view", role) },
            { label: "Ver permisos", action: () => showPermissions(role) },
            { label: "Editar", disabled: !rolesEditable, action: () => openRoleDialog("edit", role) },
            { label: "Duplicar", disabled: !rolesEditable, action: async () => {
                try { const data = await api(`/api/settings/roles/${role.id}/duplicate`, { method: "POST" }); toast(data.message); await loadRoles(data.role.id); }
                catch (error) { toast(error.message, true); }
            } },
            { label: role.is_active ? "Desactivar" : "Activar", disabled: !rolesEditable || role.slug === "admin",
                title: role.slug === "admin" ? "El rol Administrador no puede desactivarse." : "",
                action: async () => {
                    const ok = await confirm(role.is_active ? "Desactivar rol" : "Activar rol",
                        role.is_active ? `Los usuarios con el rol ${role.name} perderán todos sus permisos hasta reactivarlo.` : `El rol ${role.name} volverá a otorgar sus permisos.`,
                        { danger: role.is_active, accept: role.is_active ? "Desactivar" : "Activar" });
                    if (!ok) return;
                    try { const data = await api(`/api/settings/roles/${role.id}`, { method: "PUT", body: JSON.stringify({ is_active: !role.is_active }) }); toast(data.message); await loadRoles(role.id); }
                    catch (error) { toast(error.message, true); }
                } },
            { label: "Eliminar", danger: true, disabled: !canDelete,
                title: role.is_system ? "Los roles del sistema no pueden eliminarse." : role.user_count ? "Reasigne primero los usuarios de este rol." : "",
                action: async () => {
                    const ok = await confirm("Eliminar rol", `Se eliminará el rol ${role.name} de forma permanente.`, { danger: true, accept: "Eliminar" });
                    if (!ok) return;
                    try { const data = await api(`/api/settings/roles/${role.id}`, { method: "DELETE" }); toast(data.message); if (state.role?.id === role.id) state.role = null; await loadRoles(); }
                    catch (error) { toast(error.message, true); }
                } },
        ]);
    });

    /* ---- Diálogo de rol (crear / editar / ver) ---- */
    const roleDialog = $("st-role-dialog");
    const roleForm = $("st-role-form");
    let roleDialogMode = "create";
    let roleDialogTarget = null;
    function openRoleDialog(mode, role) {
        roleDialogMode = mode; roleDialogTarget = role || null;
        roleForm.reset();
        $("st-role-title").textContent = { create: "Nuevo rol", edit: `Editar rol: ${role?.name || ""}`, view: `Detalles del rol: ${role?.name || ""}` }[mode];
        roleForm.name.value = role?.name || "";
        roleForm.description.value = role?.description || "";
        roleForm.is_active.value = role && !role.is_active ? "0" : "1";
        roleForm.querySelectorAll("[name=permissions]").forEach((input) => { input.checked = Boolean(role?.permissions.includes(input.value)); });
        const readOnly = mode === "view";
        roleForm.querySelectorAll("input, select").forEach((input) => { input.disabled = readOnly; });
        $("st-role-submit").hidden = readOnly;
        const meta = $("st-role-meta");
        meta.hidden = !role;
        if (role) {
            meta.replaceChildren(...[["Identificador", role.slug], ["Tipo", role.is_system ? "Rol del sistema" : "Rol personalizado"],
                ["Usuarios asignados", String(role.user_count ?? 0)], ["Permisos", String(role.permissions.length)],
                ["Creado", formatDate(role.created_at)], ["Actualizado", formatDate(role.updated_at)]].flatMap(([label, value]) => [node("dt", "", label), node("dd", "", value)]));
        }
        openDialog(roleDialog);
    }
    $("st-role-new")?.addEventListener("click", () => openRoleDialog("create", null));
    roleForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (roleDialogMode === "view") { roleDialog.close(); return; }
        const payload = { name: roleForm.name.value.trim(), description: roleForm.description.value.trim(), is_active: roleForm.is_active.value === "1",
            permissions: [...roleForm.querySelectorAll("[name=permissions]:checked")].map((input) => input.value) };
        try {
            const data = roleDialogMode === "create"
                ? await api("/api/settings/roles", { method: "POST", body: JSON.stringify(payload) })
                : await api(`/api/settings/roles/${roleDialogTarget.id}`, { method: "PUT", body: JSON.stringify(payload) });
            roleDialog.close();
            toast(data.message);
            await loadRoles(data.role.id);
        } catch (error) { dialogError(roleDialog, error.message); }
    });

    /* ---------------- Matriz de permisos del rol seleccionado ---------------- */
    const permissionsRoot = $("st-permissions");
    const permissionInputs = () => [...permissionsRoot.querySelectorAll("[data-permission]")];
    function syncModules() {
        permissionsRoot.querySelectorAll("[data-module-toggle]").forEach((toggle) => {
            const inputs = permissionInputs().filter((input) => input.dataset.module === toggle.dataset.moduleToggle);
            const checked = inputs.filter((input) => input.checked).length;
            toggle.checked = checked === inputs.length && inputs.length > 0;
            toggle.indeterminate = checked > 0 && checked < inputs.length;
            permissionsRoot.querySelector(`[data-module-count="${toggle.dataset.moduleToggle}"]`).textContent = String(checked);
        });
        const total = permissionInputs().filter((input) => input.checked).length;
        $("st-permissions-footer").textContent = state.role ? `${plural(total, "permiso asignado", "permisos asignados")} al rol ${state.role.name}${state.dirty ? " (cambios sin guardar)" : ""}` : "Seleccione un rol.";
        $("st-permissions-toggle-all").textContent = total === permissionInputs().length ? "Deseleccionar todo" : "Seleccionar todo";
    }
    function showPermissions(role) {
        state.role = role; state.dirty = false;
        permissionsRoot.dataset.roleId = role ? role.id : "";
        $("st-permissions-title").textContent = `Permisos del rol: ${role ? role.name : "—"}`;
        const editable = Boolean(role) && rolesEditable;
        permissionInputs().forEach((input) => { input.checked = Boolean(role?.permissions.includes(input.dataset.permission)); input.disabled = !editable; });
        permissionsRoot.querySelectorAll("[data-module-toggle]").forEach((toggle) => { toggle.disabled = !editable; });
        $("st-permissions-toggle-all").disabled = !editable;
        $("st-permissions-description").textContent = editable ? "Marca los módulos completos o despliega cada uno para permisos individuales. Estos cambios esperan el botón superior «Guardar permisos del rol»."
            : rolesAvailable ? "Consulta de permisos: su rol no puede modificarlos." : "Permisos fijos (tablas de roles no migradas).";
        roleRows.querySelectorAll("[data-role-menu]").forEach((button) => button.setAttribute("aria-pressed", String(String(role?.id) === button.dataset.roleMenu)));
        syncModules();
    }
    permissionsRoot.addEventListener("change", (event) => {
        const target = event.target;
        if (target.matches("[data-module-toggle]")) {
            permissionInputs().filter((input) => input.dataset.module === target.dataset.moduleToggle).forEach((input) => { input.checked = target.checked; });
        } else if (!target.matches("[data-permission]")) return;
        state.dirty = true;
        syncModules();
    });
    permissionsRoot.addEventListener("click", (event) => {
        const expand = event.target.closest("[data-expand]");
        if (!expand) return;
        const detail = $(`st-permission-detail-${expand.dataset.expand}`);
        detail.hidden = !detail.hidden;
        expand.setAttribute("aria-expanded", String(!detail.hidden));
    });
    $("st-permissions-toggle-all").addEventListener("click", () => {
        const all = permissionInputs().every((input) => input.checked);
        permissionInputs().forEach((input) => { input.checked = !all; });
        state.dirty = true;
        syncModules();
    });
    L.onSave(async () => {
        if (!state.role) { toast("Seleccione un rol para guardar sus permisos.", true); return; }
        if (!rolesAvailable) { toast("Ejecute scripts/migrate_settings_full.py para administrar permisos.", true); return; }
        const permissions = permissionInputs().filter((input) => input.checked).map((input) => input.dataset.permission);
        const data = await api(`/api/settings/roles/${state.role.id}`, { method: "PUT", body: JSON.stringify({ permissions }) });
        toast(`Permisos del rol ${data.role.name} guardados.`);
        await loadRoles(state.role.id);
    });
    L.onReset(async () => {
        if (state.dirty && !(await confirm("Descartar cambios", "Se descartarán los permisos modificados y no guardados del rol seleccionado.", { accept: "Descartar" }))) return;
        await loadRoles(state.role?.id);
        toast("Permisos restablecidos a los guardados.");
    });

    showPermissions(state.roles[0] || null);
    api("/api/auth/me").then((me) => { state.me = me.id; return load(); }).catch(() => load()).then(() => loadRoles(state.role?.id)).catch((error) => toast(error.message, true));
})();
