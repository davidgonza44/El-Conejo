# Auditoría en navegador — 31/10/2026

Auditoría de coherencia entre lo que muestra la interfaz y lo que guarda la base. Se hizo en un laboratorio aislado (MariaDB, puerto 5099) con los 5 roles, sobre el commit `fb3744c`.

- [`HALLAZGOS.md`](HALLAZGOS.md): los 58 hallazgos, con su código (P1, N3, C9, X1…) y su gravedad. El plan de trabajo está en [`../PLAN_ROBUSTEZ_DEFENSA.md`](../PLAN_ROBUSTEZ_DEFENSA.md).
- [`scripts/`](scripts/): los recorridos usados para encontrarlos. Sirven para reproducir un caso o para comprobar una corrección.

## Cómo correr los scripts

Solo contra un laboratorio aislado, **nunca contra 5001 ni 5011**.

1. **Crear los usuarios de auditoría** (`aud_admin`, `aud_admin2`, `aud_asistente`, `aud_inventario`, `aud_vendedor`, `aud_soporte_ti`) dentro del contenedor de la app del laboratorio:

   ```
   docker cp scripts/seed_users.py <contenedor-app>:/tmp/seed_users.py
   docker exec -w /app -e PYTHONPATH=/app -e AUDIT_LAB_DB=<base-del-lab> -e AUDIT_PASSWORD=<clave> <contenedor-app> python /tmp/seed_users.py
   ```

   Los scripts `repro_*.py` (reproducciones de Compras por API) se corren igual.

2. **Recorrido por rol** (necesita `npm ci` en la raíz del repo y Chrome instalado):

   ```
   cd docs/auditoria-20261031/scripts
   AUDIT_PASSWORD=<clave> AUDIT_BASE_URL=http://127.0.0.1:5099 node crawl.mjs admin
   ```

   Esto genera, en `out/<rol>/`, una captura por pantalla y por diálogo, y un archivo `report.json`. Ese informe lista los "—", los textos `undefined`/`NaN`, los errores de JavaScript y las respuestas HTTP fallidas.

3. **Flujos con datos** (`flow_*.mjs`): cada uno recorre un módulo, llenando formularios con datos válidos e inválidos. Se corren igual: `node flow_product.mjs`, `node flow_venta.mjs`, etc. Los nombres de los datos de prueba empiezan por `AUD-`.

   Algunos flujos dependen de datos que creó un flujo anterior (por ejemplo, la orden OC-000004). Pueden necesitar ajustes en un laboratorio nuevo.

La carpeta `out/` (capturas) no se versiona.
