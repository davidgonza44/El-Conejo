# Flujo operativo sintético validado

> Actualización: la separación vigente de transporte y trabajo privado está en
> [Landing → inbox → archive](RECONCILIATION_LANDING_INBOX_ARCHIVE.md).
> Las rutas/pruebas de transporte descritas abajo son evidencia de la fase anterior;
> una carpeta Receive Only no debe utilizarse como inbox mutable de la aplicación.

Preparado sin consultar la base empresarial, sin Navicat real y sin activar programación.
Los servicios de conciliación, captura y notificaciones no se modificaron en esta fase.

## Contrato del productor

```text
product_code;quantity
SYN-BENCH-00000001;11
```

UTF-8, BOM permitido, delimitador `;`, encabezado obligatorio, códigos de texto (conservar ceros iniciales), cantidades enteras no negativas. Límites existentes: 50.000 filas y 48 MiB. El productor de esta prueba entrega exactamente las dos columnas solicitadas; el parser conserva su soporte previo del nombre opcional.

Nombres de corte, fecha y horario de Caracas:

```text
inventario_YYYY-MM-DD_0800.csv
inventario_YYYY-MM-DD_1200.csv
inventario_YYYY-MM-DD_1600.csv
inventario_YYYY-MM-DD_1830.csv
```

La fecha y franja identifican el corte al que pertenecen los datos, no el momento de recepción. Un archivo producido a tiempo y transportado tarde conserva su nombre. No se debe generar stock actual tardío y etiquetarlo como si fuera una exportación histórica.

## Publicación y transporte listos

`scripts/reconciliation_file_bridge.py` ofrece tres operaciones explícitas, sin conexión de base de datos, daemon, tareas instaladas ni env empresarial:

1. `synthetic`: produce SKU y cantidades totalmente sintéticos, escribe temporal, cierra/fsync y publica por rename local.
2. `publish`: publica un temporal de exportación ya cerrado, sólo después de que el exportador termine correctamente. No sobrescribe un nombre final existente.
3. `transport`: copia el final a un temporal dentro del destino, verifica tamaño y SHA-256 de fuente/copia, vuelve a comprobar la fuente y publica por rename en el volumen destino. Rechaza crecimiento/cambio, nombres inválidos y conflictos. El rename sin sobrescritura está validado en Windows.

El transporte guarda `.transport_receipts/<nombre>.json` en la carpeta destino: mismo nombre/hash es duplicado incluso si la app ya archivó el CSV; mismo nombre con otro hash requiere revisión. No se borra la fuente. Los errores no publican un final parcial. Una caída antes del recibo puede ocasionar retransmisión; la idempotencia del corte en MariaDB sigue siendo la autoridad contra corridas duplicadas.

Ejemplos de invocación, **sin ejecutarlos contra carpetas operacionales activas**:

```powershell
& '<directorio-del-proyecto>\.venv\Scripts\python.exe' -B '<directorio-del-proyecto>\scripts\reconciliation_file_bridge.py' synthetic --exports 'C:\InventarioExports\PRUEBA_AISLADA' --day 2030-01-01 --slot 0800 --rows 20000

# Únicamente tras éxito/cierre del exportador; no basta esperar unos segundos.
& '<directorio-del-proyecto>\.venv\Scripts\python.exe' -B '<directorio-del-proyecto>\scripts\reconciliation_file_bridge.py' publish --temporary 'C:\InventarioExports\PRUEBA_AISLADA\export.csv.tmp' --name inventario_2030-01-01_0800.csv --export-completed

& '<directorio-del-proyecto>\.venv\Scripts\python.exe' -B '<directorio-del-proyecto>\scripts\reconciliation_file_bridge.py' transport --source 'C:\InventarioExports\PRUEBA_AISLADA\inventario_2030-01-01_0800.csv' --inbox 'D:\InventarioInbox\PRUEBA_AISLADA'
```

No se conectó Navicat ni se fabricó un archivo de perfil propietario. Queda definido el contrato para su perfil: consulta SELECT de sólo lectura, dos alias exactos, UTF-8, `;`, encabezado y salida temporal. El orden del batch futuro debe ser exportación satisfactoria → cierre → publicación → transporte. Su programación real y cualquier schtask siguen desactivados. El productor sintético ocupa ahora el lugar de esa exportación.

## Qué se ejecutó y dónde

Se preservó el worktree previo. Archivos de esta fase:

- Nuevo `scripts/reconciliation_file_bridge.py`: productor, publicación y transporte.
- Nuevo `scripts/test_reconciliation_file_bridge.py`: pruebas de archivos sin base de datos.
- Nuevo `scripts/test_reconciliation_file_pipeline.py`: casos completos, captura Core real en MariaDB privado, ingestión y notificación persistente.
- Modificado `scripts/test_reconciliation_disposable_server.py`: opción explícita para lanzar la nueva suite; mantiene la suite anterior por defecto.
- Nuevo `scripts/test_reconciliation_physical_transport.ps1`: prueba física exclusivamente sintética C: → D:, sin Python ni bases.
- Este documento.

