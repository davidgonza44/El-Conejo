/* Reabastecimiento de referencia: estado efímero, sin red, descargas ni almacenamiento. */
(() => {
    "use strict";
    const data = document.getElementById("rl-fixtures");
    if (!data || document.documentElement.dataset.referenceMode !== "1" ||
        location.pathname !== "/predictive/replenishment" ||
        new URLSearchParams(location.search).get("ref") !== "1") return;
    const $ = (id) => document.getElementById("rl-" + id);
    const dialogs = ["filters", "export", "order"].map($);
    const form = $("filter-form");
    const inline = document.querySelector(".px-filters");
    const search = inline.querySelector('input[type="search"]');
    const tbody = document.querySelector(".is-replenishment tbody");
    const originalRows = [...tbody.rows];
    const summary = document.querySelector(".px-pagination-summary");
    const pages = document.querySelector(".px-pages");
    const initialSummary = summary.textContent;
    const initialPages = pages.innerHTML;
    const money = (n) => "$ " + n.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
    const numeric = (text) => Number.parseFloat(text.replace(/[$,]/g, "").trim());
    const iso = (text) => text ? text.split("/").reverse().join("-") : "";
    // Only the eight existing reference rows are available for interactive filtering.
    // Supplier/critical metadata below is synthetic, never a database lookup.
    const rows = JSON.parse(data.textContent).map((row, index) => ({
        ...row, stock: numeric(row.stock), demand: numeric(row.demand), cost: numeric(row.cost),
        qty: numeric(row.qty), date: iso(row.date), priority: row.prio[0],
        supplier: index < 4 ? "Distribuidora Andina" : "Suministros del Centro",
        critical: index < 5, horizon: Number(row.date.slice(0, 2)),
    }));
    let active = null, opener = null, previousOverflow = "", calendarInput = null;
    let month = new Date(2024, 5, 1), applied = {}, feedbackTimer;
    const inlineSelects = new Map();

    function closeCalendar(focus = false) {
        $("calendar").hidden = true;
        if (calendarInput) {
            calendarInput.setAttribute("aria-expanded", "false");
            if (focus) calendarInput.focus();
        }
        calendarInput = null;
    }
    function closeLayer(restore = true) {
        closeCalendar();
        if (!active) return;
        active.close();
        active = null;
        document.body.style.overflow = previousOverflow;
        if (restore && opener?.isConnected) opener.focus();
    }
    function openLayer(dialog, trigger) {
        closeLayer(false);
        opener = trigger;
        previousOverflow = document.body.style.overflow;
        active = dialog;
        if (dialog === $("filters")) loadFilters();
        $("feedback").hidden = true;
        dialog.showModal();
        document.body.style.overflow = "hidden";
        dialog.querySelector("h2").focus();
    }
    function notify(message) {
        clearTimeout(feedbackTimer);
        $("feedback").textContent = message;
        $("feedback").hidden = false;
        feedbackTimer = setTimeout(() => { $("feedback").hidden = true; }, 6000);
    }
    document.querySelectorAll(".px-actions > button").forEach((button, index) => {
        button.setAttribute("aria-haspopup", "dialog");
        button.setAttribute("aria-controls", dialogs[index].id);
        button.addEventListener("click", () => openLayer(dialogs[index], button));
    });
    dialogs.forEach((dialog) => {
        dialog.addEventListener("keydown", (event) => {
            if (event.key !== "Tab") return;
            const controls = [...dialog.querySelectorAll('button,input,select,textarea,[tabindex="0"]')]
                .filter(node => !node.disabled && node.getClientRects().length);
            const first = controls[0], last = controls[controls.length - 1];
            const outside = !controls.includes(document.activeElement);
            if ((event.shiftKey && (document.activeElement === first || outside)) ||
                (!event.shiftKey && (document.activeElement === last || outside))) {
                event.preventDefault();
                (event.shiftKey ? last : first).focus();
            }
        });
        dialog.querySelectorAll("[data-rl-close]").forEach(button => button.addEventListener("click", () => closeLayer()));
        dialog.addEventListener("cancel", (event) => {
            event.preventDefault();
            if (!$("calendar").hidden) closeCalendar(true);
            else closeLayer();
        });
        dialog.addEventListener("click", (event) => {
            if (event.target !== dialog) return;
            const box = dialog.getBoundingClientRect();
            if (event.clientX < box.left || event.clientX > box.right ||
                event.clientY < box.top || event.clientY > box.bottom) closeLayer();
        });
    });

    // One shared visual calendar is moved into the active top-layer dialog.
    function renderCalendar() {
        $("month").textContent = month.toLocaleDateString("es", {month: "long", year: "numeric"});
        const days = $("days");
        days.replaceChildren();
        const offset = (month.getDay() + 6) % 7;
        for (let i = 0; i < offset; i++) days.append(document.createElement("span"));
        const count = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
        for (let day = 1; day <= count; day++) {
            const date = new Date(month.getFullYear(), month.getMonth(), day);
            const value = [day, month.getMonth() + 1, month.getFullYear()]
                .map((part) => String(part).padStart(2, "0")).join("/");
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = String(day);
            button.setAttribute("aria-label", date.toLocaleDateString("es", {day: "numeric", month: "long", year: "numeric"}));
            button.setAttribute("aria-pressed", String(calendarInput.value === value));
            button.addEventListener("click", () => {
                calendarInput.value = value;
                calendarInput.dispatchEvent(new Event("change", {bubbles: true}));
                closeCalendar(true);
            });
            days.append(button);
        }
    }
    function positionCalendar() {
        if (!calendarInput) return;
        const rect = calendarInput.getBoundingClientRect();
        const calendar = $("calendar");
        const height = calendar.offsetHeight;
        calendar.style.left = Math.max(8, Math.min(rect.left, innerWidth - 274)) + "px";
        const top = rect.bottom + height + 5 <= innerHeight - 8 ? rect.bottom + 5 : rect.top - height - 5;
        calendar.style.top = Math.max(8, top) + "px";
    }
    function openCalendar(input) {
        if (calendarInput === input) { closeCalendar(); return; }
        closeCalendar();
        calendarInput = input;
        const value = iso(input.value) || "2024-06-15";
        month = new Date(Number(value.slice(0, 4)), Number(value.slice(5, 7)) - 1, 1);
        active.append($("calendar"));
        $("calendar").hidden = false;
        input.setAttribute("aria-expanded", "true");
        renderCalendar();
        positionCalendar();
        ($("days").querySelector('[aria-pressed="true"]') || $("days").querySelector("button")).focus();
    }
    document.querySelectorAll("[data-rl-date]").forEach((input) => {
        input.addEventListener("click", () => openCalendar(input));
        input.addEventListener("keydown", (event) => {
            if (!["Enter", " ", "ArrowDown"].includes(event.key)) return;
            event.preventDefault();
            openCalendar(input);
        });
    });
    document.querySelectorAll("[data-rl-month]").forEach(button => button.addEventListener("click", () => {
        month.setMonth(month.getMonth() + Number(button.dataset.rlMonth));
        renderCalendar();
        positionCalendar();
    }));
    $("date-clear").addEventListener("click", () => {
        calendarInput.value = "";
        closeCalendar(true);
    });
    document.addEventListener("pointerdown", (event) => {
        if (calendarInput && event.target !== calendarInput && !$("calendar").contains(event.target)) closeCalendar();
    });
    document.addEventListener("keydown", (event) => {
        if (!calendarInput) return;
        if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            closeCalendar(true);
        }
        if (!event.target.closest("#rl-days") || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
        event.preventDefault();
        const buttons = [...$("days").querySelectorAll("button")];
        const step = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7}[event.key];
        buttons[Math.max(0, Math.min(buttons.length - 1, buttons.indexOf(event.target) + step))].focus();
    }, true);
    window.addEventListener("resize", positionCalendar);
    dialogs.forEach(dialog => dialog.addEventListener("scroll", () => closeCalendar(), true));

    // Read-only quantities match the reference; all totals derive from the same fixture.
    const subtotal = rows.slice(0, 4).reduce((sum, row, index) => {
        document.querySelectorAll("[data-rl-unit]")[index].textContent = money(row.cost / row.qty);
        document.querySelectorAll("[data-rl-subtotal]")[index].textContent = money(row.cost);
        return sum + row.cost;
    }, 0);
    $("subtotal").textContent = money(subtotal);
    $("tax").textContent = money(subtotal * 0.16);
    $("total").textContent = money(subtotal * 1.16);
    $("notes").addEventListener("input", () => { $("note-count").textContent = $("notes").value.length + "/500"; });
    $("order-form").addEventListener("submit", (event) => {
        event.preventDefault();
        if (!$("delivery").value) { openCalendar($("delivery")); return; }
        closeLayer();
        notify("Simulación completada: no se creó ninguna orden de compra.");
    });
    $("export-form").addEventListener("submit", (event) => {
        event.preventDefault();
        const format = new FormData($("export-form")).get("format").toUpperCase();
        closeLayer();
        notify("Exportación " + format + " simulada: no se generó ni descargó ningún archivo.");
    });

    function readFilters() {
        const values = Object.fromEntries(new FormData(form));
        values.from = iso(values.from);
        values.to = iso(values.to);
        return values;
    }
    function loadFilters() {
        form.reset();
        Object.entries(applied).forEach(([name, value]) => {
            const field = form.elements.namedItem(name);
            if (!field) return;
            if (field.type === "checkbox") field.checked = Boolean(value);
            else field.value = ["from", "to"].includes(name) ? value.split("-").reverse().join("/") : value;
        });
        $("filter-error").hidden = true;
    }
    function inRange(row, values, key) {
        return (!values[key + "-min"] || row[key] >= Number(values[key + "-min"])) &&
            (!values[key + "-max"] || row[key] <= Number(values[key + "-max"]));
    }
    function matches(row, values) {
        const term = search.value.trim().toLocaleLowerCase("es");
        const textMatches = (row.p.name + " " + row.p.sku).toLocaleLowerCase("es").includes(term);
        const choices = ["supplier", "priority"].every(key => !values[key] || row[key] === values[key]);
        const ranges = ["stock", "demand", "cost"].every(key => inRange(row, values, key));
        return textMatches && choices && ranges &&
            (!values.category || row.cat === values.category) &&
            (!values.high || row.priority === "Alta") && (!values.critical || row.critical) &&
            (!values.from || row.date >= values.from) && (!values.to || row.date <= values.to) &&
            row.horizon <= Number(values.horizon || 30);
    }
    function hasFilters() {
        return search.value.trim() || Object.entries(applied).some(([key, value]) => value && !(key === "horizon" && value === "30"));
    }
    function renderRows() {
        const filtered = rows.map((row, index) => ({row, index})).filter(({row}) => matches(row, applied));
        tbody.replaceChildren(...filtered.map(({index}) => originalRows[index]));
        if (!filtered.length) {
            const cell = tbody.insertRow().insertCell();
            cell.colSpan = 10;
            cell.textContent = "No hay recomendaciones que coincidan con los filtros.";
            cell.style.textAlign = "center";
        }
        if (!hasFilters()) {
            summary.textContent = initialSummary;
            pages.innerHTML = initialPages;
        } else {
            summary.textContent = filtered.length ? "Mostrando 1 a " + filtered.length + " de " + filtered.length + " productos" : "Mostrando 0 de 0 productos";
            const button = document.createElement("button");
            button.type = "button";
            button.className = "px-pg is-active";
            button.textContent = "1";
            button.setAttribute("aria-current", "page");
            button.disabled = true;
            pages.replaceChildren(button);
        }
    }
    function syncInline() {
        inlineSelects.forEach((select, key) => {
            select.value = applied[key] || (key === "horizon" ? "30" : "");
            select.parentElement.firstElementChild.textContent = select.selectedOptions[0].textContent;
        });
    }
    function resetFilters() {
        applied = {};
        search.value = "";
        form.reset();
        closeCalendar();
        $("filter-error").hidden = true;
        syncInline();
        renderRows();
    }
    function validateRanges(values) {
        const invalid = ["stock", "demand", "cost"].some(key =>
            values[key + "-min"] !== "" && values[key + "-max"] !== "" &&
            Number(values[key + "-min"]) > Number(values[key + "-max"]));
        if (invalid) return "El mínimo no puede ser mayor que el máximo.";
        if (values.from && values.to && values.from > values.to) return "La fecha Desde no puede ser posterior a Hasta.";
        return "";
    }
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const values = readFilters();
        const error = validateRanges(values);
        $("filter-error").textContent = error;
        $("filter-error").hidden = !error;
        if (error) return;
        applied = values;
        syncInline();
        renderRows();
        closeLayer();
    });
    // Reset only on an explicit reset click, not when loading the last applied draft.
    form.querySelector('[type="reset"]').addEventListener("click", (event) => { event.preventDefault(); resetFilters(); });
    inline.addEventListener("reset", (event) => { event.preventDefault(); resetFilters(); });
    search.addEventListener("input", renderRows);
    ["category", "supplier", "horizon"].forEach((key, index) => {
        const box = inline.querySelectorAll(".px-select-box")[index];
        const select = $(key).cloneNode(true);
        select.id = "rl-inline-" + key;
        select.setAttribute("aria-label", inline.querySelectorAll(".px-select-label")[index].textContent);
        box.classList.add("rl-inline");
        box.append(select);
        inlineSelects.set(key, select);
        select.addEventListener("change", () => {
            applied[key] = select.value;
            syncInline();
            renderRows();
        });
    });
})();
