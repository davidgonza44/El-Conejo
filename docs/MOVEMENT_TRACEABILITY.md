# Trazabilidad de movimientos

## Alcance y despliegue

Implementada sobre el workspace existente; sus cambios anteriores se conservaron.
La primera fase terminó sin migrar tesis. El 2026-09-29, tras autorización explícita,
se aplicaron exclusivamente las dos columnas y CHECK descritos abajo a
`ferreteria_conejo_tesis`, con backup nuevo y controles PRE/POST. No hubo backfill.
El script exige `--database ferreteria_conejo_tesis --apply --authorize-thesis`;
sin la autorización rechaza ese destino antes de conectar. Otros destinos
operativos siguen bloqueados. Los destinos aleatorios de prueba siguen permitidos.

No se modificaron servicios, modelos ni pantallas de notas de entrega, código
predictivo ni documentos DOCX/PDF. Las pruebas usaron solamente datos sintéticos.

## Persistencia mínima

Solo se añaden a `stock_movements`:

```sql
creation_origin VARCHAR(20) NULL
external_reference VARCHAR(120) NULL
CHECK (creation_origin IS NULL OR creation_origin IN ('manual','initial_stock'))
CHECK (external_reference IS NULL OR
       (creation_origin IS NOT NULL AND creation_origin = 'manual'))
```

Sin valores por defecto, sin nuevas FK, sin números almacenados y sin backfill.
Los históricos conservan NULL en ambas columnas. La validación del servicio recorta
la referencia externa, convierte vacío a NULL y rechaza valores no textuales o de más
de 120 caracteres. Solo el controlador manual asigna `manual`; crear producto con
stock positivo asigna `initial_stock` mediante un argumento interno. Stock cero no
genera movimiento. Los servicios de documentos mantienen `creation_origin=NULL`.

Los campos automáticos se rechazan en las entradas HTTP pertinentes. Una referencia
externa que diga `VTA-001572` se conserva como texto; no crea ni busca asociaciones.

## Referencias calculadas y permisos

`movement_traceability_service` resuelve por lotes de hasta 500 movimientos:

| source_type | source_id calculado | Referencia | Destino autorizado |
|---|---|---|---|
| sale | Sale.id | Sale.sale_number | /sales/numero |
| purchase_reception | PurchaseReception.id | PurchaseOrder.order_number · REC-id con 5 posiciones mínimas | /purchases/receptions?detail=id |
| reconciliation | ReconciliationRun.id | ReconciliationRun.number | /reconciliation?detail=id |
| manual | StockMovement.id | MOV-id con 6 posiciones mínimas | /inventory?detail=id |
| initial_stock | product_id | MOV-id | Sin enlace documental |
| NULL | NULL | MOV-id | Origen no identificado, sin enlace |
| inconsistent | NULL | MOV-id | Origen inconsistente, sin enlace |

Venta se obtiene de SaleLine; recepción de PurchaseReceptionLine; conciliación de
ReconciliationLine. No se agregan `source_type`, `source_id`, `reference_number`,
`movement_number`, `display_reference` ni `source_url` como columnas.
Los motivos existentes no se reinterpretan ni se usan para resolver el origen.

Los permisos de origen son `sales:read`, `purchases:read`, `reconciliation:read`;
el movimiento manual requiere `inventory:read`. El vendedor solo recibe enlace a
ventas propias, usando la misma regla de alcance que el servicio de ventas. Las
páginas/API conservan sus propias autorizaciones. Sin contexto autenticado no se
generan enlaces. Permiso de inventario no concede permisos documentales.

La asociación con más de una línea de origen, o con origen estructurado y marca de
creación incompatible, se presenta como inconsistente, sin priorizar documentos.

Listado, detalle, resumen, movimientos por producto, creación y detalle de recepción
usan la misma presentación. La exportación existente consume esa presentación sin
cambiar sus columnas ni añadir un nuevo formato. La UI conserva IDs y eventos y
añade el campo externo; el éxito muestra el MOV devuelto por el servidor. Los
parámetros `detail` nuevos solo admiten enteros positivos dentro del rango INT.

## Límite deliberado: notas de entrega

Emisión y cancelación escriben `NE-...` dentro de `reason`, pero DeliveryNoteItem no
conserva una relación estructurada con esos movimientos. No se alteró ese flujo.
Sus movimientos sin otro vínculo se presentan con MOV y origen no identificado;
nunca se clasifican como manuales por descarte. No hay backfill basado en `reason`.

