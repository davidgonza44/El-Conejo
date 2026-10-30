# Cierres diarios persistidos: DEMO 5001 activa

El 10/10/2026 se desplegó y activó la candidata autorizada en **DEMO 5001**.
App healthy y worker operativo, con reinicio comprobado. La programación del
cierre diario está activa exclusivamente en el worker a las **00:05
America/Caracas**. Los cortes de conciliación automáticos permanecen desactivados.

Primera ejecución prevista: **11/10/2026 00:05 Caracas (04:05 UTC)**, atribuida
a `snapshot_date=2026-10-10`. En la verificación posterior al despliegue había
cero cierres: el primer registro debe proceder del turno real, sin captura manual.

Imagen desplegada: `conejo-portable:daily-prepared-0007-20261010`, identificador
`sha256:6946c05ce35301fa00a23d95262ae66c5b7b44c7adc8b25bb869b065eaafa282`.
La revisión sigue en `0007_catalog_currency`; no hubo migraciones y 0008 no está
empaquetada. Se conservaron los 19 conjuntos, los 259 movimientos, precios,
permisos y configuración, con las mismas huellas antes y después.

La integración reutiliza `InventorySnapshot`, la tabla `inventory_snapshots` y
`inventory_snapshot_service`. El worker portátil llama al ejecutor
`scripts.take_inventory_snapshot`; Dockerfile y `.dockerignore` lo incluyen.
No se agregan modelos, tablas, rutas, dependencias ni tareas de conciliación.

El horario existente era aproximadamente **00:05 America/Caracas**, definido en
el servicio y el ejecutor. Ahora se expresa como `CLOSING_TIME = time(0, 5)`.
La ventana de admisión es **[00:05:00, 00:06:00)**, con revisión del worker cada
20 segundos. Esa ventana permite reintentos breves; no autoriza recuperar turnos
perdidos. Se verifica nuevamente después del bloqueo y del inicio de la transacción.

`snapshot_date` identifica el día comercial recién terminado. `created_at` guarda
la hora UTC real de captura, común a todas las filas del cierre. La fotografía
incluye cambios ocurridos entre medianoche y las 00:05; **no equivale a reconstruir
las existencias exactas de las 23:59**. Se conservan stock, mínimo, categoría y
actividad de producto/categoría tal como se leen, incluidos los productos inactivos.
No se copian precios históricos ni se infieren valores monetarios pasados.

## Idempotencia y reinicios

En MariaDB/MySQL se comprueba que productos, categorías y snapshots sean InnoDB.
Un bloqueo `GET_LOCK` por base y día impide capturas simultáneas. Una conexión con
`REPEATABLE READ` y `START TRANSACTION WITH CONSISTENT SNAPSHOT` lee todo el catálogo
y guarda todas las filas en una transacción. La restricción existente
`UNIQUE(snapshot_date, product_id)` sigue siendo la protección de persistencia.

La tabla es la fuente de verdad, sin una marca de éxito en archivos que pueda
adelantarse al commit. Si el proceso termina antes del commit, MariaDB revierte
la captura; otro intento dentro de la ventana puede completarla. Después del
commit, un reinicio no sustituye ni amplía las filas originales. Fuera de la
ventana el servicio también rechaza la escritura. Un catálogo vacío queda sin
cierre consultable. No se añaden filas para compensar días perdidos.

La variable `INVENTORY_SNAPSHOT_ACTIVATED_AT` debe contener un instante ISO 8601
con zona horaria. Vacía significa desactivado. Una fecha inválida falla sin
capturar. Solo se admite un turno cuya hora programada sea igual o posterior a
la activación; activar después de las 00:05 pospone el primer cierre hasta el
siguiente turno diario. La configuración de cortes de conciliación es distinta:
`RECONCILIATION_ACTIVATED_ON`; sus cuatro horarios y su servicio no cambian.

El ejecutor público exige la base explícita y no acepta fechas ni opciones de
captura forzada. En Docker verifica entorno, base e identidad de instalación
antes de escribir. El argumento interno `now` sirve para pruebas en bases
desechables y no está expuesto por CLI, HTTP ni UI. Los fallos del cierre se
registran sin detalles privados y no interrumpen el bloque de tareas existentes.

## Lectura histórica

Resumen de Inventario ya consulta `snapshot_kpis(ayer)` y Productos consulta los
snapshots del último día del período. Se reutilizan esas consultas y sus permisos.
No se modifica la interfaz para esconder estados sin historia. Las comparaciones
sin cierre guardado siguen sin datos; el valor histórico del inventario sigue
sin comparación de precios cuando no existe historia monetaria.

Rutas existentes para consultar los resultados:

