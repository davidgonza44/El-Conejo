# Base sintética de demostración de tesis

Estado: ejecución local autorizada. El resultado de la carga y de sus validaciones
se registra en `SEED_TESIS_MYSQL.md`; la simulación permanece como referencia.

Este conjunto es **100 % sintético**, destinado a demostración, capturas del tomo,
defensa y aceptación del software. No es evidencia empírica del negocio.
Los resultados científicos siguen utilizando exclusivamente los históricos reales
2026 ya auditados. Este seed no lee, copia ni importa esos históricos; rechaza una
base destino con registros de importación histórica o demanda histórica.

## Archivos y aislamiento

- `scripts/seed_tesis.py`: CLI y generación mediante servicios existentes.
- `scripts/seed_tesis_data.py`: catálogo y plan determinista, sin conexiones.
- `scripts/seed_tesis_safety.py`: conexión, transacción, identificación y reset.
- `scripts/prepare_seed_tesis.py`: preparación explícita de la tabla interna.
- `scripts/test_seed_tesis.py`: integración aislada en SQLite temporal desechable.
- `scripts/audit_seed_tesis.py`: reproduce la revisión completa sin forecasting pesado.
- [SEED_TESIS_SIMULATION.md](SEED_TESIS_SIMULATION.md): resultados por producto y categoría,
  riesgos, reabastecimiento, ejemplos y búsqueda de etiquetas del conjunto revisado.

`scripts/seed_large_demo.py` permanece intacto. No se cambia la configuración,
el esquema comercial, las rutas ni la interfaz de la aplicación. No hay nuevas
dependencias. La única tabla nueva es la interna autorizada `seed_manifests`,
definida en el módulo CLI, fuera del registro de modelos de la aplicación.

## Volúmenes reproducibles

Semilla: `20260921`. Período fijo: **01/01/2026–20/09/2026**, 263 días.
Revisión del conjunto: `tesis-synthetic-v2`, versión 2. Un manifiesto de la
revisión anterior se rechaza; no se migra ni se regenera silenciosamente. La
estructura del manifiesto y las protecciones de conexión/reset se conservan.

| Entidad | Cantidad |
|---|---:|
| Categorías | 10 |
| Productos | 120 |
| Proveedores | 10 |
| Clientes | 60 |
| Responsables ficticios | 3: uno inactivo y dos activos, todos sin contraseña |
| Ventas | 1.571 |
| Líneas de venta | 3.783 |
| Cotizaciones / líneas | 40 / 80 |
| Pedidos / líneas | 40 / 80 |
| Órdenes de compra / líneas | 351 / 926 |
| Recepciones / líneas | 331 / 906 |
| Movimientos | 4.817 |

Movimientos: **120 entradas iniciales + 906 entradas de recepción + 3.783 salidas
de venta + 8 ajustes de una unidad**. El saldo resultante incluye **5 agotados y
16 productos con existencias positivas bajo o en el mínimo**; 99 quedan sobre
el mínimo. No se ajustan los saldos finales para obtener esos indicadores.

Los carritos se generan por finalidad de compra y familias complementarias, con
rotaciones distintas y cantidades por tipo de producto. No contienen cemento
obligatoriamente. Cada familia tiene referencias populares, ocasionales, una
retirada de la demanda después de marzo, una reciente y una sin ventas. Estos
perfiles existen solo en el generador, nunca en nombres o descripciones visibles.
Las preferencias comerciales varían por familia: pinturas de un galón, PVC de
presión y sanitario, tornillos drywall y de madera, herramientas de ocho pulgadas,
cables de distintos calibres y colores, pegamento y sellador, discos de corte y
diamantados. Se asignan historias de compra a esas presentaciones sin derivarlas
del orden del código. El clasificador real determina la elegibilidad; el generador
no escribe estados predictivos. Este refinamiento previo a ejecución conserva la
revisión 2, aún no instalada en MySQL.
Se proponen seis tickets diarios: siete solicitudes quedaron completamente sin
existencias y no se convierten en ventas ficticias; quedan 1.571 ventas efectivas.

Se revisa reposición semanalmente, se recibe dos días después y algunas
referencias tienen abastecimiento limitado e intermitente durante todo el
período. Se vende únicamente la cantidad disponible: las cantidades solicitadas
no atendidas no se registran como ventas ni como reservas ficticias.

