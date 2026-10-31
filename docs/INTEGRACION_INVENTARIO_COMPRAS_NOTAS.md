# Integración de Inventario, Compras y Notas de Entrega sobre 5011

Fecha: 31 de octubre de 2026. Rama local `integracion/inventario-compras-notas`,
sin push. Referencia operativa: 5011, imagen `conejo-portable:branch5011-33827f3`,
cuyo código (`app/`, `migrations/`, `docker_support/`) es idéntico al commit `33827f3`.
5001 no es base de este trabajo. No se modificaron, detuvieron ni desplegaron 5001 ni 5011.

## Dictamen

**APTO para solicitar autorización de despliegue en 5011**, con estas condiciones:

1. Tomar un respaldo verificado de la base de 5011 inmediatamente antes de actualizar.
2. Actualizar con el servicio `init` de Docker (migra `0007 → 0014` y verifica el esquema).
3. Tener lista la vuelta atrás ensayada: restaurar ese respaldo y arrancar la imagen anterior.

Las condiciones se ensayaron completas sobre una copia de 5011 (sección 5). Los riesgos
pendientes de la sección 9 no bloquean el despliegue, pero conviene decidirlos.

## 1. Commits

| Commit | Contenido |
|---|---|
| `14daf43` | Compras: aprobaciones y trazabilidad (trabajo previo, reutilizado). |
| `fbcb66c` | Inventario: nueve mejoras (trabajo previo, reutilizado). |
| `b28a2de`, `b175ab4` | GitHub: videos y Notas de Entrega/Clientes (reutilizado). |
| `cb1a0af` | Unión de las tres líneas; migraciones en una sola cadena. |
| `a0e0870` | Ajustes de conciliación solo desde fuente acreditada; sin doble corrección. |
| `ba914da` | 0009 y 0012 adoptan estructuras creadas fuera de Alembic. |
| `db3bf57` | 0013 representa columnas existentes de 5011; 0014 `products.is_service`. |
| `5f0b45b` | Notas: precio confirmado obligatorio y validaciones; 409 con la causa real. |
| `0c03434` | 0004a: línea base que faltaba para instalar desde cero. |
| `01d1919` | Verificador de cortes acepta las columnas históricas de 0013. |
| `50d7ed3` | `ck_po_status` en la línea base; CHECK de MariaDB (4025) responde 409. |
| `73be84d` | Pruebas de Notas en MariaDB y de proveedores en navegador ajustadas. |
| `b3509f7` | Un solo orden de bloqueo de productos para Compras e Inventario. |