- Resumen: `http://localhost:5001/inventory/summary`.
- Productos, selector de período y comparación: `http://localhost:5001/products`.
- API GET del Resumen: `/api/inventory/summary-dashboard`.
- Primer cierre, con sesión y permiso de lectura de Productos:
  `/api/products/history/summary?start=2026-10-10&end=2026-10-10&compare=none`.

Después del primer cierre real, el **11/10 después de las 00:05**, Resumen podrá
comparar existencias actuales con el cierre del 10/10. En Productos, seleccionar
«Hoy» y «Período anterior» para esa comparación. Después del segundo cierre real,
el **12/10 después de las 00:05**, se podrán comparar dos días cerrados: período
personalizado 11/10–11/10 con período anterior 10/10–10/10. Los períodos más
largos solo tendrán comparación cuando exista cierre para ambos extremos
requeridos; las fechas anteriores al inicio permanecen sin datos históricos.

## Validación reproducible

Suite fuente: `scripts/test_inventory_daily_closures.py`.

```powershell
python -B scripts/test_inventory_daily_closures.py -v
```

Sin argumentos de MariaDB utiliza exclusivamente SQLite en memoria. La prueba
MariaDB exige `DOCKER_ENV_KIND=disposable`, nombre
`conejo_disposable_daily_<8 hex>`, hostname esperado del servidor, red privada
`db:3306`, dotenv desactivado y revisión `0007_catalog_currency`. Rechaza DEMO
como destino. No inicializa ni migra el esquema.

La demostración de esta preparación está conservada en
`.tmp/daily-closures-20261010/`. Su ejecutor local no implementa despliegue ni
activación de DEMO. Para repetir sobre la copia aislada retenida:

```powershell
docker start conejo-daily-isolated-20261010-db conejo-daily-isolated-20261010-test
python -B .tmp/daily-closures-20261010/isolated.py reset-isolated-tests
docker exec conejo-daily-isolated-20261010-test python -B scripts/test_inventory_daily_closures.py -v
docker exec conejo-daily-isolated-20261010-test python -B scripts/test_product_history.py
docker exec conejo-daily-isolated-20261010-test python -B scripts/test_inventory_daily_closures.py --mariadb
docker stop conejo-daily-isolated-20261010-test conejo-daily-isolated-20261010-db
```

El reset comprueba la identidad desechable y elimina solamente cierres sintéticos
de esta suite; el respaldo restaurado tenía cero cierres. Antes del despliegue,
`isolated.py tests` ejecutó las suites y el ejecutor empaquetado desactivado.
Ese orquestador conserva la línea base anterior de contenedores y ya no debe
usarse para validar DEMO desplegada; las órdenes anteriores ejecutan las suites
directamente contra la copia desechable.

Las fechas comerciales simuladas pertenecen a esa copia, mientras `created_at`
se obtiene del reloj real. No se presenta la simulación como historia de DEMO.

La suite verifica rollback después del INSERT, terminación de un proceso antes
del commit, reinicio después del commit, seis intentos concurrentes, vista
consistente durante un cambio concurrente y restauración de ese stock de prueba.
Ejecuta el bucle principal real del worker con reloj comercial simulado. También
invoca las vistas GET con sus permisos reales, rechazando SQL de escritura.

El censo de 19 conjuntos es el de la auditoría previa: 17 tablas operativas e
históricas, existencias de productos y movimientos. En la copia solo cambia
`inventory_snapshots`; en DEMO los 19 deben permanecer idénticos. Si cambian por
actividad del usuario, la comprobación se detiene y exige revisar la nueva línea
base, sin modificar ni restaurar datos automáticamente.

## Migraciones y respaldo

**No se necesita migración para DEMO auditada.** Ya existen la tabla, sus columnas,
la restricción única y las tres tablas InnoDB. La revisión permanece en 0007.
Si otra instalación no cumple ese esquema, detener la activación y preparar una
migración específica para revisión; el worker no crea ni repara tablas.

La candidata se deriva de la imagen DEMO verificada y solo cambia cuatro fuentes
de ejecución. Conserva las migraciones de esa imagen y excluye 0008. Para este
despliegue no debe construirse desde todo el workspace ni ejecutarse
el inicializador o `alembic upgrade head`: incluiría trabajo fuera del alcance.

El respaldo completo de DEMO se guarda en un volumen Docker privado separado;
su contenido no se imprime, incorpora a Git ni empaqueta en la candidata. La
restauración en MariaDB aislada se verifica contra los 19 conjuntos auditados.
El informe local registra volumen, ruta interna, tamaño, SHA-256 e imagen base.
Se renovó antes del despliegue y se probó una restauración nueva en MariaDB
10.11.14, red interna sin puertos. Ubicación: volumen Docker
`conejo-daily-release-test-20261010-backup`, archivo interno `/backup/demo.sql`,
2 026 768 bytes, permisos `0600`, SHA-256
`3a3a81b0cca50ae8761a5087c2b8ad2101b1717308f6b86b264476a229fe0f6e`.
Se conserva también el respaldo de la preparación. Ningún dump se copió al
workspace ni se imprimió.

