// Configuración (empresa, usuarios), perfil propio con cambio de contraseña e inicio de sesión
// del usuario recién creado. El usuario nuevo se usa para no alterar las sesiones de la suite.
import { test, expect, as } from '../support/fixtures.mjs';
import { uid } from '../support/data.mjs';

const id = uid().toLowerCase();
const newcomer = { name: `E2E Nuevo Inventario ${id}`, username: `e2e_nuevo_${id}`, email: `nuevo_${id}@example.invalid`,
    password: 'E2e-Nuevo-Clave-2026!', next: 'E2e-Otra-Clave-2026#' };

test.describe.configure({ mode: 'serial' });

test.describe('Configuración (administrador)', () => {
    test.use(as('admin'));

    test('abre cada pestaña con su contenido', async ({ page }) => {
        await page.goto('/settings');
        const names = ['General', 'Análisis predictivo', 'Notificaciones', 'Usuarios y roles', 'Seguridad', 'Integraciones', 'Respaldo'];
        const tabs = page.getByRole('navigation', { name: 'Configuración' });
        for (const name of names) {
            await tabs.getByRole('link', { name, exact: true }).click();
            await expect(tabs.getByRole('link', { name, exact: true })).toHaveAttribute('aria-current', 'page');
            await expect(page.locator('main h1')).toHaveText('Configuración');
        }
    });

    test('guarda el nombre de la empresa y lo conserva al recargar', async ({ page }) => {
        await page.goto('/settings?tab=general');
        const company = page.locator('input[name="company.name"]');
        const original = await company.inputValue();
        const changed = `Ferretería y Construcciones El Conejo C.A. (${id})`;
        await company.fill(changed);
        await page.locator('#st-save').click();
        await expect(page.locator('.st-toast')).toBeVisible();
        await page.reload();
        await expect(company).toHaveValue(changed);
        await company.fill(original);
        await page.locator('#st-save').click();
        await expect(page.locator('.st-toast')).toBeVisible();
    });

    test('crea un usuario con rol de inventario', async ({ page }) => {
        await page.goto('/settings?tab=users');
        await openNewUserForm(page);
        await page.locator('#st-user-name').fill(newcomer.name);
        await page.locator('#st-user-email').fill(newcomer.email);
        await page.locator('#st-user-username').fill(newcomer.username);
        await page.locator('#st-user-role').selectOption('inventario');
        await page.locator('#st-user-password').fill(newcomer.password);
        await page.locator('#st-user-save').click();
        await expect(page.locator('.st-toast')).toContainText(`Usuario ${newcomer.name} creado`);
        await page.locator('#st-user-search').fill(newcomer.username);
        await expect(page.locator('main')).toContainText(newcomer.name);
    });

    test('rechaza un usuario con contraseña débil', async ({ page }) => {
        await page.goto('/settings?tab=users');
        await openNewUserForm(page);
        await page.locator('#st-user-name').fill('E2E Débil');
        await page.locator('#st-user-email').fill(`debil_${id}@example.invalid`);
        await page.locator('#st-user-username').fill(`e2e_debil_${id}`);
        await page.locator('#st-user-password').fill('123');
        await page.locator('#st-user-save').click();
        await expect(page.locator('.st-toast')).toBeVisible();
        await expect(page.locator('.st-toast')).not.toContainText('creado');
    });
});

// El botón existe antes de que ix_settings_users.js registre su evento: se reintenta el clic
// hasta que el formulario quede en modo creación (campos editables).
async function openNewUserForm(page) {
    await expect(async () => {
        await page.locator('#st-user-new').click();
        await expect(page.locator('#st-user-name')).toBeEditable({ timeout: 1_000 });
    }).toPass({ timeout: 15_000 });
}

test.describe('Soporte TI', () => {
    test.use(as('soporte_ti'));

    test('ve seguridad y respaldo, pero no usuarios ni datos del negocio', async ({ page }) => {
        await page.goto('/settings');
        const tabs = page.getByRole('navigation', { name: 'Configuración' });
        await expect(tabs.getByRole('link', { name: 'Seguridad', exact: true })).toBeVisible();
        await expect(tabs.getByRole('link', { name: 'Respaldo', exact: true })).toBeVisible();
        await expect(tabs.getByRole('link', { name: 'Usuarios y roles', exact: true })).toHaveCount(0);
        expect((await page.goto('/settings?tab=users')).status()).toBe(403);
    });
});

test.describe('Usuario nuevo', () => {
    test('entra, actualiza su perfil y cambia su contraseña', async ({ page }) => {
        await page.goto('/login');
        await page.locator('#login-identifier').fill(newcomer.username);
        await page.locator('#login-password').fill(newcomer.password);
        await page.locator('#login-submit').click();
        await expect(page).toHaveURL(/\/dashboard$/);

        await page.goto('/profile');
        await page.locator('#profile-job').fill('Encargado de almacén');
        await page.locator('#profile-phone').fill('0414-5550000');
        await page.locator('#profile-save').click();
        await expect(page.getByText('Perfil actualizado correctamente.')).toBeVisible();
        await expect(page.locator('#profile-job')).toHaveValue('Encargado de almacén');

        await page.locator('#profile-password').click();
        const dialog = page.locator('#profile-password-dialog');
        await expect(dialog).toBeVisible();
        await dialog.locator('#profile-password-current').fill(newcomer.password);
        await dialog.locator('#profile-password-new').fill(newcomer.next);
        await dialog.locator('#profile-password-confirm').fill(newcomer.next);
        await dialog.getByRole('button', { name: 'Guardar contraseña' }).click();
        await expect(dialog).toBeHidden();

        const old = await page.request.post('/api/auth/login', { data: { identifier: newcomer.username, password: newcomer.password } });
        expect(old.status()).toBe(401);
        const fresh = await page.request.post('/api/auth/login', { data: { identifier: newcomer.username, password: newcomer.next } });
        expect(fresh.status()).toBe(200);
    });
});