Los nombres y códigos son comerciales, por ejemplo `Cemento gris 42,5 kg`,
`CEM-002`, `Tubo PVC presión 1/2 pulgada x 3 m`, `PVC-001`.
Los nombres de personas y proveedores se inventan, sin representar identidades
reales. Los RIF son ficticios y no se verifican ante organismos. Los clientes
no necesitan contactos; los proveedores usan correos `example.invalid` no
entregables y «No registrado» en teléfono (el servicio exige texto no vacío).
No se simulan datos de contacto reales para completar campos obligatorios.

## Estados y límites reales del sistema

| Entidad | Estados generados |
|---|---|
| Ventas | 1.571 `completed`; único estado que escribe el servicio |
| Compras | 321 `completed`, 10 `partial`, 10 `pending`, 10 `cancelled` |
| Recepciones | 321 completas y 10 parciales respecto a su orden; no se inventa una columna de estado |
| Pedidos | 10 `pending`, 10 `processing`, 10 `completed`, 10 `cancelled` |
| Pago de pedidos | 15 `paid`, 25 `pending` |
| Prioridad de pedidos | 10 de cada una: `low`, `medium`, `high`, `urgent` |
| Cotizaciones persistidas | 16 `pending`, 8 `sent`, 8 `accepted`, 8 `rejected` |
| Cotizaciones visibles al 21/09/2026 | 8 pendientes, 8 enviadas, 8 aceptadas, 8 rechazadas, 8 vencidas |

Las cotizaciones vencidas se calculan por fecha; no se persiste `expired`.
Con el paso del tiempo las cotizaciones abiertas restantes también vencerán.
No se modifica el reloj de la aplicación para mantenerlas abiertas.

Ventas: efectivo (505), transferencia (693), débito (311) y crédito (62), todas
de contado. No se crean cuentas por cobrar ni un libro de pagos inexistente.
El método se elige con pesos distintos y aleatoriedad reproducible. No representa
una estadística real de medios de pago del negocio. Cotizaciones y pedidos
rotan entre los clientes existentes mediante una permutación reproducible; no
se crean clientes adicionales ni se concentra la primera página en un apellido.
Cotizaciones y pedidos se crean mediante sus servicios; no se inventa conversión
cotización→pedido→venta. Incluso un pedido completado no mueve inventario en el
modelo actual. No se generan devoluciones ni notas de entrega adicionales.

Las salidas conservan `Venta VTA-…`; las entradas de recepción conservan
`Recepción … · OC-…` y los vínculos reales de sus renglones. El campo «Referencia»
del detalle de movimientos sigue mostrando lo que soporte la UI existente;
el seed no modifica esa pantalla. Los ocho ajustes registran deterioro de una
unidad en revisiones de almacén.

Las ventas aportan historia operativa para Predictivo, con referencias frecuentes,
intermitentes y sin ventas. No se fabrican métricas, pronósticos, entrenamiento,
calendarios operativos ni cierres diarios. Las series frecuentes permiten probar
elegibilidad; las pantallas siguen mostrando estados honestos cuando el modelo,
la fecha actual o los datos necesarios no permitan un resultado. No se presenta
esta historia sintética como importación auditada.

## Manifiesto y reset conservador

`seed_manifests` guarda identificador del conjunto, versión, indicador
`synthetic=true`, semilla, UUID del entorno, UUID de ejecución, base destino,
fecha, período, IDs exactos por tabla y huellas SHA-256 de todas sus columnas.
También guarda una huella del contenido JSON. Los IDs se capturan al insertar
mediante la sesión ORM, incluidos los renglones y movimientos creados por los
servicios. No se deducen por códigos, nombres, correos, motivos ni rangos.
La clave de idempotencia de venta es un UUID determinista; no identifica la
propiedad para el reset ni se expone como texto comercial.

Sin manifiesto, el primer seed exige vacías las 16 tablas comerciales que genera,
incluida `users`. El esquema y tablas auxiliares de permisos pueden estar
preparados. No adopta datos preexistentes. Una segunda ejecución sobre un seed
intacto es un no-op; si hubo cambios, aborta. No hay opción `--force`.

