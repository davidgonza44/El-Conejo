# DEMO Docker v2

Dataset exclusivamente sintético para demostración visual/funcional. No es evidencia
experimental, no se obtuvo de archivos de la empresa y no cambia los resultados
cerrados de 2025/2026. No requiere Python instalado en Windows.

## Arranque y acceso

```powershell
docker compose --env-file .env.demo up --build -d --wait
docker compose --env-file .env.demo ps -a
```

También funciona el comando solicitado, en primer plano:

```powershell
docker compose --env-file .env.demo up --build
```

URL: http://localhost:5001. Usuario de esta instalación: `admin_8b867d4b`.
La contraseña se consulta localmente en `ADMIN_PASSWORD` de `.env.demo`; no se
imprime, no se incluye en la imagen y no se restablece al reiniciar. El usuario
`demo_compras` representa al responsable que solicita compras; se crea con una
contraseña aleatoria no publicada. El administrador aprueba como actor distinto.

Configuración DEMO adicional (sin secretos):

```dotenv
DOCKER_APP_ENV=demo
DEMO_SEED_VERSION=2
DEMO_VOLUME_SUFFIX=_seed2
DOCKER_IMAGE_TAG=demo-seed2
```

El generador `docker_support.make_env demo` ya incluye estas opciones en instalaciones
nuevas. Los valores por defecto de Compose preservan la configuración del entorno
`disposable`. No cambiar `.env.docker` ni el `.env` original para usar esta demo.

## Contenido y escenarios

| Entidad | Cantidad |
|---|---:|
| Productos DEMO-001 a DEMO-050 | 50 (48 activos, 2 inactivos) |
| Categorías | 10 |
| Proveedores | 10 |
| Clientes sintéticos | 25 |
| Cliente protegido Público en general | 1 |
| Ventas / renglones | 120 / 160 |
| Movimientos | 250 |
| Órdenes / renglones de compra | 20 / 40 |
| Recepciones / renglones | 12 / 24 |
| Notas de entrega / renglones | 12 / 12 |
| Relaciones producto-proveedor | 50 |
| Lotes históricos confirmados / registros | 1 / 1.863 |
| Conciliaciones / renglones | 1 / 17 |

Semilla fija `20261003`, versión 2, fecha de referencia **3 de octubre de 2026**.
El registro operativo abarca del 5 de junio al 3 de octubre de 2026. Fechas,
productos, precios, cantidades y relaciones comerciales son reproducibles; hashes
de contraseñas, UUID de importación, tokens internos y horas de auditoría de la
carga se generan de forma segura y no se hacen predecibles.

- DEMO-001: alta rotación; tornillos para madera.
- DEMO-002: demanda estable; cinta aislante.
- DEMO-003: demanda creciente; cemento.
- DEMO-004: demanda decreciente; pintura interior.
- DEMO-005: rotación media; codo PVC.
- DEMO-006: demanda intermitente; sellador de juntas.
- DEMO-007: baja rotación; llave ajustable.
- DEMO-046: prácticamente sin movimiento, sin operaciones recientes.
- DEMO-001 a 005: cinco productos agotados, riesgo crítico operativo.
- DEMO-006 a 013: ocho productos con existencias positivas inferiores al mínimo.
- DEMO-036 a 040: cinco productos con sobrestock.
- DEMO-049 y 050: inactivos; conservan su saldo e historial.

Compras: 10 completadas, 2 parcialmente recibidas, 3 aprobadas sin recepción,
3 pendientes de aprobación, 1 rechazada y 1 cancelada. Los servicios asignan
estados, actores, cantidades recibidas y movimientos. Se respeta la prohibición
de aprobar/rechazar la propia solicitud.

Conciliación: 14 coincidencias, 2 diferencias de stock y 1 código no identificado.
La diferencia de DEMO-014 está revisada; la de DEMO-015 queda pendiente. Ninguna
resolución altera existencias automáticamente. El CSV lleva nombre DEMO.

## Contratos auditados y coherencia

La arquitectura existente se conserva. `demo_operations.py` utiliza los servicios
de categorías, productos, clientes, proveedores, relaciones con proveedores,
ventas, inventario, compras, notas, importación histórica y conciliación.

Las notas actuales no tienen FK a ventas: constituyen un canal comercial
independiente. Las doce notas son retiros distintos, con cliente sintético y
movimiento propio; no vuelven a despachar las 120 ventas. Se mantienen los
enlaces únicos a movimientos y los precios/totales calculados por cada servicio.

Cada producto comienza desde cero y obtiene un movimiento de saldo inicial.
Se calcula ese saldo a partir de las operaciones planificadas y el objetivo final,
y después se reproduce toda la secuencia cronológicamente. Nunca se asigna el
stock final mediante un UPDATE independiente. El verificador exige continuidad
en cada paso y saldo no negativo, además de comprobar:

