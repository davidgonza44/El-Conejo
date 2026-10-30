# Ejecución portable con Docker

Python 3.12.11, Gunicorn 23.0.0 y MariaDB 10.11.14. Las imágenes base están fijadas por versión y digest; `requirements-docker.lock` fija las dependencias resueltas en Python 3.12/Linux, sin usar ningún venv local. Se necesita Docker con contenedores Linux y Docker Compose v2; no se necesita instalar Python, MySQL, Node ni XAMPP en la PC destino.

## Motor y alcance

`scripts/migrate_reconciliation_cuts.py` y `reconciliation_automation_service.py` exigen MariaDB. La captura usa InnoDB, `GET_LOCK`, `@@tx_isolation`, `REPEATABLE READ` y `START TRANSACTION WITH CONSISTENT SNAPSHOT`. Se conserva ese contrato; no se ha adaptado lógica empresarial para MySQL 8.4. El binario local auditado informó 10.4.32-MariaDB; no se consultó el servidor ni la base valiosa.

El contenedor usa `mariadb:10.11.14@sha256:dbe56e20372fc6d6b8e0e396866ba89c4c7f128c38c4f59aaa54d957db95790c`. El driver `mysql+pymysql` de SQLAlchemy detecta MariaDB. Se mantienen utf8mb4, InnoDB, constraints, triggers y servicios existentes. Referencia de la imagen: https://hub.docker.com/_/mariadb.

Esta entrega admite exclusivamente bases nuevas `conejo_disposable_*` y `conejo_demo_*`. Rechaza `ferreteria_conejo`, servidores externos y adopción de bases preexistentes no gestionadas. Tras la validación desechable, cualquier traslado de información real necesita un trabajo separado y autorizado; esta entrega no lo ejecuta.

## Arranque en esta PC

Los archivos privados `.env.docker` y `.env.demo` ya se generaron con identidades y contraseñas independientes. No se debe copiar `.env.example` encima del `.env` existente.

Windows, PowerShell:

```powershell
Set-Location -LiteralPath '<directorio-del-proyecto>'
docker compose --env-file .env.docker up --build
```

Linux y macOS, desde la carpeta donde se haya copiado el código:

```sh
cd '/ruta/al/TESIS CODIGO'
docker compose --env-file .env.docker up --build
```

Aplicación desechable: http://localhost:5000/login. La raíz `/` conserva su respuesta informativa JSON. Para ejecutarla en segundo plano y esperar salud:

```sh
docker compose --env-file .env.docker up --build -d --wait
docker compose --env-file .env.docker ps -a
```

