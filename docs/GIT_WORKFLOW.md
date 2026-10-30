# Flujo de trabajo Git — detalle operativo

Las reglas obligatorias están en `AGENTS.md`, sección `## Git workflow obligatorio`. Este documento las desarrolla con ejemplos y casos del repositorio. No las duplica: si hay contradicción, manda `AGENTS.md`.

## 1. Cómo clasificar TRACK / IGNORE / REVIEW / DELETE

| Clase | Qué entra | Ejemplos en este repo |
|---|---|---|
| TRACK | Código, tests, scripts, templates, documentación fuente | `app/**`, `scripts/*.py|*.cjs|*.ps1`, `docs/*.md`, `docs/tesis/*.md`, `docs/tesis/borradores/*.md`, `.cursor/rules/*.mdc` |
| IGNORE | Regenerable, caché, temporal, desechable | `.tmp-experimental-tests/*.sqlite`, `~$*.docx` (locks de Office), JSON/resultados regenerables, `docs/tesis/.anexo_n_revision/` |
| REVIEW | Binario, grande, con datos personales o de terceros | `REFERENCIAS-TESIS/` completa, `docs/tesis/*.docx|*.pdf`, snapshots `PRE_*`/`RESPALDO_*`, dumps, datasets, imágenes de evidencia |
| DELETE | Temporal inequívoco y solo con autorización | Lock files huérfanos, temporales cuya regeneración está verificada |

Regla práctica: ante la duda entre TRACK e IGNORE, TRACK (lo visible se revisa); ante la duda entre IGNORE y REVIEW, REVIEW (lo dudoso no se borra ni se versiona en silencio).

## 2. Cómo stagear solo archivos untracked concretos

Nunca por directorio completo si mezcla clases. Por familias temáticas y rutas explícitas:

```powershell
git add app/controllers app/models app/routes
git add scripts/migrate_*.py scripts/prepare_*.py
git add docs/tesis/borradores
git add docs/tesis/PLAN_MAESTRO_TOMO_FINAL.md docs/tesis/AUDITORIA_FINAL_TOMO_2026.md
```

Después de cada tanda, verificar qué quedó staged antes de seguir:

```powershell
git status --short
git diff --cached --stat
git diff --cached
```

Si un archivo ya tracked trae cambios ajenos a la tarea, no stagearlo: se deja modificado en el working tree y se reporta como preexistente.

## 3. Cómo hacer commits temáticos

Un commit = un work unit coherente (p. ej. "controladores y rutas de compras", no "avances varios"). Secuencia: verificar diff staged, correr pruebas relevantes y `git diff --check`, mensaje en español sencillo que diga qué cambió (ver ejemplos en `AGENTS.md`, regla 5). Sin `--amend` salvo instrucción explícita. Si el prompt dice "no commit", no se crea aunque el work unit esté listo.

## 4. Qué hacer con un working tree sucio

Asumir que todo cambio no hecho por la tarea actual es trabajo previo ajeno. No inspeccionar su contenido más allá de `git status`/`git diff --stat` para delimitar. Trabajar solo sobre archivos de la tarea; si un archivo necesario ya está modificado por otro trabajo, reportarlo y no sobrescribirlo. Si los cambios propios no se pueden aislar de los ajenos, no hacer commit y reportarlo.

## 5. Cómo manejar DOCX/PDF y snapshots de tesis

Nunca `git add` de un `.docx`/`.pdf` sin decisión manual explícita: contienen datos personales (CI, nombres, empresa) y pesan ~6–20 MB cada uno. Versionar el Markdown fuente (`docs/tesis/*.md`, `borradores/`), los scripts que generan el documento (`tomo_final/_maquetar_tomo.py`) y las figuras fuente. Los snapshots `PRE_*`/`RESPALDO_*`/`ACTUALIZADO` son historial de trabajo, no entregables: conservar fuera de Git el mínimo necesario y decidir manualmente qué copia final, si alguna, se versiona.