Se crearon las carpetas solicitadas `C:\InventarioExports` y `D:\InventarioInbox`. No se enumeraron ni utilizaron archivos preexistentes; los datos están en subcarpetas nuevas `synthetic_transport_*`.

**Limitación del ejecutor:** `.venv` no tiene permiso de escritura sobre esas rutas externas. El contexto elevado de herramientas sí pudo crear/escribir las carpetas, pero no acceder al Python base de `.venv`. No se cambiaron ACL, `.venv` ni Python. Por ello las comprobaciones se separaron, manteniendo identidad de bytes:

```text
Productor sintético PowerShell
  → C:\InventarioExports\synthetic_transport_20261001_023900_7749c34e
  → copia temporal + SHA-256 + rename
  → D:\InventarioInbox\synthetic_transport_20261001_023900_7749c34e
  → transporte Python de esos mismos archivos, sin modificar sus bytes
  → carpeta aislada del workspace configurada como RECONCILIATION_INBOX_PATH
  → conciliación automática en MariaDB desechable
  → notificación interna persistente con enlace a la corrida
```

No se declara probado el acceso directo de la cuenta `.venv` a `D:\InventarioInbox` como carpeta de ingesta escribible. Para la futura activación deberá elegirse/verificarse una cuenta con permisos adecuados de lectura, publicación y archivado; esto es una condición de despliegue conocida, no información faltante del esquema empresarial. La prueba C: → D: fue local; no certifica una red entre dos equipos ni Syncthing/OneDrive.

En todos los ensayos MariaDB se creó servidor privado con datadir nuevo y puerto loopback distinto de 3306, se verificó `@@datadir`, se crearon únicamente bases `reconciliation_test_<aleatorio>` y se eliminaron al terminar. Los procesos privados se detuvieron. No se usaron credenciales de la app ni dotenv. Los relojes de captura/recepción se adelantaron sólo en la fixture para probar horarios nominales y tardanza sin esperar horas; la captura Core y las transacciones MariaDB fueron reales.

## Resultados

| Caso | Resultado comprobado |
|---|---|
| Correcto | completed, cantidades/filas correctas, archivo archivado con hash idéntico, notificación success. |
| Tardío 60 minutos | Primero waiting_source/info; tras llegada completed/success. Cantidad congelada 11 frente a stock sintético actual 18; comparó contra 11, sin modificar 18. |
| `.tmp` | Transporte lo rechaza; ingesta lo ignora; waiting_source, ninguna corrida. |
| Parcial/interrumpido | Se invocó la ingesta con sólo 65.536 bytes disponibles en el temporal: no vio final ni creó corrida. Se interrumpió el transporte y el reintento completo procesó 20k. |
| Duplicado después del archivado | Recibo lo reconoce, no vuelve a colocar archivo ni crea corrida/notificación adicional. |
| Mismo nombre, contenido distinto | Conflicto rechazado, evidencia original intacta. |
| Nombre incorrecto | `0900` rechazado por transporte e ignorado por ingesta; sin corrida. |
| CSV inválido | Delimitador/estructura inválida: failed, ninguna corrida, notificación critical, archivo en evidencia fallida. |
| 20k | 20.000 líneas, completed, success; transporte 0,148 s, proceso 3,399 s. |
| 50k | 50.000 líneas, completed, success; transporte 0,265 s, proceso 6,550 s. |

Tiempos orientativos de una ejecución, no reemplazan el benchmark de escalabilidad anterior. La notificación probada es la bandeja/campana de la app: un resumen por corte y usuario autorizado. Se comprobó destinatario administrador, enlace correcto y cero notificaciones al vendedor sin permiso. El proveedor de mensajes externos estuvo bloqueado deliberadamente; no se enviaron correos, WhatsApp ni SMS.

Las cuatro pruebas de archivos también pasaron: publicación tras exportación sin sobrescritura; fuente cambiante no publicada; fecha/nombre/ruta inválidos rechazados; destino conflictivo conservado.

Los archivos físicos C: → D: contienen CRLF, que también acepta el parser:

| Filas | Nombre | Bytes | SHA-256 idéntico fuente/destino/corrida |
|---:|---|---:|---|
| 20.000 | `inventario_2031-01-01_0800.csv` | 471.024 | `11447a08b5d36f79439732b963ac1576d180eca03122f11287e5498afc4c3e7f` |
| 50.000 | `inventario_2031-01-01_1200.csv` | 1.177.614 | `1760d5e04ec875b3e16d94cd7914d152f2f06fbf1aae460d4899224279ca5e24` |

Ambos archivos físicos fueron posteriormente ingeridos desde D: mediante staging al inbox aislado, con igual SHA-256 en ReconciliationRun y notificación success. No fueron meramente copiados: completaron el recorrido hasta la base desechable y su notificación.

