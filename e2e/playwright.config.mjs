// Suite E2E: levanta e2e/harness/server.py (SQLite temporal, datos sintéticos) y prueba la app real.
import { defineConfig } from '@playwright/test';
import path from 'node:path';

const PORT = Number(process.env.E2E_PORT || 5099);
const ROOT = path.resolve(import.meta.dirname, '..');
const PYTHON = process.env.E2E_PYTHON
    || path.join(ROOT, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');

export default defineConfig({
    testDir: './specs',
    outputDir: '../.tmp/e2e-results',
    // Un solo servidor y una sola base: los flujos que escriben datos corren en orden.
    workers: 1,
    fullyParallel: false,
    timeout: 45_000,
    expect: { timeout: 10_000 },
    // Un reintento graba la traza del fallo; Playwright marca como "flaky" lo que pase al reintentar.
    retries: 1,
    reporter: [['list'], ['html', { outputFolder: '../.tmp/e2e-report', open: 'never' }]],
    globalSetup: './global-setup.mjs',
    use: {
        baseURL: `http://127.0.0.1:${PORT}`,
        channel: process.env.E2E_CHANNEL ?? 'chrome',
        locale: 'es-VE',
        timezoneId: 'America/Caracas',
        viewport: { width: 1440, height: 900 },
        // Grabar trazas en cada prueba la hace ~5 veces más lenta: solo en el reintento.
        trace: 'on-first-retry',
        screenshot: 'only-on-failure',
    },
    webServer: {
        command: `"${PYTHON}" -B e2e/harness/server.py`,
        cwd: ROOT,
        url: `http://127.0.0.1:${PORT}/login`,
        env: { E2E_PORT: String(PORT), PYTHONIOENCODING: 'utf-8' },
        // Siempre una base nueva: los specs crean datos y deben partir del mismo estado.
        reuseExistingServer: false,
        timeout: 180_000,
        stdout: 'pipe',
    },
});
