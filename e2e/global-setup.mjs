// Inicia sesión una vez por rol (API real de login) y guarda la sesión para los specs.
import { request } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { AUTH_DIR, USERS, ROLES } from './support/users.mjs';

export default async function globalSetup(config) {
    const { baseURL } = config.projects[0].use;
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    for (const role of ROLES) {
        const context = await request.newContext({ baseURL });
        const response = await context.post('/api/auth/login', {
            data: { identifier: USERS[role].username, password: USERS[role].password },
        });
        if (!response.ok()) {
            throw new Error(`No se pudo iniciar sesión como ${role}: ${response.status()} ${await response.text()}`);
        }
        await context.storageState({ path: path.join(AUTH_DIR, `${role}.json`) });
        await context.dispose();
    }
}