Invariantes PRE/POST de la suite de escenarios: 50.000 productos; SUM(current_stock)=5.496.428; SUM(id*current_stock)=137.487.549.124; valores idénticos al finalizar. StockMovement=0. El cambio de stock del caso tardío fue sólo sintético y se repuso antes de comparar el baseline. La segunda suite con archivos físicos también comprobó igualdad PRE/POST y cero movimientos.

## Comandos y evidencia

Todos los comandos Python usaron `.venv\Scripts\python.exe`, nunca Python global.

```powershell
.\.venv\Scripts\python.exe -B scripts/test_reconciliation_file_bridge.py
# 4 pruebas PASS, exit 0.

.\.venv\Scripts\python.exe -B -W ignore::DeprecationWarning scripts/test_reconciliation_disposable_server.py --file-pipeline --exports-root '<directorio-del-proyecto>\tmp\InventarioExports' --inbox-root '<directorio-del-proyecto>\tmp\InventarioInbox'
# 10 escenarios PASS, exit 0.

& '.\scripts\test_reconciliation_physical_transport.ps1'
# Contexto de herramienta con acceso a C:/D:, 20k/50k PASS, exit 0.

.\.venv\Scripts\python.exe -B -W ignore::DeprecationWarning scripts/test_reconciliation_disposable_server.py --file-pipeline --exports-root '<directorio-del-proyecto>\tmp\InventarioExports' --inbox-root '<directorio-del-proyecto>\tmp\InventarioInbox' --physical-manifest '<directorio-del-proyecto>\tmp\synthetic_transport_20261001_023900_7749c34e\results.json'
# Dos archivos físicos completos hasta notificación PASS, exit 0.
```

Resultados locales, exclusivamente sintéticos:

- `tmp/synthetic_validation_20260930_223712_86486313/results.json`: diez escenarios; base `reconciliation_test_a89973cf469c`, eliminada.
- `tmp/synthetic_transport_20261001_023900_7749c34e/results.json`: publicación/copia física y hashes. El nombre de esta carpeta usa UTC.
- `tmp/synthetic_validation_20260930_224018_4df753f1/results.json`: ingestión de los archivos físicos, base eliminada.

Los intentos directos con `.venv` sobre C:/D: fallaron por permisos antes de crear una base de casos; los servidores privados correspondientes se detuvieron. La primera prueba de fuente cambiante reveló una lectura innecesariamente larga si el archivo crecía: se acotó la copia al tamaño inicial y se repitió con PASS. No se ocultó un fallo de permisos ni se modificaron ACL para evitarlo.

## Única información pendiente del sistema original

**El SELECT de origen validado que devuelva `product_code` y `quantity`.** Para construirlo se necesita el origen real (tabla/vista/joins), la expresión del código/SKU que coincide con el catálogo y la expresión de existencia conciliable, incluidos los filtros/agregaciones que correspondan. Debe devolver una fila por código y cantidades enteras compatibles. No se deben asumir columnas, almacenes, estados, unidades ni una existencia contable distinta de la que se quiere comparar.

Plantilla deliberadamente no ejecutable, sin nombres empresariales inventados:

```sql
SELECT
    <EXPRESION_REAL_DEL_CODIGO> AS product_code,
    <EXPRESION_REAL_DE_LA_EXISTENCIA> AS quantity
FROM <ORIGEN_REAL_Y_JOINS_CONFIRMADOS>
<FILTROS_O_AGRUPACION_CONFIRMADOS>;
```

Con ese SELECT, el contrato del archivo, publicación, transporte, nombres, comparación y notificación no requieren cambios por los nombres del esquema. La configuración/activación real del perfil Navicat, cuenta/permisos y programación requiere una fase de despliegue autorizada; no se da por ejecutada aquí.

## Límites operativos explícitos

- Un productor debe publicar sólo tras éxito/cierre. Un archivo truncado que ya tiene nombre final y sintaxis válida no se puede distinguir de un CSV parcial legítimo usando únicamente estas dos columnas. Estabilidad de tamaño o SHA-256 por sí solos no prueban integridad semántica.
- Un corte debe haber capturado a tiempo su snapshot local. La llegada posterior del CSV no reconstruye una captura perdida.
- CSV inválido queda failed; corregir el mismo corte exige revisión/reintento explícito y gestión de evidencia/recibo conflictivo. No se sobrescriben ni reparan automáticamente.
- El transporte probado es una copia explícita, no un servicio de sincronización entre equipos. No se instalaron tareas ni servicios.

**Confirmaciones:** ninguna conexión ni SELECT a la base empresarial original; tesis no usada ni modificada; cero stock/movimientos reales; cero CSV empresariales; cero exportaciones reales de Navicat; cero schtasks, Syncthing/OneDrive; cero commit/push/reset/restore/clean/stash. No se cambió predictivo ni captura Core. No se tomaron capturas de pantalla en esta fase.
