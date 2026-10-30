/* Reference-only supplier simulation. No transport or persistence. */
(() => {
    "use strict";
    if (document.documentElement.dataset.referenceMode !== "1" ||
        location.pathname !== "/suppliers" || new URLSearchParams(location.search).get("ref") !== "1") return;
    const dialog = document.getElementById("sl-dialog");
    const trigger = [...document.querySelectorAll(".iv-sup-page .iv-actions button")]
        .find(button => button.textContent.trim() === "Nuevo proveedor");
    if (!dialog || !trigger) return;
    const form = document.getElementById("sl-form");
    const notes = document.getElementById("sl-notes");
    const counter = document.getElementById("sl-counter");
    const state = document.getElementById("sl-state");
    const error = document.getElementById("sl-error");
    const feedback = document.getElementById("sl-feedback");
    const required = [...form.querySelectorAll("[required]")];
    const previousOverflow = document.body.style.overflow;
    let feedbackTimer;
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-controls", dialog.id);
    function closeOtherLayers() {
        document.querySelectorAll("dialog[open]").forEach(layer => layer.close());
        document.querySelectorAll('[popover]:popover-open').forEach(layer => layer.hidePopover());
        document.querySelectorAll('[data-bs-toggle="dropdown"][aria-expanded="true"]')
            .forEach(button => window.bootstrap?.Dropdown?.getInstance(button)?.hide());
        document.querySelectorAll(".modal.show").forEach(layer => window.bootstrap?.Modal?.getInstance(layer)?.hide());
        document.getElementById("ix-shell")?.classList.remove("is-nav-open");
        document.getElementById("ix-menu-toggle")?.setAttribute("aria-expanded", "false");
    }
    trigger.addEventListener("click", () => {
        if (dialog.open) return;
        closeOtherLayers();
        clearTimeout(feedbackTimer);
        feedback.hidden = true;
        form.reset();
        required.forEach(field => field.removeAttribute("aria-invalid"));
        error.hidden = true;
        counter.textContent = "0/300";
        state.parentElement.classList.remove("is-inactive");
        document.body.style.overflow = "hidden";
        dialog.showModal();
        document.getElementById("sl-title").focus();
    });
    dialog.querySelectorAll("[data-sl-close]").forEach(button => {
        button.addEventListener("click", () => dialog.close());
    });
    dialog.addEventListener("close", () => {
        if (document.querySelector("dialog[open]")) return;
        document.body.style.overflow = previousOverflow;
        trigger.focus({preventScroll: true});
    });
    dialog.addEventListener("keydown", event => {
        if (event.key !== "Tab") return;
        const controls = [...dialog.querySelectorAll('button, input, select, textarea')];
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement.id === "sl-title")) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });
    notes.addEventListener("input", () => { counter.textContent = notes.value.length + "/300"; });
    state.addEventListener("change", () => {
        state.parentElement.classList.toggle("is-inactive", state.value === "Inactivo");
    });
    function isValid(field) {
        return field.value.trim() !== "" && field.validity.valid;
    }
    required.forEach(field => {
        const validate = () => {
            if (field.hasAttribute("aria-invalid")) field.setAttribute("aria-invalid", String(!isValid(field)));
            if (required.every(isValid)) error.hidden = true;
        };
        field.addEventListener("input", validate);
        field.addEventListener("change", validate);
    });
    form.addEventListener("submit", event => {
        event.preventDefault();
        const invalid = required.filter(field => !isValid(field));
        required.forEach(field => field.setAttribute("aria-invalid", String(invalid.includes(field))));
        error.hidden = invalid.length === 0;
        if (invalid.length) {
            invalid[0].focus();
            return;
        }
        dialog.close();
        feedback.textContent = "Simulación completada. No se ha creado ningún proveedor ni guardado datos.";
        feedback.hidden = false;
        feedbackTimer = setTimeout(() => { feedback.hidden = true; }, 6000);
    });
})();