Respaldos: `C:\Users\Davidgonza\OneDrive\Documents\Tesis-Codigo\_respaldos\`
(archivos de Inventario previos al primer commit y copia lógica de la base de 5011, ambos con SHA-256).

## 2. Qué se reutilizó, qué se agregó y qué datos se adaptaron

**Reutilizado sin rehacer:** las nueve mejoras de Inventario, los requisitos de Compras de
`14daf43` y las correcciones de Notas de GitHub. En Notas ya existían CSRF global
(`auth.js`), confirmación previa, clave por envío, numeración propia y bloqueo ordenado;
no se duplicaron. Las reglas de Compras, reposición y pronóstico que consultan
`is_service` se activaron con la columna, sin reescribirlas.

**Agregado porque faltaba o estaba defectuoso:**

- Cadena de migraciones: la instalación desde cero nunca funcionó (fallaba en 0005);
  dos 0010 con el mismo padre; 0009 y 0012 chocaban con estructuras ya existentes;
  columnas de 5011 sin modelo hacían fallar `verify_schema`.
- Clasificación persistente de servicios.
- Notas: el precio confirmado era opcional y se redondeaba en silencio; faltaban el rechazo de
  caracteres de control, el tope de importe y el filtro de productos activos; el selector
  "solo USD" nunca filtraba porque el mapa de productos no guardaba la moneda.
- 409: decía "registro repetido" ante cualquier violación; un CHECK en MariaDB terminaba en 500.
- Conciliación: un CSV subido a mano podía terminar en ajuste con dos aprobaciones.
- Duplicación eliminada: dos funciones idénticas de bloqueo de productos.

**Datos adaptados (copia de 5011):** ninguna fila se modifica. Se adoptan sin cambios
`supplier_changes`, `purchase_orders.supplier_snapshot` (28 valores) y 25 columnas de
conciliación (110 de 112 cortes con datos). Todos los productos quedan como no servicio.
La secuencia de notas continúa en el mayor número existente.

## 3. Matriz de cambios

Implementado = el código está; Probado = prueba ejecutada y aprobada; Integrado = funciona
junto a los otros módulos en MariaDB real (instalación nueva y copia de 5011).

### Inventario

| # | Cambio | Implementado | Probado | Integrado |
|---|---|---|---|---|
| 1 | Ajuste de conciliación por diferencia, con segundo aprobador y fuente acreditada | Sí | Sí (SQLite; CSV manual rechazado en E2E) | Sí |
| 2 | Notas por `inventory_service`, con avisos de stock | Sí | Sí | Sí |
| 3 | CHECK de stock y movimientos | Sí (0011) | Sí (MariaDB rechaza) | Sí |
| 4 | Bloqueo ordenado y 409 ante deadlock | Sí (un solo orden para los tres módulos) | Sí (concurrencia MariaDB) | Sí |
| 5 | Entradas y salidas idempotentes | Sí | Sí (E2E) | Sí |
| 6 | Desactivar con stock 0; inactivo admite ajuste a 0 | Sí | Sí (E2E) | Sí |
| 7 | Stock inicial al aprobar el alta | Sí | Sí (E2E) | Sí |
| 8 | Categoría sin productos activos para desactivarse | Sí | Sí (E2E) | Sí |
| 9 | Catálogo visual paginado | Sí | Sí (API, DOM simulado y navegador) | Sí |

### Compras

La lista de diez requisitos se reconstruyó de `docs/PURCHASE_INTEGRITY.md` y de sus pruebas.

| # | Requisito | Implementado | Probado | Integrado |
|---|---|---|---|---|
| 1 | Cantidad exacta cotizada | Sí | Sí | Sí |
| 2 | Servicios rechazados desde la solicitud y la cotización | Sí (`is_service` persistente, 0014) | Sí (sin registros creados; E2E) | Sí |
| 3 | Identidad histórica del proveedor | Sí | Sí (E2E) | Sí |
| 4 | No inventar históricos | Sí | Sí | Sí; las 28 copias heredadas se documentan (sección 7) |
| 5 | Propuestas de proveedores en filtros, contadores y alertas | Sí | Sí | Sí |
| 6 | Estado de proveedor con motivo y otro rol | Sí | Sí | Sí |
| 7 | Recepción entre aprobación y hoy | Sí | Sí | Sí |
| 8 | Corrección de recepción autorizada y atómica | Sí | Sí (E2E y MariaDB) | Sí |
| 9 | Controles de corrección y exclusión con reposición | Sí | Sí (MariaDB concurrente) | Sí |
| 10 | Bandeja de aprobaciones, totales por etapa y exportaciones | Sí | Sí | Sí |

### Notas de Entrega

Las siete correcciones salen de la imagen `sha256:d21291fd…` (el commit `9a8c106` no está
en ningún repositorio local), comparada con su base `25c0a343` en cinco archivos.

| # | Corrección | Implementado | Probado | Integrado |
|---|---|---|---|---|
| N1 | CSRF en emitir y cancelar | Sí (global, reutilizado) | Sí (E2E sin token → 400) | Sí |
| N2 | `expected_unit_price` obligatorio; 409 `price_changed` | Sí | Sí (19 consumidores adaptados) | Sí |
| N3 | Confirmación previa a la emisión | Sí (reutilizado) | Sí (navegador) | Sí |
| N4 | Sin reenvíos duplicados; aviso ante resultado incierto | Sí | Sí (E2E y MariaDB) | Sí |
| N5 | Datos del cliente validados (control, longitud, documento, teléfono) | Sí | Sí | Sí |
| N6 | Enteros estrictos e importe máximo | Sí | Sí | Sí |
| N7 | Bloqueo determinista; solo productos activos en USD | Sí | Sí (MariaDB concurrente) | Sí |

## 4. Migraciones

Cadena única: `0001 → 0004 → 0004a → 0005 … 0012 → 0013 → 0014` (HEAD `0014_product_service_flag`).

| Prueba (MariaDB 10.11.14, misma imagen y digest del proyecto) | Resultado |
|---|---|
| Instalación nueva con el `init` real de Docker | `0001 → 0014`; `verify_schema`, triggers, permisos y administrador verificados. |
| Esquema resultante frente a los modelos | Idéntico en tablas, columnas y CHECK. |
| Copia de 5011 con el `init` real | `0007 → 0014` verificado; 52 productos, stock 3694, 260 movimientos, sin cambios. |
| Datos inválidos | 0011 se detiene sin crear nada. |
| Datos de 5001 (solo lectura) | Ninguna fila bloquearía 0011. |

## 5. Arranque y recuperación (ensayo sobre la copia de 5011)

En un laboratorio separado (proyecto, red, volumen y puerto propios):

1. La imagen actual de 5011 arranca sana sobre la copia restaurada (`0007`).
2. Respaldo previo a la actualización, con SHA-256.
3. La imagen candidata migra a `0014`, arranca sana y aprueba el E2E: 61/61.
4. Se restaura el respaldo (SHA-256 verificado): la imagen anterior vuelve a arrancar sana,
   la base regresa a `0007` con los mismos datos y sin restos del E2E.

## 6. Pruebas

| Prueba | Resultado |
|---|---|
| E2E en laboratorio, instalación nueva, commit final | 61/61 |
| E2E sobre la copia de 5011 migrada | 61/61 |
| `test_purchase_tutor_concurrency` (MariaDB) | 17/17 |
| `test_purchase_integrity_concurrency` (MariaDB) | 22/22 |
| `test_delivery_note_traceability_mariadb` | Aprobada, incluida la cancelación concurrente (200/409) |
| Navegador, suite completa (Playwright, 253) | 249 aprobadas; 2 de Configuración fallaron por carga del equipo y aprobaron al repetirse (48/48 en la corrida focalizada final) |
| Suites SQLite frente a `33827f3` (76 suites) | Sin regresiones: las 29 que pasaban siguen pasando, más 4 nuevas; las 43 que ya fallaban fallan igual, con el mismo número de fallas |

El E2E cubre: recepción y aumento de stock; corrección con movimiento compensatorio;
emisión, reintento y cancelación de notas con restitución; precio confirmado; duplicados de
movimientos, notas, recepciones y aprobaciones; cotizaciones, proveedores y aprobaciones;
servicios; CHECK; CSRF; y la cadena de movimientos de cada producto.

### Fallas que ya existían en `33827f3`

Ninguna es una regresión: cada suite falla igual, con el mismo número de fallas, en
`33827f3` y en el candidato. No se cambiaron sus expectativas. Se agrupan por causa:

| Causa | Suites |
|---|---|
| Esperan permisos o flujos anteriores a la separación operativa y a las aprobaciones (reciben 403 o una propuesta en vez de un alta directa) | `test_categories_core`, `test_customers_core`, `test_orders_core`, `test_quotes_core`, `test_sales_core`, `test_sales_seller_scope`, `test_shell_core`, `test_prediction_readiness`, `test_seed_tesis`, `test_operational_separation`, `test_navigation_permissions`, `test_historical_imports`, `test_settings_core`, `test_soporte_ti`, `test_sales_dashboard_core`, `test_sales_report_security`, `test_create_tesis_admin`, `test_prepare_tesis_rbac`, `test_inventory_core` (espera un ajuste directo; hoy es una solicitud), `test_movement_traceability` (7), `test_catalog_approval_review`, `test_category_approval` |
| Crean órdenes de compra directas; hoy toda orden nace de una solicitud de reposición | `test_cost_visibility`, `test_product_suppliers`, `test_purchase_approval`, `test_purchase_csrf`, `test_purchase_currency`, `test_purchase_quote_selection`, `test_purchase_unavailable`, `test_replenishment_filters`, `test_replenishment_inbox`, `test_replenishment_supplier_coverage`, `test_suppliers_currency_audit`, `test_suppliers_currency_filters`, `test_tutor_cycle`, `test_legacy_suites_vigente_20261017`, `test_product_supplier_filter` |
| Datos sintéticos desfasados del esquema | `test_movement_detail` (`purchase_orders.currency` obligatoria), `test_dashboard_inventory_history`, `test_product_export` |
| Indicadores con otra definición vigente | `test_dashboard_core` (11), `test_inventory_summary_core` (6) |
| Página con fixture ausente | `test_products_core` (1 de 77 comprobaciones) |

Además, `test_reconciliation_automatic_sqlite` depende de la hora: falla igual en `33827f3`
después de las 18:30 de Caracas. En Windows, algunas suites agregan errores secundarios al
borrar su carpeta temporal (archivo en uso); no cambian el resultado.

Las suites de Inventario, Compras y Notas que fallaban por permisos o flujos anteriores tienen
su cobertura vigente en `test_inventory_robustness`, `test_purchase_integrity`,
`test_delivery_notes_robustness` y `test_integracion_5011`, todas aprobadas.

## 7. Identidades históricas de proveedor (28 órdenes de 5011)

No se modificó ningún registro.

- Las 28 llevan `source = migracion_20261029`: las reconstruyó una migración de otra línea
  el 29/10. Ninguna es una captura original del momento de aprobación.
- 23 corresponden a órdenes aprobadas y coinciden con un proveedor sin cambios desde antes de
  la orden. Son reconstrucciones verosímiles, pero indirectas.
- 5 (2 anuladas y 3 rechazadas) no tienen aprobación registrada: no respaldan una identidad
  aprobada.
- Los proveedores son de demostración ("DEMO …").

## 8. Coherencia entre módulos

- **Existencias y movimientos:** todo cambio de stock pasa por `inventory_service`. La base exige
  aritmética válida y stock no negativo. Las reversiones y compensaciones son movimientos nuevos.
- **Aprobaciones:** nadie aprueba lo que propuso (productos, categorías, proveedores, órdenes,
  ajustes y correcciones).
- **Transacciones:** documento y movimientos se confirman juntos; ante un fallo no queda nada.
- **Bloqueos:** un solo orden por id de producto; el documento se bloquea antes que el producto.
- **Concurrencia y errores:** deadlock y espera agotada → 409; violación de integridad → 409
  con la causa real; en Compras, reintento automático ante deadlock.

## 9. Riesgos y diferencias pendientes

1. **Fuente acreditada:** `RECONCILIATION_ACCREDITED_SOURCES` está vacío. Mientras no se acredite
   una fuente, la conciliación solo informa diferencias; los ajustes los solicita Inventario.
2. **`docker_support/verify.py`** está desactualizado frente a la separación operativa: espera que
   el administrador abra `/sales` y cree ventas. Es un problema previo de Ventas que no se tocó;
   no forma parte del arranque.
3. **`delivery_notes.currency`** se representa, pero el servicio no la asigna; la moneda
   documental sigue pendiente de autorización.
4. **Copias de proveedor heredadas:** 5 sin aprobación (sección 7).
5. **Diferencias con 5001:** esa línea tiene servicios y correcciones que no están aquí
   (`reconciliation_policy_service`, `reconciliation_runtime_service`, `reconciliation_schedule_service`,
   predictivo, reportes y su versión de Notas). Fuera de alcance por decisión del responsable.
6. **Red de Docker:** el host agotó los rangos de red automáticos; el laboratorio usó una subred
   explícita. Un despliegue nuevo podría necesitar lo mismo.
