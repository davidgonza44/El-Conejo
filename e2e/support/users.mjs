import fs from 'node:fs';
import path from 'node:path';

const HERE = import.meta.dirname;

export const USERS = JSON.parse(fs.readFileSync(path.join(HERE, '..', 'fixtures', 'users.json'), 'utf8'));
export const ROLES = Object.keys(USERS).filter((key) => !key.startsWith('_'));
export const AUTH_DIR = path.resolve(HERE, '..', '..', '.tmp', 'e2e-auth');

/** Ruta del estado de sesión guardado por global-setup para ``role``. */
export const authFile = (role) => path.join(AUTH_DIR, `${role}.json`);
