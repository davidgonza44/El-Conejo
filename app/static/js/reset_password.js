/* Lógica de /reset-password: crea la nueva contraseña usando el token
   que viene en el query string del enlace (?token=...). */

(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);

    function showAlert(el, message) {
        el.textContent = message;
        el.classList.remove("d-none");
    }

    function hideAlerts(...els) {
        els.forEach((el) => el.classList.add("d-none"));
    }

    const token = new URLSearchParams(window.location.search).get("token") || "";

    if (!token) {
        showAlert(
            $("rp-error"),
            "El enlace no contiene un token de recuperación. Solicite uno nuevo."
        );
        $("rp-form").classList.add("d-none");
    }

    $("rp-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        hideAlerts($("rp-success"), $("rp-error"));

        const newPassword = $("rp-password").value;
        const confirmPassword = $("rp-confirm").value;

        if (!newPassword || !confirmPassword) {
            showAlert($("rp-error"), "Complete ambos campos de contraseña.");
            return;
        }
        if (newPassword !== confirmPassword) {
            showAlert($("rp-error"), "Las contraseñas no coinciden.");
            return;
        }

        const button = $("rp-submit");
        button.disabled = true;
        try {
            const response = await fetch("/api/auth/password-reset/confirm", {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    token,
                    new_password: newPassword,
                    confirm_password: confirmPassword,
                }),
            });
            let data = {};
            try {
                data = await response.json();
            } catch (error) {
                // Respuesta sin cuerpo JSON: se deja data vacío.
            }

            if (!response.ok) {
                // Token inválido, vencido o usado: error claro para el usuario.
                showAlert($("rp-error"), data.error || "No fue posible cambiar la contraseña.");
                return;
            }

            showAlert($("rp-success"), data.message || "Contraseña actualizada correctamente.");
            $("rp-form").classList.add("d-none");
            $("rp-login-box").classList.remove("d-none");
        } catch (error) {
            showAlert($("rp-error"), "Error de conexión con el servidor.");
        } finally {
            button.disabled = false;
        }
    });
})();