```text
stock final = saldo inicial + recepciones + ajustes positivos
              - ventas - notas de entrega - ajustes negativos
250 movimientos = 50 iniciales + 24 recepciones + 160 ventas + 12 notas + 4 ajustes
```

La carga fija las fechas comerciales y las fechas de sus movimientos/estados
después de crear cada documento por servicios. Esta operación es exclusiva del
bootstrap de una BD DEMO nueva, antes de publicar la aplicación. No habilita
edición de fechas desde la API ni modifica importes, estados o stock directamente.

El contrato del importador admite solo 2025 y no modifica inventario. Por eso se
genera un CSV DEMO nuevo en memoria y se recorre carga → preview → dry run →
confirmación. Sus ventas históricas pertenecen al período anterior al saldo
inicial del registro operativo; no son salidas de 2026 ni se vuelven a descontar.
El lote `DEMO-VISUAL-V2` está físicamente en el volumen privado de esta demo.
No se leen ni escriben datasets ni resultados de tesis del host.

La fuente canónica de demanda combina los 1.863 registros de 2025 con las 172
líneas comerciales de 2026, sin sumar otra vez `stock_movements`. Pronósticos,
recomendaciones y precisión se calculan mediante el pipeline LIVE existente,
incluido su backtest temporal y caché dentro del volumen DEMO. No hay métricas
precargadas. Los productos sin historia suficiente conservan sus estados honestos.

Las ventanas de dashboard/tendencias usan la fecha real. Esta edición fija se
envejecerá con el calendario; reiniciar no desplaza ventas ni inventa actividad.
Para una demostración mucho más adelante, publicar otra versión y efectuar un
reset explícito. El verificador comprueba los patrones de tendencia al 3/10/2026.

## Seguridad, versionado y reset controlado

Antes de conectar se exige modo DEMO, `DOCKER_ENV_KIND=demo`, `APP_ENV=demo`,
dotenv deshabilitado, URL pública en 5001, servicio privado `db:3306`, usuario no
root, identidad válida y BD con patrón `conejo_demo_<identidad>`. También se valida
la URL efectiva del engine para rechazar una configuración Flask antigua o ajena.
`ferreteria_conejo` se rechaza antes de conectar. No se desactivan FK, checks ni triggers.

`container_installation` conserva la identidad y `demo_seed` registra versión,
seed, fecha, hash de contenido, conteos y estado `building`/`ready`. El bloqueo de
bootstrap serializa las cargas. Un segundo arranque devuelve la semilla lista sin
repetir operaciones. Una versión distinta, semilla antigua sin versión o carga
interrumpida detiene el arranque: no repara ni adopta datos silenciosamente.

En esta instalación se sustituyó expresamente la semilla anterior usando los
volúmenes nuevos del proyecto **conejo-demo-8b867d4b**:

- `conejo-demo-8b867d4b_db_data_seed2`
- `conejo-demo-8b867d4b_app_instance_seed2`
- `conejo-demo-8b867d4b_app_uploads_seed2`

Los tres volúmenes anteriores sin sufijo se conservaron. No se ejecutó `down -v`.

Para reconstruir desde cero en el futuro, decidirlo explícitamente, comprobar el
proyecto y puerto con `docker compose --env-file .env.demo ps -a`, detener solo la
demo con `docker compose --env-file .env.demo down` (sin `-v`), y cambiar únicamente
`DEMO_VOLUME_SUFFIX` de `.env.demo` a un sufijo nuevo y no utilizado, por ejemplo
`_seed2_reset01`. Luego levantar con el comando de arriba. Los volúmenes previos
siguen disponibles y no se borran. Cambiar la versión exige también actualizar
la constante/fixture y pruebas del código: la variable sola no migra datos.

## Verificación reproducible

```powershell
# Pruebas puras y barreras, sin red ni volúmenes.
docker run --rm --network none --entrypoint python conejo-portable:demo-seed2 -m unittest docker_support.test_demo -v
# Guardar huella tras comprobar todas las invariantes, HTTP, PDFs y pipeline real.
docker compose --env-file .env.demo run --rm --no-deps verify python -m docker_support.verify_demo
# Segundo arranque; después comprobar que todos los hashes siguen iguales.
docker compose --env-file .env.demo up -d --wait
docker compose --env-file .env.demo run --rm --no-deps verify python -m docker_support.verify_demo --persist --skip-pipeline
# Reinicio y prueba de persistencia.
docker compose --env-file .env.demo restart db app scheduler
docker compose --env-file .env.demo up -d --wait
docker compose --env-file .env.demo run --rm --no-deps verify python -m docker_support.verify_demo --persist --skip-pipeline
# Retirar contenedores/red y recrearlos conservando los volúmenes.
docker compose --env-file .env.demo down
docker compose --env-file .env.demo up -d --wait
docker compose --env-file .env.demo run --rm --no-deps verify python -m docker_support.verify_demo --persist --skip-pipeline
```

