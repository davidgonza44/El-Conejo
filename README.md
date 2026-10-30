# Sistema inteligente de apoyo al control de inventario

Aplicación web para apoyar el control de inventario de **Ferretería y
Construcciones El Conejo C.A.** El sistema actual ya incluye una interfaz
Jinja2, operaciones de inventario, notas de entrega, importación histórica y
diagnóstico de preparación de datos; no genera todavía pronósticos ni planes de
reabastecimiento.



## Puesta en marcha

Requisitos: Python 3.10 o superior y MySQL 8 accesible. Node.js solo es
necesario para las herramientas de análisis del repositorio.

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Complete en `.env` las variables locales de MySQL y de las integraciones que
vaya a habilitar. Nunca use credenciales reales en `.env.example`.

En una base de datos de desarrollo desechable:

```powershell
python scripts\init_db.py
python run.py
```

`scripts/init_db.py` y los scripts de migración, limpieza o pruebas HTTP pueden
modificar datos. Revise su destino antes de ejecutarlos.

## Estado y arquitectura

- Backend: Flask con application factory, blueprints, Flask-Login,
  Flask-WTF, SQLAlchemy, PyMySQL y MySQL 8.
- Frontend: Jinja2 renderizado en el servidor, Tabler/Bootstrap, CSS,
  JavaScript nativo y Chart.js.
- Integraciones opcionales verificadas: Google OpenID Connect y correo SMTP.
- Módulos existentes: autenticación, usuarios y permisos, productos,
  categorías, movimientos, notas de entrega, reportes, importaciones
  históricas, chatbot y preparación de datos predictivos.

El flujo preferido conserva la separación existente:

```text
Flask route -> controller -> service -> SQLAlchemy model -> MySQL
```

Algunas rutas de página renderizan plantillas directamente y el dashboard
conserva consultas existentes en su capa de rutas. No se requiere una
reescritura para uniformarlas.

- [Diagrama interactivo](docs/architecture/system-overview.html)
- [Fuente verificable del diagrama](docs/architecture/system-overview.json)

## Referencias visuales

`references/` contiene la dirección visual aprobada. Antes de modificar una
pantalla, lea `references/README.md` y `references/MANIFEST.csv` y use solo las
imágenes relacionadas con ella.

> **Prohibido:** no leer, copiar, empaquetar ni usar
> `references/90_archivo_no_usar/`.

El nombre de marca válido es **Ferretería y Construcciones El Conejo C.A.** y
el logo fuente está en `references/00_marca/logo_oficial_el_conejo.png`.

## Herramientas de contexto y análisis

Instale primero las dependencias Node declaradas por el repositorio cuando
trabaje en una copia nueva:

```powershell
npm ci
```

| Herramienta | Uso y estado en este repositorio | Comando principal | Salida local |
| --- | --- | --- | --- |
| AGENTS / Cursor | `AGENTS.md` es la única fuente de reglas; Cursor solo remite a ella. | Leer `AGENTS.md` antes de trabajar | Ninguna |
| OpenSpec 1.10 | Requisitos y criterios aprobados, sin duplicar reglas de agentes. | `npm run openspec:doctor` | `openspec/` versionado |
| CodeGraph 2.3.10 | Orientación, impacto y revisión estructural. Doctor y orientación funcionan con la dependencia local. | `npm run codegraph:orient` | `.codegraph/`, ignorada |
| Repomix 1.18 | Paquete XML comprimido para contexto controlado. | `npm run repomix:pack` | `repomix-output.xml`, ignorado |
| Archify 2.17 | Diagrama de arquitectura basado en evidencia del repositorio. Se usa el skill ya instalado; no hay dependencia nueva. | Ver comandos siguientes | JSON y HTML bajo `docs/architecture/` |
| Gentle AI 2.5.0 | Binario fijado en el Cursor Cloud Build. No se instala el preset ni se usa como fuente de reglas. | `gentle-ai doctor` | Ninguna en esta fase |
| Engram 1.20.0 | Binario local fijado en el Cursor Cloud Build. MCP perfil `agent`; sin Engram Cloud/sync. | `engram version` | `~/.engram` fuera del repo |
| Pi 0.84.4 | Binario fijado (`@earendil-works/pi-coding-agent`). Fase 1: solo infraestructura read-only; sin clave ni llamada LLM. | `.cursor/pi-review.sh --check` | `/.pi/` ignorado; `~/.pi` fuera del repo |
| Ponytail | Plugin ya disponible para contener sobreingeniería y aplicar KISS/YAGNI. | Se activa desde el asistente | Sin scaffolding |
| RTK | Pendiente de identificar el producto exacto. No se asume Redux Toolkit. | No aplica | Ninguna |

### CodeGraph

```powershell
npm run codegraph:doctor
npm run codegraph:orient
npm run codegraph:review
```

`codegraph:orient` construye o actualiza su índice local bajo `.codegraph/`;
ese directorio nunca debe versionarse. No hace falta un paso `init` adicional
para los comandos actuales.

### Repomix

```powershell
npm run repomix:pack
git check-ignore repomix-output.xml
```

`.repomixignore` excluye credenciales, dependencias, índices, entornos Python,
datos subidos, instancias, importaciones privadas, temporales de prueba, la
carpeta de referencias prohibida y salidas generadas. El HTML autocontenido de
Archify también se excluye porque su runtime embebido no aporta contexto de
código; la fuente JSON sí puede incluirse.

El paquete es local y potencialmente sensible aunque el escaneo automático no
encuentre secretos. Revíselo antes de compartirlo y no publique su contenido en
logs, incidencias o prompts externos.

