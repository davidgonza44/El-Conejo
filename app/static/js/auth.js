/* Utilidades de sesión compartidas por todas las páginas web. */

/*
 * Toda escritura con sesión exige X-CSRFToken (app/__init__.py). Este envoltorio lo añade a
 * las peticiones fetch del mismo origen con el token de <meta name="csrf-token">; las
 * llamadas que ya lo envían no cambian.
 */
(() => {
    const token = document.querySelector('meta[name="csrf-token"]')?.content;
    if (!token || window.fetch.__conejoCsrf) return;
    const nativeFetch = window.fetch.bind(window);
    const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);
    const conejoFetch = (input, init = {}) => {
        const request = input instanceof Request ? input : null;
        const method = String(init.method || request?.method || "GET").toUpperCase();
        const url = new URL(request ? request.url : String(input), window.location.href);
        if (SAFE.has(method) || url.origin !== window.location.origin) return nativeFetch(input, init);
        const headers = new Headers(init.headers || request?.headers || undefined);
        if (!headers.has("X-CSRFToken")) headers.set("X-CSRFToken", token);
        return nativeFetch(input, { ...init, headers });
    };
    conejoFetch.__conejoCsrf = true;
    window.fetch = conejoFetch;
})();

/**
 * Cierra la sesión contra la API y vuelve al login.
 * Reutilizable: el dashboard real usará esta misma función.
 */
async function logout() {
    try {
        await fetch("/api/auth/logout", {
            method: "POST",
            credentials: "same-origin",
        });
    } catch (error) {
        // Sin conexión o sesión ya cerrada: igualmente se vuelve al login.
    }
    window.location.href = "/login";
}