## Archivos de esta intervención

- `app/models/stock_movement.py`
- `app/services/movement_traceability_service.py` (nuevo)
- `app/services/inventory_service.py`
- `app/services/product_service.py`
- `app/services/sale_service.py`
- `app/services/reception_query_service.py`
- `app/controllers/inventory_controller.py`
- `app/templates/inventory/_movement_layers.html`
- `app/static/js/ix_movements_live.js`
- `app/static/js/ix_movements_live_layers.js`
- `app/static/js/ix_reconciliation.js`
- `app/static/css/ix_movement_layers.css`
- `scripts/migrate_movement_traceability.py` (nuevo)
- `scripts/test_movement_traceability_migration.py` (nuevo)
- `scripts/test_movement_traceability.py` (nuevo)
- `scripts/verify_movement_traceability.cjs` (nuevo)
- `scripts/test_inventory_core.py`
- `scripts/test_movement_detail.py`
- `scripts/test_movement_presentation.py`
- Este documento.

## Verificación de la primera fase

El entorno `python` global no tenía Flask-Login y `.venv` no tenía sklearn para las
suites de aplicación. Se usó `venv312` para estas suites, sin instalar dependencias.
La migración no inicia la aplicación y pudo probarse con `.venv`.

| Comando exacto | Resultado |
|---|---|
| `.venv/Scripts/python.exe -B scripts/test_movement_traceability_migration.py` | PASS: 2 pruebas de bloqueo; DDL real dos veces sobre una base nueva `conejo_trace_test_<12 hex>`; dos columnas nullable, CHECK efectivos e históricos intactos; borrado exclusivo de esa base creada por la prueba |
| `venv312/Scripts/python.exe -B -W ignore::DeprecationWarning scripts/test_movement_traceability.py` | PASS: 10 casos; venta propia/ajena, recepción parcial exacta, conciliación con/sin ajuste, manual, referencia externa, suplantación, stock inicial, históricos, inconsistencia, paridad y lectura sin escrituras |
| `venv312/Scripts/python.exe -B -W ignore::DeprecationWarning scripts/test_inventory_core.py` | PASS: 99 verificaciones |
| `venv312/Scripts/python.exe -B -W ignore::DeprecationWarning scripts/test_sales_core.py` | PASS: 93 verificaciones |
| `venv312/Scripts/python.exe -B -W ignore::DeprecationWarning scripts/test_purchase_approval.py` | PASS: 8 pruebas, incluidas recepción parcial/completa y rollback |
| `venv312/Scripts/python.exe -B -W ignore::DeprecationWarning scripts/test_reconciliation_core.py` | PASS: 9 pruebas |
| `venv312/Scripts/python.exe -B -W ignore::DeprecationWarning scripts/test_movement_detail.py` | PASS: 6 detalles, render real de tabla/drawer, tablas opcionales, 401/404, snapshot sin cambios |
| `venv312/Scripts/python.exe -B -W ignore::DeprecationWarning scripts/test_movement_presentation.py --no-pdf` | PASS: listado/detalle/producto/exportación, esquema UI, CSV/XLSX/HTML imprimible, snapshot sin cambios; no se generó PDF |
| `node scripts/test_movement_presentation.cjs` | PASS: resumen/refresh reales y filtros |
| `venv312/Scripts/python.exe -B -W ignore::DeprecationWarning scripts/test_predictive_operational.py` | PASS: 28 pruebas |
| `venv312/Scripts/python.exe -B -W ignore::DeprecationWarning scripts/test_predictive_module.py` | PASS: 209 verificaciones y 35 pruebas de modelos avanzados |
| `venv312/Scripts/python.exe -B -W ignore::DeprecationWarning scripts/test_movement_traceability.py --serve` | Servidor exclusivo de SQLite en memoria, MySQL bloqueado, loopback :5046 y sin concurrencia sobre su conexión de prueba |
| `node scripts/verify_movement_traceability.cjs` | PASS: Edge headless instalado, creación manual, enlaces exactos, permisos, parámetro malicioso ignorado, drawer directo, teclado y móvil |
| `git -c core.safecrlf=false diff --check` | PASS |

