/* Shell "ix" compartido: menú lateral en móvil y grupos desplegables del sidebar. */
(function () {
    "use strict";

    const accountToggle = document.getElementById("ix-account-toggle");
    const accountMenu = document.getElementById("ix-account-menu");
    function closeAccount() {
        if (!accountMenu) return;
        accountMenu.hidden = true;
        accountToggle.setAttribute("aria-expanded", "false");
    }
    accountToggle?.addEventListener("click", () => {
        accountMenu.hidden = !accountMenu.hidden;
        accountToggle.setAttribute("aria-expanded", String(!accountMenu.hidden));
    });
    document.addEventListener("pointerdown", event => {
        if (accountMenu && !accountMenu.contains(event.target) && !accountToggle.contains(event.target)) closeAccount();
    });
    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && accountMenu && !accountMenu.hidden) { closeAccount(); accountToggle.focus(); }
    });
    document.getElementById("ix-account-logout")?.addEventListener("click", async event => {
        if (document.documentElement.dataset.referenceMode === "1") return;
        const button = event.currentTarget;
        button.disabled = true;
        try {
            const response = await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
            if (!response.ok) throw new Error("No se pudo cerrar la sesión. Intente nuevamente.");
            location.assign("/login");
        } catch (error) {
            button.disabled = false;
            button.title = error.message;
            let message = document.getElementById("ix-account-error");
            if (!message) { message = document.createElement("p"); message.id = "ix-account-error"; message.setAttribute("role", "alert"); accountMenu.append(message); }
            message.textContent = error.message;
        }
    });

    const shell = document.getElementById("ix-shell");
    const toggle = document.getElementById("ix-menu-toggle");
    if (shell && toggle) {
        toggle.addEventListener("click", () => {
            const open = shell.classList.toggle("is-nav-open");
            toggle.setAttribute("aria-expanded", String(open));
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && shell.classList.contains("is-nav-open")) {
                shell.classList.remove("is-nav-open");
                toggle.setAttribute("aria-expanded", "false");
                toggle.focus({ preventScroll: true });
            }
        });
    }

    document.querySelectorAll(".ix-nav-group > button").forEach((button) => {
        button.addEventListener("click", () => {
            const group = button.parentElement;
            const list = document.getElementById(button.getAttribute("aria-controls"));
            const open = group.classList.toggle("is-open");
            button.classList.toggle("is-active", open);
            button.setAttribute("aria-expanded", String(open));
            if (list) list.hidden = !open;
            const chevron = button.querySelector(".ix-nav-chevron");
            if (chevron) {
                chevron.classList.toggle("ti-chevron-up", open);
                chevron.classList.toggle("ti-chevron-right", !open);
            }
        });
    });

    const brandLogo = document.querySelector("[data-company-logo]");
    const brandImage = brandLogo && brandLogo.querySelector("img");
    if (brandImage) {
        const restoreBrand = () => {
            brandImage.remove();
            brandLogo.classList.remove("is-image");
            if (!brandLogo.querySelector(".ti-tool")) {
                const icon = document.createElement("i");
                icon.className = "ti ti-tool";
                icon.setAttribute("aria-hidden", "true");
                brandLogo.appendChild(icon);
            }
        };
        brandImage.addEventListener("error", restoreBrand);
        if (brandImage.complete && brandImage.naturalWidth === 0) restoreBrand();
    }

    /* ---- Búsqueda global y campana (solo en vivo; la referencia es maqueta estática) ---- */
    if (document.documentElement.dataset.referenceMode === "1") return;
    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };
    async function api(url, options = {}) {
        const response = await fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin", ...options });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `Error ${response.status}`);
        return data;
    }

    const input = document.getElementById("ix-search-input");
    const results = document.getElementById("ix-search-results");
    if (input && results) {
        const GROUPS = [
            ["products", "Productos", (i) => [i.name, i.code]],
            ["customers", "Clientes", (i) => [i.name, i.tax_id || i.phone || ""]],
            ["sales", "Ventas", (i) => [i.sale_number, i.customer]],
        ];
        let timer, requestId = 0, active = -1;
        const options = () => [...results.querySelectorAll('[role="option"]')];
        function close() {
            results.hidden = true;
            input.setAttribute("aria-expanded", "false");
            input.removeAttribute("aria-activedescendant");
            active = -1;
        }
        function open() {
            results.hidden = false;
            input.setAttribute("aria-expanded", "true");
        }
        function highlight(index) {
            const items = options();
            if (!items.length) return;
            active = (index + items.length) % items.length;
            items.forEach((item, i) => item.setAttribute("aria-selected", String(i === active)));
            input.setAttribute("aria-activedescendant", items[active].id);
            items[active].scrollIntoView({ block: "nearest" });
        }
        function render(data) {
            results.replaceChildren();
            let n = 0;
            GROUPS.forEach(([key, label, fields]) => {
                const items = data.groups[key] || [];
                if (!items.length) return;
                results.append(el("p", "ix-search-group", label));
                items.forEach((item) => {
                    const [title, sub] = fields(item);
                    const a = el("a", "ix-search-option");
                    a.href = item.href; a.id = `ix-search-opt-${n++}`;
                    a.setAttribute("role", "option"); a.setAttribute("aria-selected", "false");
                    a.append(el("span", "ix-search-title", title), el("span", "ix-search-sub", sub));
                    a.title = `${title} · ${sub}`;
                    results.append(a);
                });
            });
            if (!n) results.append(el("p", "ix-search-empty", `Sin resultados para “${data.query}”.`));
            open();
        }
        async function lookup() {
            const q = input.value.trim();
            if (q.length < 2) { close(); return; }
            const current = ++requestId;
            try {
                const data = await api(`/api/shell/search?q=${encodeURIComponent(q)}`);
                if (current === requestId) render(data);
            } catch (error) {
                if (current !== requestId) return;
                results.replaceChildren(el("p", "ix-search-empty", error.message));
                open();
            }
        }
        input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(lookup, 250); });
        input.addEventListener("focus", () => { if (results.childElementCount && input.value.trim().length >= 2) open(); });
        input.addEventListener("keydown", (event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                if (results.hidden) { lookup(); return; }
                highlight(active + (event.key === "ArrowDown" ? 1 : -1));
            } else if (event.key === "Enter") {
                const target = options()[active] || options()[0];
                if (target && !results.hidden) { event.preventDefault(); target.click(); }
            } else if (event.key === "Escape" && !results.hidden) {
                event.preventDefault(); close();
            }
        });
        document.addEventListener("pointerdown", (event) => {
            if (!results.hidden && !input.parentElement.parentElement.contains(event.target)) close();
        });
    }

    const bell = document.getElementById("ix-bell");
    const panel = document.getElementById("ix-bell-panel");
    if (bell && panel) {
        const badge = document.getElementById("ix-bell-badge");
        const list = document.getElementById("ix-bell-list");
        function paintBadge(count) {
            badge.hidden = count === 0;
            badge.textContent = count > 99 ? "99+" : String(count);
            bell.setAttribute("aria-label", count ? `Alertas de inventario: ${count}` : "Alertas de inventario: ninguna");
        }
        /* Miniatura real si existe; si falta o falla, icono de respaldo (nunca imagen rota). */
        function thumb(src) {
            const wrap = el("span", "ix-thumb");
            wrap.setAttribute("aria-hidden", "true");
            wrap.append(el("i", "ti ti-package"));
            if (src) {
                const img = el("img");
                img.alt = ""; img.loading = "lazy";
                img.addEventListener("error", () => img.remove());
                img.src = src;
                wrap.append(img);
            }
            return wrap;
        }
        /* Alertas de stock en vivo (comportamiento previo; se usa si la bandeja persistente no está migrada). */
        function paintAlerts(data) {
            list.replaceChildren(...data.items.map((item) => {
                const li = el("li", "ix-bell-item");
                const badgeEl = el("span", `ix-badge is-${item.status === "agotado" ? "red" : "orange"}`, item.status_label);
                const text = el("span", "ix-row-text");
                const name = el("span", "ix-row-title", item.name); name.title = item.name;
                text.append(name, el("span", "ix-row-desc", `${item.status_label} · ${item.current_stock} unidades (mín. ${item.minimum_stock})`));
                li.append(thumb(item.image_url), text, badgeEl);
                return li;
            }));
            if (!data.items.length) list.replaceChildren(el("li", "ix-bell-empty", data.disabled ? "Alertas de inventario desactivadas en Configuración." : "Sin alertas de inventario."));
            document.getElementById("ix-bell-all").href = data.href;
        }
        /* Bandeja persistente: no leídas primero; clic marca leída y navega al registro si lo hay. */
        const SEVERITY = { critical: "red", warning: "orange", info: "blue" };
        function paintNotifications(data) {
            list.replaceChildren(...data.items.map((item) => {
                const li = el("li", `ix-bell-item${item.read ? "" : " is-unread"}`);
                const button = el("button", "ix-bell-row"); button.type = "button";
                button.setAttribute("aria-label", `${item.read ? "" : "Sin leer: "}${item.title}`);
                const text = el("span", "ix-row-text");
                const name = el("span", "ix-row-title", item.title); name.title = item.title;
                text.append(name, el("span", "ix-row-desc", item.message));
                button.append(text, el("span", `ix-badge is-${SEVERITY[item.severity] || "blue"}`, item.event_label));
                button.addEventListener("click", async () => {
                    try { if (!item.read) await api(`/api/shell/notifications/${item.id}/read`, { method: "POST" }); } catch (error) { /* la navegación sigue */ }
                    if (item.href) window.location.href = item.href; else load();
                });
                li.append(button);
                return li;
            }));
            if (!data.items.length) list.replaceChildren(el("li", "ix-bell-empty", data.app_enabled === false ? "Notificaciones en la app desactivadas en Configuración." : "Sin notificaciones."));
            const footer = document.getElementById("ix-bell-all");
            footer.href = data.href;
            let readAll = document.getElementById("ix-bell-read-all");
            if (!readAll) {
                readAll = el("button", "ix-bell-footer ix-bell-read-all", "Marcar todas como leídas"); readAll.type = "button"; readAll.id = "ix-bell-read-all";
                readAll.addEventListener("click", async () => { try { await api("/api/shell/notifications/read-all", { method: "POST" }); await load(); } catch (error) { list.replaceChildren(el("li", "ix-bell-empty", error.message)); } });
                footer.before(readAll);
            }
            readAll.hidden = !data.unread;
        }
        async function load() {
            try {
                const data = await api("/api/shell/notifications");
                if (data.available) {
                    panel.querySelector(".ix-bell-title").textContent = "Notificaciones";
                    paintBadge(data.unread);
                    bell.setAttribute("aria-label", data.unread ? `Notificaciones: ${data.unread} sin leer` : "Notificaciones: ninguna sin leer");
                    paintNotifications(data);
                    return;
                }
                const alerts = await api("/api/shell/alerts");
                paintBadge(alerts.count);
                paintAlerts(alerts);
            } catch (error) {
                badge.hidden = true;
                list.replaceChildren(el("li", "ix-bell-empty", error.message));
            }
        }
        document.addEventListener("ix:notifications-changed", () => load());
        function toggle(force) {
            const open = force ?? panel.hidden;
            panel.hidden = !open;
            bell.setAttribute("aria-expanded", String(open));
            if (open) { load(); panel.querySelector("a").focus({ preventScroll: true }); }
        }
        bell.addEventListener("click", () => toggle());
        document.addEventListener("pointerdown", (event) => {
            if (!panel.hidden && !bell.parentElement.contains(event.target)) toggle(false);
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !panel.hidden) { toggle(false); bell.focus(); }
        });
        load();
    }
})();
