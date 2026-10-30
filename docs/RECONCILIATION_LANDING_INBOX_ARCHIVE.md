# Separación de transporte y trabajo privado

## Diseño implementado

```text
PC ferretería: C:\InventarioExports (futuro Send Only)
  -> transporte Syncthing, todavía NO instalado/configurado
PC nuestro: D:\InventarioSync (futuro Receive Only)
  -> intake: lectura, estabilidad, SHA-256, copia verificada
  -> instance\reconciliation_inbox
  -> motor automático existente y notificación interna
  -> instance\reconciliation_archive
```

El código de intake no crea, modifica, renombra, mueve ni elimina archivos en landing.
Tampoco crea esa carpeta. Únicamente trabaja con el nombre determinista del corte
que solicita el motor; ignora los temporales y otros nombres. Los tres directorios
se resuelven y deben ser independientes, sin igualdad ni anidamiento.

Se reutiliza el transporte del bridge en `reconciliation_file_service.py`.
`reconciliation_intake_service.py` añade estabilidad y entrega al motor existente.
No cambia parser, comparación, modelos, captura Core ni reglas de stock.

## Configuración

| Clave | Default, relativo a instance/ | Propuesta futura, NO aplicada |
|---|---|---|
| RECONCILIATION_LANDING_PATH | reconciliation_landing | D:\InventarioSync |
| RECONCILIATION_INBOX_PATH | reconciliation_inbox | <directorio-del-proyecto>\instance\reconciliation_inbox |
| RECONCILIATION_ARCHIVE_PATH | reconciliation_archive | <directorio-del-proyecto>\instance\reconciliation_archive |

`RECONCILIATION_SETTLE_SECONDS=2` exige dos observaciones separadas al menos dos
segundos. Las observaciones sobreviven al reinicio en `.intake_observations` del
inbox. Con el futuro runner periódico, la segunda observación ocurre en su siguiente
invocación: no hay un nuevo proceso de vigilancia ni una espera bloqueante.
Las pruebas funcionales grandes usan cero; la prueba específica verifica el default.
No se modificó `.env` ni se aplicaron rutas/ACL de producción.

## Integridad e idempotencia

1. El productor termina y cierra `.tmp`, y publica el nombre final solamente al
   completar la exportación. Se mantienen `product_code;quantity` y los cuatro
   nombres `inventario_YYYY-MM-DD_{0800,1200,1600,1830}.csv`.
2. Intake comprueba tamaño/mtime/ctime entre observaciones. Un bloqueo, cambio o
   fallo de E/S queda pendiente para reintentar, sin CSV fallido ni corrida parcial.
3. Copia a un temporal exclusivo del inbox, limita bytes, hace flush/fsync y compara
   SHA-256 de fuente y copia. Guarda un recibo duradero privado antes de publicar.
4. Publica mediante rename sin sobrescritura en Windows. Recomprueba el archivo
   final y su recibo; el motor vuelve a contrastar el SHA de los bytes que procesará.
5. Un reinicio tras el recibo pero antes de publicar permite recopia del mismo hash.
   Un reinicio tras publicar permite procesar la copia ya verificada.
6. El archivado existente trabaja sólo con la copia privada. El landing permanece
   intacto. Un corte completado no vuelve a ingerirse aunque siga presente allí.

Contenido diferente para el mismo nombre/recibo queda bloqueado y registrado como
conflicto para revisión; nunca se sobrescribe automáticamente la evidencia. Esto
también incluye una corrección de un CSV ya ingresado: requiere resolver explícitamente
su identidad/evidencia, no borrar recibos ni aceptar silenciosamente otro hash.
Los archivos privados antiguos sin recibo conservan su contrato previo; toda entrega
creada por este intake tiene recibo antes de hacerse visible al motor.

La estabilidad temporal no prueba completitud lógica: un CSV truncado pero válido
y estable puede parecer terminado. El productor debe respetar cierre y publicación
atómica. Los hashes prueban igualdad de bytes, no autenticidad del origen. El inbox
y sus recibos deben quedar restringidos a la cuenta del runner y administradores.
No se implementó retención ni limpieza de temporales abandonados por terminación
abrupta; sus nombres no se procesan. No hay modificación de esquema.

## Permisos mínimos propuestos, sin cambios de ACL

| Carpeta | Cuenta Syncthing | Cuenta aplicación/runner |
|---|---|---|
| Landing | Leer/escribir/crear/renombrar/eliminar dentro de esa carpeta | Listar, atravesar, leer datos y atributos; sin crear/escribir/eliminar |
| Inbox privado | Ninguno necesario | Leer/escribir/crear/renombrar/eliminar sus archivos y subcarpetas |
| Archive privado | Ninguno necesario | Leer/escribir/crear/renombrar/eliminar sus archivos y subcarpetas |

Aplicar permisos por carpeta y cuenta, no Full Control al disco D:. La identidad de
Windows que ejecuta Python determina el acceso; `.venv` por sí misma no es una cuenta.
Para que la separación sea exigible por ACL se necesitan identidades adecuadas; si
Syncthing y runner usan la misma cuenta, los permisos de esa cuenta son compartidos.
Este diseño no depende de ampliar permisos fuera de las carpetas acordadas.

## Compatibilidad de transporte