También se puede usar literalmente `docker compose up --build` seleccionando antes el archivo de entorno en la sesión. [Docker documenta COMPOSE_ENV_FILES](https://docs.docker.com/compose/how-tos/environment-variables/envvars/#compose_env_files).

```powershell
$env:COMPOSE_ENV_FILES = '.env.docker'
docker compose up --build
```

```sh
export COMPOSE_ENV_FILES=.env.docker
docker compose up --build
```

No establecer esa variable en el `.env` original. Los ejemplos siguientes usan `--env-file` explícito para evitar ambigüedad. Variables exportadas en la terminal pueden prevalecer sobre los archivos; usar una terminal sin sobrescrituras DB_* de otro proyecto.

## Primera instalación en otra PC, sin Python local

Copiar el código y los archivos de Docker. No trasladar `.env`, archivos privados, venv, `.venv`, venv312, dumps ni los directorios de datos del equipo original. Construir primero sin credenciales:

```sh
docker build -t conejo-portable:local .
```

El siguiente generador corre sin red. Para Windows PowerShell, ejecutar solo si `.env.docker` aún no existe:

```powershell
if (Test-Path -LiteralPath '.env.docker') { throw '.env.docker ya existe; conservarlo.' }
docker create --name conejo-envgen-disposable --network none --workdir /tmp -e PYTHONPATH=/app conejo-portable:local python -m docker_support.make_env disposable
docker start -a conejo-envgen-disposable
docker cp conejo-envgen-disposable:/tmp/.env.docker .env.docker
docker rm conejo-envgen-disposable
docker compose --env-file .env.docker up --build -d --wait
```

Para Linux o macOS:

```sh
set -e
test ! -e .env.docker
docker create --name conejo-envgen-disposable --network none --workdir /tmp -e PYTHONPATH=/app conejo-portable:local python -m docker_support.make_env disposable
docker start -a conejo-envgen-disposable
docker cp conejo-envgen-disposable:/tmp/.env.docker .env.docker
chmod 600 .env.docker
docker rm conejo-envgen-disposable
docker compose --env-file .env.docker up --build -d --wait
```

Detener la secuencia si cualquier comando falla; no reemplazar archivos existentes. Si ya hay Python disponible, el equivalente opcional es `python docker_support/make_env.py disposable`; abre el archivo con creación exclusiva y nunca lee el `.env` original.

## Demo separada

En esta PC ya existe `.env.demo`. En otra PC, repetir la generación anterior sustituyendo `disposable` por `demo`, `conejo-envgen-disposable` por `conejo-envgen-demo` y `.env.docker` por `.env.demo` en todos los comandos.

```sh
docker compose --env-file .env.demo up --build -d --wait
docker compose --env-file .env.demo ps -a
```

Demo: http://localhost:5001/login. Tiene proyecto Compose, red, base, usuario administrador, contraseñas y tres volúmenes propios. La semilla v2 contiene 50 productos, 120 ventas, 250 movimientos, compras, recepciones y un histórico exclusivamente sintético. Los servicios calculan stock, importes y pronósticos. El versionado impide duplicar o migrar silenciosamente la semilla. Configuración, escenarios, pruebas y reset conservando volúmenes: [DOCKER_DEMO.md](DOCKER_DEMO.md). Una demo antigua requiere ese reset explícito antes de usar v2.

## Configuración y credenciales

| Variable | Uso |
| --- | --- |
| COMPOSE_PROJECT_NAME | Aísla nombres de contenedores, red y volúmenes. |
| DOCKER_ENV_KIND | `disposable` o `demo`. |
| ENVIRONMENT_ID | Identidad aleatoria, validada contra la marca del volumen. |
| APP_PORT | 5000 para pruebas; 5001 para demo. |
| DB_HOST / DB_PORT | Compose fija `db:3306`. |
| DB_NAME | Nombre nuevo con prefijo correspondiente al tipo de entorno. |
| DB_USER / DB_PASSWORD | Usuario limitado a la base del contenedor; nunca root. |
| MARIADB_ROOT_PASSWORD | Contraseña diferente, disponible solo en el servicio db. |
| SECRET_KEY / SECRETS_ENCRYPTION_KEY | Claves aleatorias de sesión y cifrado. |
| ADMIN_USERNAME / ADMIN_EMAIL / ADMIN_PASSWORD | Solo bootstrap y comprobaciones explícitas; no llegan al servicio app. |
| DEMO_MODE | Solo `true` en la demo independiente. |

Consultar las credenciales iniciales en el archivo privado correspondiente. El generador no imprime contraseñas, se excluyen de Git y del contexto de build. Proteger su acceso local y conservarlo junto con los volúmenes: perder las claves de cifrado o cambiar la identidad impide reutilizarlos correctamente.

El administrador se crea con el servicio de autenticación y su hash habitual. No existen credenciales predeterminadas. Un segundo init no restablece contraseñas, privilegios ni cuentas eliminadas. Cambiar ADMIN_PASSWORD en el archivo después del bootstrap no cambia la cuenta existente; usar la administración de usuarios de la aplicación. Lo mismo aplica a las contraseñas del motor en un volumen ya inicializado: las variables iniciales de MariaDB no son una herramienta de rotación.

## Arranque e inicialización

```text
db: MariaDB + volumen + SELECT 1 autenticado
  └─ service_healthy → init: guardas → bloqueo → Alembic → verificar → RBAC/admin/demo
                            └─ service_completed_successfully → app + scheduler
```

Estas condiciones son las definidas por [Docker Compose](https://docs.docker.com/compose/how-tos/startup-order/). Además, los procesos vuelven a comprobar conexión, motor e identidad antes de servir solicitudes. Gunicorn ejecuta `app:create_app()` con UID/GID 10001, un worker y cuatro threads. No usa `run.py`, debug ni rutas locales de Python.

Alembic ejecuta `0001_current_schema` (46 tablas de modelos, índices, constraints y dos triggers de histórico) y `0002_system_customer` (cliente de sistema Público en general). El DDL es una instantánea versionada del código auditado; no se genera dinámicamente al arrancar. Las transformaciones de datos antiguos de scripts legacy no se ejecutan sobre una instalación nueva. `scripts/init_db.py` tampoco se ejecuta.

Se validan las columnas de los 46 modelos y se reutilizan los verificadores originales de `migrate_reconciliation_cuts.py` y `migrate_historical_imports.py`. Una tabla de instalación conserva identidad, estado e identificador del primer administrador. El bloqueo MariaDB serializa inicializadores. DDL idempotente y marcas persistentes permiten repetir el arranque; un fallo bloquea app y scheduler.

Para futuras modificaciones, agregar una nueva revisión Alembic explícita, revisar su SQL y actualizar la revisión esperada del bootstrap. No reescribir revisiones aplicadas ni ejecutar `freeze_schema.py` para reemplazar la historia de una instalación. No se admite downgrade destructivo automático. Esto no certifica ni adopta un esquema real anterior.

## Archivos, respaldos y dependencias externas

`db_data` conserva MariaDB; `app_instance` conserva configuración, histórico, conciliación, respaldos y estado del scheduler; `app_uploads` conserva imágenes. No hay bind mounts hacia carpetas locales. MySQL/MariaDB no publica un puerto al host y la web escucha solo en 127.0.0.1.

El servicio scheduler sustituye schtasks dentro de Linux para los respaldos: frecuencia diaria, lunes semanal o día 1 mensual, horario de Caracas. Utiliza el mismo servicio de respaldo y retención. Requiere guardar y registrar la programación en Configuración; guarda el turno ejecutado y toma un bloqueo de proceso para impedir duplicados. No recupera en ráfaga turnos perdidos ni repite automáticamente un turno interrumpido. El cliente `mariadb-dump` está en la imagen y se probó un respaldo real de la base desechable.

La publicación de archivos de conciliación mantiene el contrato de no sobrescribir: rename en Windows y enlace atómico en Linux, seguido de eliminación del temporal. Temporal y destino deben estar en la misma carpeta y el sistema de archivos debe soportar enlaces duros. Los volúmenes Linux probados cumplen esa condición.

El productor externo de CSV y la programación operativa de cortes de conciliación no se conectan ni habilitan automáticamente en la demo. La captura MariaDB y la conciliación manual sí se verifican. Correo, SMS y WhatsApp están deshabilitados; notificaciones en dry-run. Google/Drive, OAuth y proveedores externos requieren sus propias credenciales y una configuración posterior. La interfaz conserva recursos CDN existentes y puede necesitar Internet para ellos; la construcción necesita acceso a los registros de imágenes, apt y PyPI.

## Operación y pruebas

Los siguientes comandos son iguales en PowerShell, bash y zsh:

```sh
docker compose --env-file .env.docker ps -a
docker compose --env-file .env.docker logs --tail=80 init app scheduler
docker compose --env-file .env.docker restart db app scheduler
docker compose --env-file .env.docker up -d --wait
docker compose --env-file .env.docker stop
docker compose --env-file .env.docker start
docker compose --env-file .env.docker down
docker compose --env-file .env.docker up -d --wait
```

`down` conserva los volúmenes. No añadir `--volumes` ni `-v` si se quiere conservar información. No cambiar COMPOSE_PROJECT_NAME ni ENVIRONMENT_ID para reiniciar una instalación existente.

Pruebas que crean exclusivamente datos sintéticos en el entorno desechable:

```sh
docker run --rm --network none conejo-portable:local python -m docker_support.test_portability
docker compose --env-file .env.docker run --rm --no-deps verify python -m docker_support.verify_scheduler
docker compose --env-file .env.docker run --rm --no-deps verify
docker compose --env-file .env.docker restart db app scheduler
docker compose --env-file .env.docker up -d --wait
docker compose --env-file .env.docker run --rm --no-deps verify python -m docker_support.verify --persist
docker compose --env-file .env.demo run --rm --no-deps verify python -m docker_support.verify_demo
docker compose --env-file .env.demo up -d --wait
docker compose --env-file .env.demo run --rm --no-deps verify python -m docker_support.verify_demo --persist
```

Ejecutar la prueba de scheduler antes de guardar la huella de aceptación: cambia y luego desactiva la programación sintética. `verify` agrega una nueva operación sintética por ejecución; `verify --persist` solo verifica el estado guardado y vuelve a autenticar. No usarla como generador de datos reales. El informe `DOCKER_VALIDACION.md` documenta qué se ejecutó y sus límites.
