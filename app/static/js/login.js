/* Lógica de la página /login: login tradicional y passwordless.
   Google NO usa fetch: es una redirección normal del navegador. */

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

    async function postJson(url, body) {
        const response = await fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        let data = {};
        try {
            data = await response.json();
        } catch (error) {
            // Respuesta sin cuerpo JSON: se deja data vacío.
        }
        return { ok: response.ok, status: response.status, data };
    }

    // ------------------------------------------------------------------
    // 2FA: el servidor responde {mfa_required:true} sin abrir sesión; se pide el código.
    // ------------------------------------------------------------------
    function showMfaStep() {
        $("mfa-form").classList.remove("d-none");
        $("login-form").classList.add("d-none");
        $("mfa-code").focus();
    }

    function afterLogin(data) {
        if (data.password_expired) {
            window.location.href = "/password-expired";
            return;
        }
        window.location.href = data.home || "/login";
    }

    function handleLoginResult(ok, data, errorEl, fallback) {
        if (ok && data.mfa_required) {
            showMfaStep();
            return;
        }
        if (ok) {
            afterLogin(data);
            return;
        }
        showAlert(errorEl, data.error || fallback);
    }

    $("mfa-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        hideAlerts($("login-error"));
        const code = $("mfa-code").value.trim();
        if (!code) {
            showAlert($("login-error"), "Ingrese el código de verificación.");
            return;
        }
        const button = $("mfa-submit");
        button.disabled = true;
        try {
            const { ok, status, data } = await postJson("/api/auth/mfa/verify", {
                code,
                trust_device: $("mfa-trust").checked,
            });
            if (ok) {
                afterLogin(data);
                return;
            }
            showAlert($("login-error"), data.error || "Código incorrecto.");
            if (status === 401) {
                // El desafío expiró: volver al primer paso.
                $("mfa-form").classList.add("d-none");
                $("login-form").classList.remove("d-none");
            }
        } catch (error) {
            showAlert($("login-error"), "Error de conexión con el servidor.");
        } finally {
            button.disabled = false;
        }
    });

    // Tras volver de Google con 2FA activo, el servidor redirige a /login?mfa=1 con el desafío en sesión.
    if (new URLSearchParams(window.location.search).get("mfa") === "1") {
        fetch("/api/auth/mfa/challenge", { credentials: "same-origin" })
            .then((response) => response.json())
            .then((data) => { if (data.pending) { showMfaStep(); } })
            .catch(() => {});
    }

    // ------------------------------------------------------------------
    // Login tradicional (email o username + contraseña)
    // ------------------------------------------------------------------
    $("login-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        hideAlerts($("login-error"));

        const identifier = $("login-identifier").value.trim();
        const password = $("login-password").value;
        if (!identifier || !password) {
            showAlert($("login-error"), "Ingrese usuario/correo y contraseña.");
            return;
        }

        const button = $("login-submit");
        button.disabled = true;
        try {
            const { ok, data } = await postJson("/api/auth/login", { identifier, password });
            handleLoginResult(ok, data, $("login-error"), "No fue posible iniciar sesión.");
        } catch (error) {
            showAlert($("login-error"), "Error de conexión con el servidor.");
        } finally {
            button.disabled = false;
        }
    });

    // ------------------------------------------------------------------
    // Passwordless: solicitar código
    // ------------------------------------------------------------------
    $("pwl-request").addEventListener("click", async () => {
        hideAlerts($("pwl-info"), $("pwl-error"), $("pwl-token-box"));

        const email = $("pwl-email").value.trim();
        if (!email) {
            showAlert($("pwl-error"), "Ingrese su correo electrónico.");
            return;
        }

        const button = $("pwl-request");
        button.disabled = true;
        try {
            const { ok, data } = await postJson("/api/auth/passwordless/request", { email });
            if (!ok) {
                showAlert($("pwl-error"), data.error || "No fue posible solicitar el código.");
                return;
            }
            showAlert(
                $("pwl-info"),
                "Si el correo está registrado, recibirá un código de acceso temporal."
            );
            if (data.dev_token) {
                // Solo llega en APP_ENV=development para probar sin correo configurado.
                $("pwl-dev-token").textContent = data.dev_token;
                $("pwl-token").value = data.dev_token;
                $("pwl-token-box").classList.remove("d-none");
            }
        } catch (error) {
            showAlert($("pwl-error"), "Error de conexión con el servidor.");
        } finally {
            button.disabled = false;
        }
    });

    // ------------------------------------------------------------------
    // Passwordless: verificar código
    // ------------------------------------------------------------------
    $("pwl-verify").addEventListener("click", async () => {
        hideAlerts($("pwl-info"), $("pwl-error"));

        const email = $("pwl-email").value.trim();
        const token = $("pwl-token").value.trim();
        if (!email || !token) {
            showAlert($("pwl-error"), "Ingrese el correo y el código recibido.");
            return;
        }

        const button = $("pwl-verify");
        button.disabled = true;
        try {
            const { ok, data } = await postJson("/api/auth/passwordless/verify", { email, token });
            handleLoginResult(ok, data, $("pwl-error"), "Código inválido o vencido.");
        } catch (error) {
            showAlert($("pwl-error"), "Error de conexión con el servidor.");
        } finally {
            button.disabled = false;
        }
    });
})();