## Activación aplicada y verificación del primer cierre

Las fases autorizadas se ejecutaron en orden. La candidata se desplegó primero
con cierres desactivados; después de pasar salud, identidad, preservación y
29 vistas GET, se recreó solamente el worker con
`INVENTORY_SNAPSHOT_ACTIVATED_AT=2026-10-10T09:51:32.311364-04:00`.
La app conserva la variable vacía y `RECONCILIATION_ACTIVATED_ON` sigue vacío
en ambos procesos. La configuración sobrevive a reinicios del worker y Docker
mediante `restart=unless-stopped`; no depende de esta sesión del asistente.

El servicio utiliza su horario fijo; no se registró una tarea de respaldo ni
una tarea de cortes para representar este cierre. No ejecutar `compose up` desde
el workspace sin preparar otra publicación revisada: esta entrega usa una imagen
concreta y conserva trabajo ajeno, incluida 0008, fuera de la imagen.

Desde `<directorio-del-proyecto>`, después de las **00:06 del 11/10**, ejecutar:

```powershell
python -B .tmp/daily-deploy-20261010/verify_first_close.py
```

Este comando está probado en lectura sobre DEMO y **no ejecuta cierres**. Revalida
la identidad, Alembic 0007 y el heartbeat. Agrupa `inventory_snapshots` por fecha,
comprueba un solo instante de captura por día, productos sin duplicados, hora
UTC/Caracas auténtica y atribución al día anterior. El resultado se conserva en
`.tmp/daily-deploy-20261010/first-close-verification.json`.

Resultado esperado: `status=saved`, fecha `2026-10-10`, instante local dentro de
`[2026-10-11 00:05, 00:06)`, y una fila por producto presente en la captura.
El catálogo actual tiene 52 productos, incluidos inactivos; si cambia antes del
turno, comprobar la cobertura del catálogo que existía entonces. No comparar
stock archivado contra stock actual después de nuevos movimientos para decidir
si el cierre fue correcto. La prueba MariaDB ya verificó la lectura consistente
y la conservación de stock/fechas durante la captura.

Antes del turno devuelve `pending_real_window`. Si se pierde el primer turno,
devuelve `missed_no_backfill` y código 2: conservar el hueco, revisar el worker y
esperar el siguiente turno. Los días anteriores no se rellenan. El log del worker
debe contener `Daily close saved: 2026-10-10, N products.` tras el commit real.

El informe de despliegue, las huellas y las pruebas de reversión están en
`.tmp/daily-deploy-20261010/INFORME.md`. La comprobación inicial de tablas y las
pruebas con cierres guardados en MariaDB aislada no equivalen a afirmar que el
primer turno futuro ya se ejecutó.

## Reversión conservada

La sustitución, activación, reinicio y reversión se probaron en la copia aislada.
También se ejecutaron allí las funciones exactas de los comandos de operador,
sin puertos publicados y conservando los 19 conjuntos. Los contenedores de
prueba quedaron detenidos; el respaldo privado y los contenedores anteriores
de DEMO quedaron retenidos.

Para desactivar solamente el cierre diario, usar cuando el operador lo decida:

```powershell
python -B .tmp/daily-deploy-20261010/rollback.py activation
```

Sustituye el worker activo por el contenedor de la misma candidata con la variable
vacía. La app permanece healthy. Para revertir también la imagen desplegada:

```powershell
python -B .tmp/daily-deploy-20261010/rollback.py deployment
```

Restaura los originales retenidos
`conejo-demo-8b867d4b-app-rollback-daily-20261010` y
`conejo-demo-8b867d4b-scheduler-rollback-daily-20261010`, imagen
`sha256:e1530290330e7e455d7504e66bbaeda4c2b6709ec92041449c29ab42b7aa278b`.
Los comandos verifican identidad/configuración, conservan los contenedores
reemplazados y no hacen operaciones SQL de escritura ni restauraciones de datos.
Conservar todos los cierres ya persistidos. No ejecutar downgrade, borrar cierres
ni restaurar el respaldo sobre DEMO como parte de esa reversión. Una restauración
de datos requeriría una autorización específica.

Fuentes nuevas: TRACK para revisión futura. Evidencias y scripts locales `.tmp/`:
IGNORE. Respaldo privado: REVIEW, conservado fuera del workspace. Esta entrega
no hace staging, commit ni push, ni modifica tesis, experimento predictivo,
migración 0008, base real o servicio publicado en 5000.
