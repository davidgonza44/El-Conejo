/* Lógica de /forgot-password: solicita el enlace de recuperación por correo.
   La respuesta del servidor es siempre neutra (no revela si el correo existe). */

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

    $("fp-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        hideAlerts($("fp-info"), $("fp-error"), $("fp-dev-box"));

        const email = $("fp-email").value.trim();
        if (!email) {
            showAlert($("fp-error"), "Ingrese su correo electrónico.");
            return;
        }

        const button = $("fp-submit");
        button.disabled = true;
        try {
            const response = await fetch("/api/auth/password-reset/request", {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email }),
            });
            let data = {};
            try {
                data = await response.json();
            } catch (error) {
                // Respuesta sin cuerpo JSON: se deja data vacío.
            }

            if (!response.ok) {
                showAlert($("fp-error"), data.error || "No fue posible procesar la solicitud.");
                return;
            }

            showAlert(
                $("fp-info"),
                data.message ||
                    "Si el correo está registrado, recibirá un enlace para restablecer su contraseña."
            );

            if (data.dev_reset_link) {
                // Solo llega en APP_ENV=development para probar sin correo real.
                const link = $("fp-dev-link");
                link.textContent = data.dev_reset_link;
                link.href = data.dev_reset_link;
                $("fp-dev-box").classList.remove("d-none");
            }
        } catch (error) {
            showAlert($("fp-error"), "Error de conexión con el servidor.");
        } finally {
            button.disabled = false;
        }
    });
})();