También pasaron `node --check app/static/js/ix_movements_live.js`,
`node --check app/static/js/ix_movements_live_layers.js`,
`node --check app/static/js/ix_reconciliation.js` y
`node --check scripts/verify_movement_traceability.cjs`. Se analizaron con
`ast.parse` los 13 archivos Python intervenidos, sin importar ni iniciar la
aplicación: sintaxis válida y sin espacios al final de línea. El servidor
sintético se detuvo al terminar las capturas.

Las expectativas anteriores de «—» se actualizaron a MOV. El harness de
presentación necesitó registrar la ruta de conciliación que utiliza el sidebar.
La primera prueba de navegador descubrió concurrencia sobre la conexión SQLite
compartida del harness: se corrigió solo el servidor sintético a `threaded=False`.
El navegador Chromium empaquetado no estaba instalado; se utilizó Edge existente.

Consultas medidas, con metadatos/permisos ya consultados: 5 SELECT para 1 y 5 filas;
la suite de presentación también comprobó 5 SELECT para 1 y 6 filas. Ningún SELECT
adicional por documento. Las lecturas de trazabilidad no emitieron INSERT/UPDATE/
DELETE/DDL y las filas sintéticas comparadas permanecieron idénticas.

Las suites SQLite no prueban los bloqueos de filas de MySQL. Se conservaron los
bloqueos y las transacciones existentes; no se ejecutaron suites contra la base E2E
preexistente ni contra tesis. La prueba MySQL real fue exclusivamente de migración.

Capturas sintéticas locales en `tmp/movement-traceability/`: `history-1600.png`,
`new-1600.png`, `manual-detail-1600.png`, `reconciliation-1600.png` y
`manual-detail-mobile.png`. Verifican esta funcionalidad; no se afirma paridad
visual nueva con un mockup. No contienen datos operativos.

Sin commit, push, reset, restore, clean ni stash. Detención antes de migrar tesis.

## Aplicación autorizada y regresiones posteriores

En la segunda fase se modificaron el guard de
`scripts/migrate_movement_traceability.py` y su prueba; se añadieron
`scripts/audit_traceability_thesis.py`, `scripts/smoke_traceability_thesis.py` y
`scripts/test_purchase_reception_disposable.py`. La suite
`scripts/test_receptions_core.py` admite excluir únicamente PDF mediante su
argumento interno `no_pdf`, sin cambiar las aserciones restantes.

El backup nuevo incluye estructura/datos y opciones explícitas para triggers,
routines y events. Los snapshots compararon conteos y SHA-256 de todas las tablas,
usando en POST las columnas originales de movimientos; también compararon saldos,
objetos almacenados y SHOW CREATE TABLE. Únicamente aparecieron las dos columnas
nullable y los dos CHECK previstos. Ambos conteos no NULL permanecieron en cero.
Las evidencias de esta ejecución se conservan localmente en el directorio nuevo
`instance/backups/traceability_20260929_032057_110723/`, incluido `RESULTADO.md`.

Regresiones posteriores: 3 guardas e idempotencia DDL real; 10 pruebas de
trazabilidad; 99 verificaciones de inventario; 93 de ventas; 8 de aprobación;
9 de conciliación; 179 aserciones de compras y 67 de recepciones en bases MySQL
aleatorias nuevas, eliminadas al terminar. Estas últimas conservan las pruebas
de concurrencia y transacciones de las suites existentes. El harness crea el
esquema actual directamente y verifica sus columnas, sin ejecutar migraciones
antiguas ajenas a esta fase. Predictivo: 28 pruebas operacionales y suite de
209 verificaciones con pruebas avanzadas. Detalle/presentación también pasaron,
incluida paridad y consultas por lotes. No se generaron PDF ni DOCX.

Smoke HTTP real: aplicación temporal en loopback, transacciones READ ONLY,
guard SQL y autenticación efímera de un administrador existente. Solo se sustituyó
el hook de persistencia de sesiones; se conservaron permisos y política de
contraseñas. No se hizo login persistente ni se crearon filas de prueba en tesis.
Las 14 respuestas fueron 200; VTA, OC/REC y MOV histórico se resolvieron mediante
datos reales. No existen conciliaciones reales en esta base: CONC se verificó
en pruebas aisladas. El servidor se detuvo; el snapshot posterior al smoke
coincidió con PRE. No se tomaron capturas de datos operativos.

Notas de entrega, ProductSupplier, Brand y resultados predictivos permanecieron
sin cambios. Cero backfill y cero modificaciones de movimientos históricos.
Sin operaciones Git de escritura; detención tras las verificaciones autorizadas.
