# Pruebas E2E (Playwright)

Prueban la aplicación real en un navegador Chrome contra un servidor Flask desechable.
No usan MySQL, `.env` ni datos reales: cada ejecución crea una base SQLite temporal,
siembra datos sintéticos con los servicios de dominio y la borra al terminar.

## Requisitos

- Python 3.10+ con las dependencias de `requirements.txt` en `.venv/` (o la ruta en `E2E_PYTHON`).
- Node.js y `npm ci` en la raíz.
- Google Chrome instalado. Para usar el Chromium de Playwright: `npx playwright install chromium`
  y `E2E_CHANNEL=` (vacío) al ejecutar.

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
npm ci
```

## Ejecución

```powershell
npm run e2e                          # suite completa (~13 min)
npm run e2e -- specs/04-products     # un archivo
npm run e2e -- -g "Cancelación"      # por nombre
npm run e2e:report                   # informe HTML de la última ejecución
```

Resultados, capturas y trazas de fallos quedan en `.tmp/` (ignorado por Git).
Cada ejecución levanta su propio servidor en el puerto `E2E_PORT` (5099 por defecto);
el puerto debe estar libre.

## Piezas

| Archivo | Función |
| --- | --- |
| `harness/server.py` | Crea la app con SQLite temporal, RBAC sembrado y usuarios por rol. |
| `harness/seed.py` | Catálogo, proveedores, stock, cliente y nota de entrega creados con el rol correcto de cada operación. |
| `fixtures/users.json` | Credenciales **solo de prueba** de los cinco roles (admin, inventario, asistente, vendedor, soporte_ti). |
| `global-setup.mjs` | Inicia sesión una vez por rol y guarda la sesión. |
| `support/fixtures.mjs` | Falla cualquier prueba con errores JS o respuestas 5xx; caché local de los CDN; cliente de API con CSRF. |

## Cobertura por funcionalidad

| Spec | Qué verifica |
| --- | --- |
| `01-access-matrix` | Cada pantalla con cada rol: abre (200) o bloquea (403); sin sesión redirige al login; ventas a clientes retiradas. |
| `02-auth` | Login por rol y aterrizaje, correo como usuario, credenciales inválidas, cierre de sesión, recuperación y restablecimiento. |
| `03-navigation` | Todos los enlaces del menú de cada rol abren páginas autorizadas; `aria-current`; búsqueda global; alertas; menú móvil. |
| `04-products` | El asistente propone un producto (con validaciones), el administrador lo aprueba y el stock inicial queda como movimiento. |
| `05-inventory` | Entradas y salidas actualizan el stock; no se permite sacar más de lo disponible; el ajuste espera aprobación; IDs enteros, límites de stock y CSRF; el error queda junto a Cantidad. |
| `06-delivery-notes-customers` | Revisión y confirmación antes de emitir; descuento y devolución de stock; precio desactualizado (409); formatos y longitudes; CSRF; PDF; clientes. |
| `07-categories-suppliers` | Propuestas del asistente, aprobación y rechazo; nadie aprueba lo propio; la modificación de un proveedor aprobado no toca sus datos hasta aprobarse. |
| `08-purchases` | Ciclo completo de compra; recepciones sin filtros por monto para roles sin costos; indicadores de completadas iguales en Compras y Proveedores. |
| `09-reports` | Cinco pestañas, período, exportación, histórico, vista física, moneda no acreditada, filtro por categoría exacta, ticket ponderado y páginas de error HTML. |
| `10-settings-profile` | Pestañas de configuración, nombre de la empresa, alta de usuario, alcance de Soporte TI, perfil y cambio de contraseña. |
| `11-historical-predictive` | Importación histórica CSV v1 (validar, simular, confirmar), estados honestos del análisis predictivo, chatbot, catálogo y conciliación. |
| `12-guide-branding` | Guía de uso por rol, RIF en Configuración, membrete en impresiones, PDF y Excel, y pantallas antiguas dentro del menú moderno. |