### Archify

El diagrama actual tiene tipo `architecture`, perfil `showcase`, 11 nodos y
fuentes fijadas a una revisión Git. Para regenerarlo con el skill instalado:

```powershell
$archify = Join-Path $HOME ".codex\skills\archify"
node "$archify\bin\archify.mjs" validate architecture `
  docs\architecture\system-overview.json --quality showcase `
  --repo-root . --json
node "$archify\bin\archify.mjs" deliver architecture `
  docs\architecture\system-overview.json `
  docs\architecture\system-overview.html --quality showcase `
  --repo-root . --json
node "$archify\bin\archify.mjs" visual-check `
  docs\architecture\system-overview.html --json
```

`visual-check` genera capturas y sidecars locales de comprobación. Inspecciónelos
y elimínelos después; solo el JSON fuente y el HTML final pertenecen al
repositorio.

### Cursor Cloud: Gentle AI, Engram y Pi

El Cloud Build instala binarios exactos en `/usr/local/bin`:

- Gentle AI `v2.5.0` (release oficial `Gentleman-Programming/gentle-ai`)
- Engram `v1.20.0` (canal estable `v*`, no `pi-v*` ni prereleases `v2.0.0-rc.*`)
- Pi `0.84.4` (paquete npm `@earendil-works/pi-coding-agent@0.84.4`, `--ignore-scripts`)

Engram queda en modo local. No se configuran `ENGRAM_CLOUD_*`, autosync ni memoria precargada en el snapshot. El MCP de Cloud Agents debe registrarse en Cursor Cloud (no en el repositorio) como stdio: `engram mcp --tools=agent`. No ejecutar `engram setup cursor` ni versionar `~/.cursor/mcp.json`.

Pi en Fase 1 es solo infraestructura de instalación y un wrapper read-only. La verificación no requiere autenticación ni realiza llamadas LLM. La autenticación y cualquier revisión con modelo se evaluarán aparte. Pi no está integrado con Gentle AI: no se instalan `gentle-pi` ni companions, no se configura MCP/subagents y no se ejecuta `gentle-ai install --agent pi`. El único punto de entrada aprobado es `.cursor/pi-review.sh`; en esta fase use `--check`.

Rollback de Pi (no se ejecuta automáticamente; solo al revertir esta instalación Cloud). Use el Node y el npm-cli.js del prefijo fijado; no dependa de `#!/usr/bin/env node` ni del `npm` de PATH:

```powershell
sudo /usr/local/lib/nodejs/node-v22.23.2/bin/node `
  /usr/local/lib/nodejs/node-v22.23.2/lib/node_modules/npm/bin/npm-cli.js `
  uninstall -g `
  --prefix /usr/local/lib/nodejs/node-v22.23.2 `
  @earendil-works/pi-coding-agent
sudo rm -f /usr/local/bin/pi
sudo rm -f /usr/local/cargo/bin/pi
```

Esos dos enlaces los creó a mano el instalador Cloud (`ln -sfn`) y `npm uninstall` no garantiza quitarlos. Elimine `~/.pi` solo si existe y es seguro borrarlo. Revierta los cambios de Fase 1 del repositorio y reconstruya el entorno Cloud.

### Gentle AI, Ponytail y RTK

El preset se evaluó sin instalarlo:

```powershell
gentle-ai --version
gentle-ai doctor
gentle-ai install --help
gentle-ai install --preset full-gentleman --scope workspace --dry-run
```

El dry-run propuso los agentes `cursor`, `vscode-copilot` y `codex`, y estos
componentes en orden: `claude-theme`, `context7`, `persona`, `engram`, `gga`,
`opencode-gentle-logo`, `permissions`, `sdd` y `skills`. El comando informó 2
pasos de preparación y 12 de aplicación, pero no mostró paths de destino; no se
inventan rutas ausentes del resultado.

No se conserva ningún elemento del preset en esta fase: `AGENTS.md`, Cursor,
OpenSpec, Engram y los skills ya cubren las necesidades aprobadas, y aplicar el
preset duplicaría o podría sobrescribir configuración existente. El doctor
actual informa estado `unhealthy` por `gga` ausente, dos binarios Engram en
`PATH` y un handshake MCP persistido fallido; corregir herramientas globales
queda fuera del alcance del repositorio.

Ponytail ya aporta el control KISS/YAGNI como plugin, por lo que no se agrega
otra dependencia ni scaffolding. RTK seguirá detenido hasta recibir el nombre o
enlace del producto exacto.

## Seguridad de las salidas

- No incluya `.env`, `instance/`, `uploads/`, `private_imports/`, datos de
  usuarios, credenciales, dumps ni reportes privados en análisis o commits.
- `.codegraph/`, `.atl/` y `repomix-output.*` son estado local ignorado.
- No versione capturas temporales, sidecars de validación ni cachés Python.
- Revise `git status`, el diff completo y `git diff --check` antes de entregar.

## Verificación

Comprobaciones seguras de documentación y herramientas:

```powershell
npm run codegraph:doctor
npm run openspec:doctor
npm run openspec:validate
git diff --check
```

Suites que crean sus propios datos SQLite temporales:

```powershell
python scripts/test_delivery_note_pdf.py
python scripts/test_historical_imports.py
python scripts/test_prediction_readiness.py
```

Trate cualquier otro script bajo `scripts/` como mutante hasta inspeccionar su
código. Las reglas completas de seguridad, arquitectura y validación están en
[`AGENTS.md`](AGENTS.md).
#   E l - C o n e j o  
 