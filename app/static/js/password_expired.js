/* Página /password-expired: cambio obligatorio de contraseña contra /api/auth/change-password. */
(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);
    const error = $("pe-error");

    $("pe-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        error.classList.add("d-none");
        const button = $("pe-submit");
        button.disabled = true;
        try {
            const response = await fetch("/api/auth/change-password", {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json", "X-CSRFToken": $("pe-form").dataset.csrf },
                body: JSON.stringify({
                    current_password: $("pe-current").value,
                    new_password: $("pe-new").value,
                    confirm_password: $("pe-confirm").value,
                }),
            });
            const data = await response.json().catch(() => ({}));
            if (response.ok) {
                window.location.href = $("pe-form").dataset.home || "/login";
                return;
            }
            error.textContent = data.error || "No fue posible cambiar la contraseña.";
            error.classList.remove("d-none");
        } catch (err) {
            error.textContent = "Error de conexión con el servidor.";
            error.classList.remove("d-none");
        } finally {
            button.disabled = false;
        }
    });
})();
