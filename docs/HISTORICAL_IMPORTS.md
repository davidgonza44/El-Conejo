# Importación histórica 2025 (CSV v1)

## Alcance

Este módulo conserva demanda histórica de 2025 en tablas separadas. No crea
movimientos, no modifica `products.current_stock` ni `products.minimum_stock`,
no crea notas de entrega y no alimenta los KPI operativos actuales. Tampoco
implementa predicción, pronósticos ni recomendaciones de reabastecimiento.

La zona horaria institucional es `America/Caracas`. CSV v1 acepta únicamente
fechas sin hora entre `2025-01-01` y `2025-12-31`, ambas inclusive. El
diagnóstico de suficiencia (`demand_data_service`) une esta fuente con las
notas operativas emitidas posteriores al `period_end` de los lotes
confirmados; no genera pronósticos. Ver `docs/PREDICTION_READINESS.md`.

## Archivo y columnas

- CSV UTF-8 con BOM (`utf-8-sig`), delimitado por punto y coma.
- Máximo 10 MiB por archivo y 12 MiB por petición multipart.
- Máximo 50.000 filas, 40 columnas y 4.096 caracteres por celda.
- Los encabezados van en la primera fila. Si no coinciden exactamente con la
  plantilla, un administrador debe definir el mapping de forma explícita.
- XLSX no se admite en v1. Su soporte queda como mejora posterior y no requiere
  `pandas` ni `openpyxl` en esta versión.

La plantilla vacía contiene:

| Columna | Uso |
|---|---|
| `event_date` | Fecha `YYYY-MM-DD` dentro de 2025. |
| `product_code` | Código recibido; se conserva el original y se compara en NFC, trim externo y mayúsculas. |
| `product_name` | Nombre opcional; solo permite sugerir una coincidencia manual. |
| `quantity` | Decimal positivo `DECIMAL(12,2)`, sin signo, NaN, infinito ni notación científica. |
| `record_type` | `sale`, `return`, `cancellation` o `correction`. |
| `record_status` | `issued`, `active`, `cancelled`, `voided` o `superseded`. |
| `document_number` | Documento normalizado usado para relacionar registros. |
| `source_record_id` | Identificador estable del registro en el sistema fuente. |
| `source_line_id` | Identificador de línea; conserva su capitalización. |
| `unit_price` | Decimal opcional no negativo, con máximo dos decimales. |

La matriz v1 permite todos los estados para ventas y devoluciones. Para
`cancellation` y `correction` se permiten `issued`, `active`, `voided` y
`superseded`; el estado `cancelled` se reserva para ventas/devoluciones. Los
estados `cancelled`, `voided` y `superseded` se conservan para auditoría y no
aportan demanda.

## Flujo administrativo

1. Cargar el archivo en almacenamiento privado. La carga no importa filas.
2. Revisar o definir el mapping y ejecutar preview.
3. Resolver productos inactivos, sugerencias por nombre, relaciones ambiguas y
   fingerprints débiles. El nombre nunca enlaza automáticamente un producto.
4. Ejecutar el dry run. Si no hay bloqueos, se emite un token de confirmación
   de un solo uso con expiración de 15 minutos.
5. Confirmar explícitamente. El archivo, staging, productos, relaciones y
   duplicados se revalidan dentro de una transacción única.
6. Una reversión es lógica: conserva archivo, filas y huellas, excluye el lote
   de demanda y restaura de forma auditada el registro sustituido. Un lote con
   dependencias confirmadas debe revertirse en orden inverso.

Una devolución o anulación debe apuntar a una venta/corrección identificable.
No se usan cantidades negativas. Una corrección supersede lógicamente el
registro anterior sin borrarlo. La demanda neta mensual negativa exige revisión
y nunca se reemplaza silenciosamente por cero.

## Matching y duplicados

La relación de productos prioriza el código normalizado exacto. Una colisión
entre dos productos bloquea todo el lote. El nombre normalizado solo crea una
sugerencia que un administrador debe confirmar. Un producto inactivo requiere
aprobación administrativa y queda identificado como tal.

El SHA-256 impide confirmar o volver a cargar el mismo archivo, incluso si su
lote fue revertido. El fingerprint de negocio versionado no usa nombre de
archivo ni número físico de fila. Si falta `document_number` o
`source_line_id`, la huella es débil, se muestra como posible duplicado y no se
deduplica automáticamente.

## Seguridad y retención

El archivo se guarda bajo un UUID en `instance/historical_imports`, fuera de
`static`, `media` y rutas públicas. La ruta privada nunca forma parte de la API.
Solo administradores ven sus metadatos. Los valores exportados se neutralizan
si comienzan por `=`, `+`, `-` o `@`; no se ejecutan fórmulas ni SQL dinámico.
No se deben importar nombres, correos, documentos de identidad ni otros datos
personales de clientes.

V1 conserva los archivos para trazabilidad y defensa y no realiza eliminación
automática. Antes de producción se debe aprobar una política de retención con
plazo, archivo legal, borrado seguro y registro de autorización.

La cuenta de ejecución de producción debe tener `SELECT` sobre `products` y
`categories`, y lectura/escritura solamente sobre `historical_imports`,
`historical_demand_records` y `historical_import_errors`. La migración DDL se
ejecuta con una cuenta de despliegue separada. El script no crea usuarios de
MySQL ni modifica la conexión local.

## Permisos

- Admin: read, export, upload, review, confirm y revert.
- Inventario: read y export.
- Vendedor: sin acceso.

Todas las escrituras HTTP requieren autenticación, permiso granular y token
CSRF. La confirmación requiere además su token efímero propio.
