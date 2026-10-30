/* Logo selection is local only; the existing General save persists the multipart request. */
(() => {
    "use strict";
    const row = document.getElementById("st-company-logo");
    if (!row || !window.stLive || document.documentElement.dataset.referenceMode === "1") return;
    const input = document.getElementById("st-logo-file");
    const preview = document.getElementById("st-logo-preview");
    const name = document.getElementById("st-logo-name");
    const choose = document.getElementById("st-logo-choose");
    const remove = document.getElementById("st-logo-delete");
    let selected = null;
    let localUrl = null;
    let revision = 0;
    const clearUrl = () => { if (localUrl) URL.revokeObjectURL(localUrl); localUrl = null; };
    function paint() {
        const url = localUrl || row.dataset.savedUrl;
        preview.hidden = !url;
        if (url) preview.src = url;
        else preview.removeAttribute("src");
        choose.querySelector("span").textContent = url ? "Cambiar logo" : "Subir logo";
        remove.hidden = !url;
        name.textContent = selected ? selected.name : url ? "Logo actual" : "";
        name.title = name.textContent;
    }
    choose.addEventListener("click", () => input.click());
    input.addEventListener("change", async () => {
        const file = input.files[0];
        input.value = "";
        if (!file) return;
        const current = ++revision;
        try {
            if (file.size > 2 * 1024 * 1024) throw new Error("El archivo supera el límite de 2 MB.");
            const formats = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp" };
            const type = formats[file.name.split(".").pop().toLowerCase()];
            if (!type || type !== file.type) throw new Error("Formato de imagen no permitido.");
            const bytes = new Uint8Array(await file.slice(0, 12).arrayBuffer());
            const magic = bytes[0] === 137 && bytes[1] === 80 && bytes[2] === 78 && bytes[3] === 71 ? "image/png"
                : bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255 ? "image/jpeg"
                    : String.fromCharCode(...bytes.slice(0, 4)) === "RIFF" && String.fromCharCode(...bytes.slice(8, 12)) === "WEBP" ? "image/webp" : null;
            if (!magic) throw new Error("El archivo no es una imagen válida.");
            if (magic !== type) throw new Error("Formato de imagen no permitido.");
            const bitmap = await createImageBitmap(file).catch(() => { throw new Error("El archivo no es una imagen válida."); });
            bitmap.close();
            if (current !== revision) return;
            clearUrl();
            selected = file;
            localUrl = URL.createObjectURL(file);
            paint();
        } catch (error) { window.stLive.toast(error.message, true); }
    });
    remove.addEventListener("click", async () => {
        if (!await window.stLive.confirm("Eliminar logo", "Se eliminará el logo y volverá el icono predeterminado. Los demás ajustes sin guardar se conservarán. ¿Desea continuar?", { danger: true, accept: "Eliminar logo" })) return;
        remove.disabled = true;
        try {
            if (row.dataset.savedUrl) {
                await window.stLive.api(row.dataset.logoUrl, { method: "DELETE" });
            }
            revision += 1;
            selected = null;
            row.dataset.savedUrl = "";
            clearUrl();
            paint();
            paintShell("");
            window.stLive.toast("Logo eliminado.");
        } catch (error) { window.stLive.toast(error.message, true); }
        finally { remove.disabled = false; }
    });
    function paintShell(url) {
        const box = document.querySelector("[data-company-logo]");
        if (!box) return;
        box.replaceChildren();
        if (url) {
            box.classList.add("is-image");
            const image = document.createElement("img");
            image.alt = "Logo de la empresa";
            image.width = 40;
            image.height = 40;
            image.addEventListener("error", () => paintShell(""));
            image.src = url;
            box.appendChild(image);
            return;
        }
        box.classList.add("is-image");
        const image = document.createElement("img");
        image.alt = "Logo de Ferretería y Construcciones El Conejo C.A.";
        image.width = 40;
        image.height = 40;
        image.addEventListener("error", paintShellIcon);
        image.src = "/static/img/logo_oficial_el_conejo.png";
        box.appendChild(image);
    }
    function paintShellIcon() {
        const box = document.querySelector("[data-company-logo]");
        if (!box) return;
        box.replaceChildren();
        box.classList.remove("is-image");
        const icon = document.createElement("i");
        icon.className = "ti ti-tool";
        icon.setAttribute("aria-hidden", "true");
        box.appendChild(icon);
    }
    window.stCompanyLogo = {
        body(settings) {
            const body = new FormData();
            body.set("settings", JSON.stringify(settings));
            if (selected) body.set("logo", selected);
            return body;
        }
    };
    window.stLive.onSave(async () => {
        const button = document.getElementById("st-save");
        if (!button) return;
        button.disabled = true;
        try {
            const result = await window.stLive.saveSettings();
            if (result) {
                const key = result.settings["company.logo"];
                row.dataset.savedUrl = key ? `${row.dataset.logoUrl}?version=${encodeURIComponent(key)}` : "";
                selected = null;
                clearUrl();
                paint();
                paintShell(row.dataset.savedUrl);
                window.stLive.toast(result.message);
            }
        } finally { button.disabled = false; }
    });
    window.addEventListener("pagehide", clearUrl);
})();
