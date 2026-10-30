// Autenticación desde la interfaz: login por rol, errores, cierre de sesión y recuperación.
import { test, expect } from '../support/fixtures.mjs';
import { USERS } from '../support/users.mjs';

async function login(page, identifier, password) {
    await page.goto('/login');
    await page.locator('#login-identifier').fill(identifier);
    await page.locator('#login-password').fill(password);
    await page.locator('#login-submit').click();
}

test.describe('Inicio de sesión', () => {
    const HOME = { admin: '/dashboard', inventario: '/dashboard', asistente: '/dashboard', vendedor: '/products', soporte_ti: '/settings' };

    for (const [role, home] of Object.entries(HOME)) {
        test(`${role} entra y aterriza en ${home}`, async ({ page }) => {
            await login(page, USERS[role].username, USERS[role].password);
            await expect(page).toHaveURL(new RegExp(`${home}$`));
            await expect(page.locator('#ix-account-toggle')).toContainText(USERS[role].name);
        });
    }

    test('acepta el correo como identificador', async ({ page }) => {
        await login(page, USERS.admin.email.toUpperCase(), USERS.admin.password);
        await expect(page).toHaveURL(/\/dashboard$/);
    });

    test('rechaza credenciales inválidas con un mensaje genérico', async ({ page }) => {
        await login(page, USERS.admin.username, 'contraseña-incorrecta');
        await expect(page.locator('#login-error')).toHaveText(/Credenciales inválidas/);
        await expect(page).toHaveURL(/\/login$/);
    });

    test('rechaza un usuario inexistente con el mismo mensaje', async ({ page }) => {
        await login(page, 'no_existe_e2e', 'Cualquier-Clave-1');
        await expect(page.locator('#login-error')).toHaveText(/Credenciales inválidas/);
    });

    test('exige ambos campos', async ({ page }) => {
        await page.goto('/login');
        await page.locator('#login-submit').click();
        await expect(page).toHaveURL(/\/login$/);
    });
});

test.describe('Sesión', () => {
    test('cerrar sesión revoca el acceso', async ({ page }) => {
        await login(page, USERS.inventario.username, USERS.inventario.password);
        await expect(page).toHaveURL(/\/dashboard$/);
        await page.locator('#ix-account-toggle').click();
        await page.locator('#ix-account-logout').click();
        await expect(page).toHaveURL(/\/login$/);
        await page.goto('/dashboard');
        await expect(page).toHaveURL(/\/login$/);
    });

    test('con sesión abierta, /login redirige al inicio del rol', async ({ page }) => {
        await login(page, USERS.asistente.username, USERS.asistente.password);
        await expect(page).toHaveURL(/\/dashboard$/);
        await page.goto('/login');
        await expect(page).toHaveURL(/\/dashboard$/);
    });

    test('el menú de cuenta enlaza a Mi perfil', async ({ page }) => {
        await login(page, USERS.vendedor.username, USERS.vendedor.password);
        await page.locator('#ix-account-toggle').click();
        await page.locator('#ix-account-menu').getByRole('link', { name: 'Mi perfil' }).click();
        await expect(page).toHaveURL(/\/profile$/);
    });
});

test.describe('Recuperación de contraseña', () => {
    test('la solicitud responde igual exista o no la cuenta', async ({ page }) => {
        const messages = [];
        for (const email of [USERS.admin.email, 'nadie@example.invalid']) {
            await page.goto('/forgot-password');
            await page.locator('#fp-email').fill(email);
            await page.locator('#fp-submit').click();
            await expect(page.locator('#fp-info')).toBeVisible();
            messages.push(await page.locator('#fp-info').innerText());
        }
        expect(messages[0]).toBe(messages[1]);
    });

    test('un enlace de restablecimiento inválido se rechaza', async ({ page }) => {
        await page.goto('/reset-password?token=token-invalido-e2e');
        await page.locator('#rp-password').fill('Nueva-Clave-E2E-2026!');
        await page.locator('#rp-confirm').fill('Nueva-Clave-E2E-2026!');
        await page.locator('#rp-submit').click();
        await expect(page.locator('#rp-error')).toBeVisible();
        await expect(page.locator('#rp-success')).toBeHidden();
    });

    test('sin token, el formulario avisa antes de enviar', async ({ page }) => {
        await page.goto('/reset-password');
        await expect(page.locator('#rp-error')).toContainText('no contiene un token');
    });
});
