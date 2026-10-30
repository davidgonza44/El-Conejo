/* Current profile; password, photo and session operations reuse their canonical APIs. */
(() => {
    "use strict";
    const $ = id => document.getElementById(id);
    const root = $("profile-root");
    const reference = root.dataset.reference === "1";
    $("profile-per-page")?.addEventListener("change", () => $("profile-activity-filters").requestSubmit());
    const notice = message => { $("page-alert").textContent = message; $("page-alert").hidden = false; };
    async function api(path, method, body) {
        if (reference) throw new Error("La vista de referencia no permite cambios.");
        const headers = { "X-CSRFToken": root.dataset.csrf };
        if (!(body instanceof FormData)) headers["Content-Type"] = "application/json";
        const response = await fetch(path, { method, credentials: "same-origin", headers,
            body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body) });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "No se pudo completar la operación.");
        return data;
    }
    function paintAvatar(url) {
        const holder = $("profile-avatar");
        const camera = holder.querySelector(".pr-camera");
        holder.replaceChildren();
        const item = document.createElement(url ? "img" : "span");
        if (url) { item.src = url; item.alt = "Foto de perfil"; item.id = "profile-avatar-img"; }
        else { item.textContent = holder.dataset.initials; item.id = "profile-avatar-initials"; }
        holder.append(item);
        if (camera) holder.append(camera);
    }
    let pendingPhoto = null, previewURL = null;
    const originalAvatar = $("profile-avatar-img")?.getAttribute("src") || null;
    function clearPhoto() {
        if (previewURL) URL.revokeObjectURL(previewURL);
        previewURL = null; pendingPhoto = null;
        if ($("profile-photo")) $("profile-photo").value = "";
    }
    function counter() { if ($("profile-bio")) $("profile-counter").textContent = `${$("profile-bio").value.length}/300`; }
    $("btn-upload-photo")?.addEventListener("click", () => {
        if (reference) { notice("La vista de referencia no permite subir fotos."); return; }
        $("profile-photo").click();
    });
    $("profile-photo")?.addEventListener("change", () => {
        const file = $("profile-photo").files[0];
        if (!file) return;
        if (file.size > 2 * 1024 * 1024 || !/\.(png|jpe?g|webp)$/i.test(file.name)) {
            notice("Seleccione PNG, JPG o WEBP de máximo 2 MB."); return;
        }
        clearPhoto(); pendingPhoto = file; previewURL = URL.createObjectURL(file); paintAvatar(previewURL);
    });
    $("profile-bio")?.addEventListener("input", counter);
    $("profile-form")?.addEventListener("reset", () => {
        clearPhoto(); paintAvatar(originalAvatar); $("page-alert").hidden = true;
        requestAnimationFrame(counter);
    });
    $("profile-form")?.addEventListener("submit", async event => {
        event.preventDefault();
        const button = $("profile-save"); button.disabled = true;
        try {
            // Validate/save personal fields before uploading the optional selected photo.
            const payload = Object.fromEntries(new FormData(event.currentTarget));
            await api("/api/auth/me/profile", "PATCH", payload);
            if (pendingPhoto) {
                const body = new FormData(); body.append("image", pendingPhoto);
                await api("/api/auth/me/profile-photo", "POST", body);
            }
            clearPhoto(); location.assign("/profile?saved=1");
        } catch (error) { notice(error.message); }
        finally { button.disabled = false; }
    });
    if (new URLSearchParams(location.search).get("saved") === "1") notice("Perfil actualizado correctamente.");
    const passwordDialog = $("profile-password-dialog");
    let passwordOpener;
    function openPassword() {
        if (location.hash !== "#change-password") return;
        passwordOpener = document.activeElement;
        $("profile-password-form").reset(); $("profile-password-error").textContent = "";
        if (!passwordDialog.open) passwordDialog.showModal();
    }
    window.addEventListener("hashchange", openPassword); openPassword();
    document.querySelectorAll('a[href$="#change-password"]').forEach(a => a.addEventListener("click", () => {
        if (location.hash === "#change-password") openPassword();
    }));
    $("profile-password-cancel").addEventListener("click", () => passwordDialog.close());
    passwordDialog.addEventListener("close", () => {
        history.replaceState(null, "", location.pathname + location.search);
        passwordOpener?.focus();
    });
    $("profile-password-form").addEventListener("submit", async event => {
        event.preventDefault(); const button = event.submitter; button.disabled = true;
        try {
            const data = await api("/api/auth/change-password", "POST", Object.fromEntries(new FormData(event.currentTarget)));
            passwordDialog.close(); notice(data.message);
        } catch (error) { $("profile-password-error").textContent = error.message; }
        finally { button.disabled = false; }
    });
    let revoke = null;
    const confirmDialog = $("profile-confirm-dialog");
    document.querySelectorAll("[data-revoke-session]").forEach(button => button.addEventListener("click", () => {
        revoke = () => api(`/api/settings/security/sessions/${button.dataset.revokeSession}`, "DELETE"); confirmDialog.showModal();
    }));
    $("profile-revoke-others")?.addEventListener("click", () => {
        revoke = () => api("/api/settings/security/sessions/revoke", "POST", { scope: "others" }); confirmDialog.showModal();
    });
    $("profile-confirm-cancel").addEventListener("click", () => confirmDialog.close());
    $("profile-confirm-accept").addEventListener("click", async event => {
        event.currentTarget.disabled = true;
        try { await revoke(); location.reload(); }
        catch (error) { confirmDialog.close(); notice(error.message); $("profile-confirm-accept").disabled = false; }
    });
})();