Detener la secuencia si un comando falla. El verificador exige el fixture limpio;
si el jurado registra operaciones, los conteos cambiarán legítimamente. No se
repara el estado para forzar una prueba aprobada. El único snapshot se guarda
en el volumen DEMO, sin publicar hashes de autenticación ni tokens.

## Resultado de aceptación del 3 de octubre de 2026

Se ejecutaron los comandos anteriores sobre `conejo-demo-8b867d4b` y
`conejo_demo_8b867d4b`. Resultado final:

- Seis pruebas de `docker_support.test_demo`: aprobadas en Docker sin red ni volúmenes.
  Incluyen rechazo previo a conexión de la BD valiosa, producción, puerto 5000,
  configuración Flask ajena, versión incorrecta y carga incompleta.
- `verify_demo`: aprobado. Conteos de todas las tablas impresos sin registros
  privados; verificadas ecuaciones por movimiento, importes, enlaces únicos,
  aprobación por actor distinto, histórico confirmado y conciliación sin ajuste.
- Pipeline LIVE: siete productos con pronóstico/recomendación; cinco evaluados
  por backtest. Selección observada: tres Random Forest, un naive y una tendencia
  lineal. Los otros dos usan estimación descriptiva. La métrica WAPE agregada que
  mostró la UI fue 99,5 %; no se manipuló para aparentar precisión alta. Estos
  resultados muestran el funcionamiento con esta serie sintética, no la validez
  del experimento de tesis.
- Login HTTP real, 17 páginas autenticadas, dashboard con 43 productos activos
  con stock y 13 alertas; reportes y API consultadas con contenido.
- Las cinco exportaciones predictivas devolvieron CSV con datos; los doce PDF de
  notas devolvieron documentos PDF válidos.
- Segundo `up`: huellas idénticas, sin duplicados; bootstrap informó
  `DEMO seed v2: already ready, no data changes.`
- `restart db app scheduler`, seguido de `up --wait` y `verify_demo --persist
  --skip-pipeline`: aprobado, mismas filas y hashes.
- `down` **sin `-v`**, `up --wait` y nueva verificación persistente: aprobado,
  mismas filas y hashes. Los volúmenes antiguos también siguen conservados.
- `node docker_support/verify_demo_ui.cjs`: doce vistas autenticadas con contenido
  DEMO y cero excepciones JavaScript. Capturas locales en `.tmp/demo-v2-ui/`
  (ignoradas por Git), viewport 1600 × 1000. Conciliación se verificó abriendo el
  detalle `CONC-000001`, donde aparecen los códigos y el archivo DEMO.
- `node --check docker_support/verify_demo_ui.cjs` y `git diff --check`: aprobados.
  Este último conserva advertencias preexistentes de conversión LF/CRLF.

La primera prueba de exportaciones alcanzó el límite de 120 segundos del
verificador genérico mientras el trabajo web seguía pendiente. El verificador
DEMO ahora espera el estado real hasta 15 minutos y reutiliza la caché persistente;
la versión final pasó. La primera revisión visual no pudo cargar el CDN de
Chart.js dentro del sandbox; al ejecutar Edge con acceso autorizado cargó bien.
No se modificó la interfaz para ocultar esos errores de comprobación.

No se conectó a `ferreteria_conejo`, no se modificó el `.env` original, no se
leyeron dumps ni datos reales y no se ejecutaron experimentos de tesis. Los
contenedores de 5000 mantuvieron su arranque previo y la imagen `conejo-portable:local`.

Archivos preexistentes modificados por esta tarea: `compose.yaml`,
`docker_support/bootstrap.py`, `docker_support/demo.py`, `docker_support/guard.py`,
`docker_support/make_env.py`, `docker_support/verify_demo.py`, `docs/DOCKER.md` y
la configuración privada `.env.demo` (solo cuatro opciones no secretas añadidas).

Archivos nuevos, clasificados como código/documentación para versionar (TRACK):
`docker_support/demo_data.py`, `docker_support/demo_operations.py`,
`docker_support/demo_invariants.py`, `docker_support/test_demo.py`,
`docker_support/verify_demo_ui.cjs` y este documento. Permanecen sin seguimiento
en Git, junto con el soporte Docker que ya estaba sin seguimiento al comenzar.
`.env.demo` y capturas se clasifican IGNORE y permanecen excluidos. Rama:
`backend-inventory-core`; cambios previos preservados, sin staging, commit ni push.