Antes del reset se comprueban versión, conjunto, UUID, IDs, huellas y filas
faltantes. Se inspeccionan todas las claves foráneas entrantes y se rechazan
referencias ajenas, además de notificaciones polimórficas y cierres por categoría.
Si aparece una relación desconocida, un cambio o un error, no se elimina nada.
La eliminación se limita a IDs registrados, en orden de dependencias.
Datos y manifiesto se regeneran dentro de una sola transacción exterior;
los commits de los servicios liberan SAVEPOINTs, no confirman parcialmente.
Un fallo después de borrar revierte también el borrado.

Se conservan UUID del entorno y semilla; cambia el UUID de ejecución. MySQL puede
asignar nuevos IDs y números `VTA`/`OC`/`PED`/`COT` después de un reset: no se
reinician AUTO_INCREMENT ni se promete igualdad byte a byte. El catálogo, plan,
cantidades, fechas, relaciones y distribuciones son reproducibles.

Las huellas detectan modificaciones, no autentican frente a un administrador de
BD que pueda reescribir tanto datos como manifiesto. La tabla debe permanecer
fuera de la UI y bajo permisos administrativos; no es un secreto ni un sustituto
de control de acceso. No se escriben manifiestos en productos ni archivos externos.

«Rafael Villalba» queda inactivo y firma únicamente las existencias iniciales de
enero, como responsable histórico. «Mariana Duarte» está activa con rol inventario
y firma compras, recepciones y ajustes. «Daniel Acosta» está activo con rol
vendedor y firma ventas, cotizaciones y pedidos. Los tres tienen `password_hash`
nulo, correo no verificado y correo `example.invalid`; no se crean contraseñas,
identidades externas ni tokens de acceso. Los principales solo se seleccionan
dentro del contexto CLI. Activo describe su estado operativo en este escenario,
no la entrega de credenciales. El acceso para defensa se gestiona por separado.
La actividad posterior puede bloquear el reset para preservar la aceptación.

## Comandos para revisión; NO ejecutados en MySQL

Ejecutar desde la raíz del repositorio, **solo después de autorizar y preparar
por separado la base de tesis y su esquema comercial**. Estos scripts nunca
crean la base ni ejecutan migraciones del dominio. Detener Flask, jobs,
programadores y otros escritores durante preparación/seed/reset. El bloqueo
nombrado serializa estas CLI entre sí; no detiene la aplicación.

Variables explícitas de la sesión PowerShell:

```powershell
$env:DB_HOST = 'localhost'
$env:DB_NAME = 'ferreteria_conejo_tesis'
$env:DB_PORT = '3306'
$env:DB_USER = 'root'
$env:DB_ALLOW_EMPTY_PASSWORD = '1'
```

`DB_USER=root` y `DB_PORT=3306` son obligatorios. Una contraseña normal se toma de
`DB_PASSWORD`; vacía o ausente requiere `DB_ALLOW_EMPTY_PASSWORD=1`, que selecciona
explícitamente contraseña vacía. No se imprimen contraseñas ni se leen `.env`.
`DATABASE_URL` y
`SQLALCHEMY_DATABASE_URI` deben estar ausentes o vacías. No imprimir secretos.
Host y base distintos abortan antes de conectar; también se verifica
`SELECT DATABASE()` antes de escribir, resolución loopback, InnoDB, ausencia de
triggers afectados y relaciones entre bases. No se acepta un destino remoto.

La base vacía y las tablas de todos los modelos pueden prepararse explícitamente
con `python -B scripts/prepare_seed_tesis_database.py`. Usa `utf8mb4_unicode_ci`
y el mismo `metadata.create_all()` usado por SQLAlchemy en `init_db.py`, sin sus
semillas genéricas. Rechaza una base que ya tenga tablas para no alterar esquemas.
En MariaDB el manifiesto acepta la representación JSON como LONGTEXT únicamente
si conserva la restricción exacta `json_valid(payload)`.

Para iniciar Flask con destino verificado y sin leer `.env`, después de establecer
las cinco variables anteriores: `.\venv312\Scripts\python.exe -B scripts/serve_seed_tesis.py`.
Sirve en `http://127.0.0.1:5000`, sin debug ni reloader. No crea usuarios de acceso.

**Preparación explícita, separada (DDL de una sola tabla):**

```powershell
.\.venv\Scripts\python.exe -B scripts/prepare_seed_tesis.py
```