Send Only en origen y Receive Only en destino encajan con este contrato. Receive Only
no hace inmutable la carpeta ante programas locales: los cambios locales pueden
quedar como cambios locales del transporte. Por eso la aplicación sólo lee landing.
La integración consume archivos, no requiere API de Syncthing.
Referencia oficial: [tipos de carpeta de Syncthing](https://docs.syncthing.net/users/foldertypes.html).
No se ha probado ni configurado una pareja real de dispositivos en esta fase.

## Validación y alcance

Las pruebas usan carpetas temporales y MariaDB privada con datadir/puerto propios,
productos sintéticos, base nueva y eliminación exclusiva de esa base al terminar.
No usan tesis ni conexión empresarial. Las notificaciones verificadas son internas;
el envío externo se bloquea explícitamente en las pruebas.

Resultados finales (2026-10-01), todos con exit code 0:

| Comando ejecutado desde la raíz | Resultado |
|---|---|
| `.\.venv\Scripts\python.exe -B scripts/test_reconciliation_intake.py` | 12 pruebas PASS |
| `.\.venv\Scripts\python.exe -B scripts/test_reconciliation_file_bridge.py` | 4 pruebas PASS |
| `.\.venv\Scripts\python.exe -B -W ignore::DeprecationWarning scripts/test_reconciliation_automatic_sqlite.py` | 12 pruebas PASS; última ejecución 73,760 s |
| `.\.venv\Scripts\python.exe -B scripts/test_reconciliation_disposable_server.py --landing-pipeline --exports-root '<directorio-del-proyecto>\tmp\landing_tests' --inbox-root '<directorio-del-proyecto>\tmp\landing_inbox_tests'` | 6 escenarios registrados PASS |
| `.\.venv\Scripts\python.exe -B tmp/verify_landing_changes.py` | AST/espacios PASS; funciones de captura sin cambios respecto al inicio de esta fase |
| `git diff --check` | PASS; avisos preexistentes de conversión LF/CRLF |

Intake: válido, repetido, temporal ignorado, archivo cambiante, bloqueo real de
Windows, interrupción y reintento, hash de copia incorrecto, inbox alterado,
conflicto de contenido, reinicio tras recibo, reinicio de proceso tras copia y
rutas solapadas. La prueba de propiedad intercepta operaciones de escritura,
eliminación y rename/replace para prohibirlas en landing. Se comprueban bytes,
mtime y ausencia de artefactos de aplicación en landing.

MariaDB: 20k y 50k completados/archivados/notificados; duplicados sin nueva corrida;
reinicio tras copia; conflicto detenido sin corrida; CSV inválido rechazado por el
motor existente. Se inyectó también un bloqueo transitorio justo al leer el inbox:
el corte permaneció waiting_source y el siguiente intento terminó correctamente.

| Filas CSV | Copia + procesamiento + archivo, última ejecución | Landing |
|---|---|---|
| 20.000 | 7,68 s | Bytes y mtime intactos |
| 50.000 | 13,19 s | Bytes y mtime intactos |

Tiempos de una prueba funcional local, con otra suite concurrente; excluyen la
captura previa y la espera de estabilidad. No son un benchmark de red Syncthing.
Invariantes PRE=POST: 50.000 productos, SUM(stock)=5.496.428,
SUM(id*stock)=137.487.549.124; cero StockMovement.
Evidencia sintética local: `tmp/synthetic_validation_20261001_065500_3afb063f/results.json`.
La base `reconciliation_test_0b1f592b3ae9` fue eliminada y el proceso MariaDB privado
se detuvo. Se conservan sólo los artefactos sintéticos de prueba.

## Archivos de esta fase

- `app/config.py`: landing y demora de estabilidad, conservando defaults privados.
- `app/services/reconciliation_file_service.py`: transporte compartido y recibos.
- `app/services/reconciliation_intake_service.py`: separación de rutas, estabilidad e integridad.
- `app/services/reconciliation_automation_service.py`: entrega desde intake, validación
  antes del parser y espera ante bloqueo transitorio; sólo cambian `_root` y `process_cut`.
- `scripts/reconciliation_file_bridge.py`: reutiliza el transporte compartido.
- `scripts/test_reconciliation_intake.py`: pruebas de archivos y reinicios.
- `scripts/test_reconciliation_landing_pipeline.py`: integración MariaDB sintética.
- `scripts/test_reconciliation_file_pipeline.py`: permite reutilizar su fixture aislado.
- `scripts/test_reconciliation_disposable_server.py`: opción explícita para la nueva suite.
- Este documento y enlace de actualización en `RECONCILIATION_SYNTHETIC_OPERATING_FLOW.md`.

Se preservaron cambios anteriores del worktree. Sin dependencias, migraciones,
nuevo motor, cambios de UI ni capturas de pantalla. La revisión comparó las funciones
de captura contra la copia tomada al inicio; no se modificaron.

## Pendientes de instalación

Del sistema original falta únicamente el SELECT real que entregue `product_code`
y `quantity`; no se inventaron identificadores ni se consultó su MySQL.
Después, con autorización: definir cuentas y ACL por carpeta, instalar/emparejar
Syncthing, configurar Send Only/Receive Only y las rutas, probar transporte real,
y autorizar por separado exportaciones y runner programado. Ninguno se activó aquí.
