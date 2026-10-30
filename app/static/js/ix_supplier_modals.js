/* Supplier reference overlays: visual simulation only, no transport or storage. */
(() => {
    "use strict";
    if (document.documentElement.dataset.referenceMode !== "1" || location.pathname !== "/suppliers" ||
        new URLSearchParams(location.search).get("ref") !== "1") return;
    const dialogs = [...document.querySelectorAll(".sm-dialog")];
    const feedback = document.getElementById("sm-feedback");
    const originalOverflow = document.body.style.overflow;
    const triggers = new Map();
    let feedbackTimer;
    function notify(message) {
        clearTimeout(feedbackTimer);
        feedback.textContent = message;
        feedback.hidden = false;
        feedbackTimer = setTimeout(() => { feedback.hidden = true; }, 6000);
    }
    function open(dialog) {
        if (dialog.open) return;
        document.querySelectorAll("dialog[open]").forEach(layer => layer.close());
        document.querySelectorAll("[popover]:popover-open").forEach(layer => layer.hidePopover());
        document.getElementById("ix-shell")?.classList.remove("is-nav-open");
        document.getElementById("ix-menu-toggle")?.setAttribute("aria-expanded", "false");
        document.getElementById("sl-feedback").hidden = true;
        feedback.hidden = true;
        document.body.style.overflow = "hidden";
        dialog.showModal();
        dialog.querySelector("h2").focus();
    }
    function trapFocus(event, dialog) {
        if (event.key !== "Tab") return;
        const controls = [...dialog.querySelectorAll('button, input, select')]
            .filter(control => control.type !== "radio" || control.checked);
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement.tagName === "H2")) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }
    for (const [id, label] of [["sm-filters", "Filtros"], ["sm-export", "Exportar CSV"]]) {
        const dialog = document.getElementById(id);
        const trigger = [...document.querySelectorAll(".iv-sup-page .iv-actions button")]
            .find(button => button.textContent.trim() === label);
        triggers.set(dialog, trigger);
        trigger.setAttribute("aria-haspopup", "dialog");
        trigger.setAttribute("aria-controls", id);
        trigger.addEventListener("click", () => open(dialog));
    }
    dialogs.forEach(dialog => {
        dialog.querySelectorAll("[data-sm-close]").forEach(button => {
            button.addEventListener("click", () => dialog.close());
        });
        dialog.addEventListener("keydown", event => trapFocus(event, dialog));
        dialog.addEventListener("close", () => {
            if (document.querySelector("dialog[open]")) return;
            document.body.style.overflow = originalOverflow;
            triggers.get(dialog).focus({preventScroll: true});
        });
    });
    const filterForm = document.getElementById("sm-filter-form");
    filterForm.addEventListener("reset", () => {
        filterForm.querySelectorAll(".sm-date input").forEach(input => { input.type = "text"; });
    });
    filterForm.addEventListener("submit", event => {
        event.preventDefault();
        document.getElementById("sm-filters").close();
        notify("Filtros simulados en modo referencia. La tabla de ejemplo conserva sus datos originales.");
    });
    document.getElementById("sm-export-form").addEventListener("submit", event => {
        event.preventDefault();
        const format = document.querySelector('input[name="sm-format"]:checked').value.toUpperCase();
        document.getElementById("sm-export").close();
        notify(`Exportación ${format} simulada. No se ha generado ni descargado ningún archivo.`);
    });
})();