Si existe, valida su estructura y no la cambia. Una estructura desconocida
aborta; no se intenta migrarla ni reemplazarla. DDL MySQL tiene commit implícito,
por eso este paso nunca forma parte de la transacción de generación.

**Seed:**

```powershell
.\.venv\Scripts\python.exe -B scripts/seed_tesis.py
```

**Reset exclusivo del seed intacto:**

```powershell
.\.venv\Scripts\python.exe -B scripts/seed_tesis.py --reset-seed
```

Un reset rechazado requiere revisión manual; no borrar la tabla de manifiestos
para eludirlo ni modificar el seed técnico.

**Plan sin conexión y suite aislada:**

```powershell
python -B scripts/seed_tesis.py --plan
.\.venv\Scripts\python.exe -B scripts/test_seed_tesis.py
.\.venv\Scripts\python.exe -B scripts/audit_seed_tesis.py
```

La suite crea únicamente SQLite en una carpeta temporal propia y la elimina
al cerrar. Bloquea conexiones PyMySQL y no usa la fábrica de la aplicación,
almacenamiento de usuarios, históricos ni archivos de configuración. Comprueba
balances, documentos, datos visibles, estados, elegibilidad predictiva,
idempotencia, reset intacto, rechazo de modificaciones/dependencias y rollback.
SQLite **no acredita** bloqueos MySQL, DDL MySQL ni ejecución con el esquema
destino: esas verificaciones quedan pendientes de autorización. No se tomaron
capturas ni se declara paridad visual.

## Verificación ejecutada el 22/09/2026

- `.\.venv\Scripts\python.exe -B scripts/test_seed_tesis.py`: **22 pruebas OK**,
  salida 0 en la ejecución final. Incluye preparación aislada y rechazo de base
  efectiva incorrecta. Se comprueban exactamente 18 elegibles en las mismas nueve
  categorías y al menos seis sufijos comerciales distintos,
  ventas independientes en todas las familias, variedad de clientes/pagos y
  responsables activos en los movimientos recientes, además de saldos y reset.
- `.\.venv\Scripts\python.exe -B scripts/audit_seed_tesis.py | Out-File -LiteralPath docs/SEED_TESIS_SIMULATION.md -Encoding utf8`:
  salida 0. Generación final completa, aislada: 18 elegibles en nueve categorías,
  60 intermitentes, 13 insuficientes, 12 inactivos por demanda y 17 sin demanda.
  42 candidatos a reabastecimiento con tasa media; cero etiquetas prohibidas en
  139.692 campos de 13.128 registros. No se ejecutó entrenamiento de modelos.
- `python -B scripts/seed_tesis.py --plan`: salida 0; volúmenes de esta guía.
- Los ocho archivos del conjunto se revisan individualmente con
  `git -c core.autocrlf=false diff --no-index --check -- /dev/null <archivo>`:
  sin diagnósticos de espacios; salida 1 por tratarse de archivos nuevos.
- Análisis sintáctico con `ast.parse`, sin generar bytecode: 6/6 scripts válidos.
- `Get-FileHash scripts/seed_large_demo.py -Algorithm SHA256`: idéntico antes y
  después: `13062DD0825DF784188A81E5B61F4617EEC421E5600E57CB573615F0827B274A`.

Persisten avisos de deprecación de `datetime.utcnow()` en código/dependencias
existentes; no son fallos de las pruebas. No se ejecutó MySQL, no se creó la base,
no se tomaron capturas y no se realizaron operaciones Git de escritura.

Refinamiento final de referencias: CEM-001/003, PIN-002/005, PVC-001/005,
CON-003/007, TOR-004/007, HER-002/005, CAB-002/004, PEG-001/008 y DIS-001/005.
La primera comprobación del refinamiento detectó 17 elegibles (una presentación
de discos tenía abastecimiento limitado); se corrigió la selección comercial y
se repitieron íntegramente suite y auditoría. No se cambiaron reglas predictivas.
Se mantienen las distribuciones de demanda, tickets por categoría, pagos y los
42 candidatos a reposición; los importes cambian con las presentaciones vendidas.
Respecto a la revisión previa hay una compra y una recepción menos, dos renglones
de recepción menos y dos movimientos menos. El riesgo aproximado cambia de
26 medios/87 bajos a 25 medios/88 bajos, conservando cinco críticos y dos altos.
