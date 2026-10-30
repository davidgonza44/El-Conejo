/* Utilidades de sesión compartidas por todas las páginas web. */

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