## 6. Cómo manejar datasets y bases SQLite

Las SQLite de suites (`scripts/test_*.py` desechables, `.tmp-experimental-tests/`) son artefactos de prueba: van a `.gitignore`, nunca a Git. Los dumps y respaldos con datos reales (`instance/backups/`, `/backups/`, ya ignorados) nunca se versionan ni se inspeccionan más allá de su nombre. Un dataset solo entra a Git como REVIEW y con autorización, prefiriendo muestra mínima o script que lo regenera.

## 7. Cómo manejar REFERENCIAS-TESIS

Son 55+ archivos de terceros (manuales, tesis de referencia, antecedentes) y material propio del Tomo I: grandes, con derecho de autor ajeno y, en el caso del Tomo I, datos personales. Política: uso local como fuente de consulta, nunca `git add` masivo. Para estilo se leen los extractos `REFERENCIAS-TESIS/_texto/`; el Word/PDF solo se abre para márgenes, numeración o confirmar una página. No reempaquetar ni redistribuir su contenido.

## 8. Cuándo usar .gitignore vs .git/info/exclude

`.gitignore` (versionado): artefacto regenerable que afecta a todo el equipo con regla específica y estrecha (p. ej. `.tmp-experimental-tests/`, `~$*.docx`, un JSON regenerable concreto). `.git/info/exclude` (local, no versionado): exclusiones de una sola máquina (cachés de herramientas personales, temporales de un experimento). Nunca reglas amplias (`*.md`, `docs/*`, `*.py`) que puedan ocultar código real.

## 9. Cómo recuperar una tarea interrumpida

Leer `git status --short --branch`, `git diff --stat` y `git diff --cached --stat` para reconstruir: qué estaba staged (tarea en curso), qué está modificado sin stagear (trabajo previo o posterior) y qué sigue `??` (pendiente de clasificar). Reanudar por el work unit más pequeño pendiente, no por el más antiguo. No usar `git stash` global para "limpiar": si hace falta apartar algo, `git stash push -- <rutas concretas>` y registrarlo en el reporte.

## 10. Flujo recomendado para tareas largas

```text
work unit → tests → diff → staging selectivo → commit local → siguiente work unit
```

Cada vuelta del ciclo termina con el reporte de finalización de `AGENTS.md` (regla 10) a escala del work unit: qué entró al commit, qué sigue `??`, qué preexistente se preservó. Así el working tree nunca vuelve a acumular cientos de `??` sin clasificar.

## 11. Comandos seguros de diagnóstico

```powershell
git status --short --branch
git diff --stat
git diff --cached --stat
git diff --cached
git ls-files --others --exclude-standard
git log --oneline -10
git diff --check
```

Todos son lectura; ninguno modifica el working tree.

## 12. Comandos destructivos prohibidos salvo autorización explícita

`git reset --hard`, `git restore .`, `git checkout -- .`, `git clean -fd`, `git add .`, `git add -A`, `git push`, reescritura de historial (`rebase`, `--amend` ajeno), publicar ramas o tocar remotos. `git clean` (en cualquier forma) sobre `REFERENCIAS-TESIS/`, `docs/tesis/` o documentación está prohibido: destruiría trabajo irrecuperable. Cada excepción requiere autorización explícita y queda registrada en el reporte.

## Nota del estado actual (2026-10-02)

El repositorio tiene ~504 archivos untracked históricos: ~380 TRACK (~5 MB de código, scripts, templates y Markdown), ~8 IGNORE (~2,7 MB de SQLite desechables y artefactos regenerables) y ~116 REVIEW (~321 MB de `REFERENCIAS-TESIS` y DOCX/PDF de tesis). Su regularización es una tarea separada y posterior: por familias temáticas en commits pequeños (nunca los 380 TRACK en un solo commit), IGNORE vía `.gitignore`, y REVIEW permanece fuera de Git hasta decisión manual. Esta política rige el trabajo de ahora en adelante; no regulariza el pasado por sí sola.
