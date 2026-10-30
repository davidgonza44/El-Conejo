# Repository Agent Guide

This file is the single source of truth for assistants working in this repository. Read it before changing or generating project files. Keep tool-specific rules short and link back here instead of copying these instructions.

## Product and stack

- Product name: **Ferretería y Construcciones El Conejo C.A.**
- Backend: Python 3.10+, Flask application factory (`create_app`), Flask-Login, Flask-WTF, SQLAlchemy, PyMySQL, and MySQL 8.
- Frontend: server-rendered Jinja2, native JavaScript, Chart.js, Tailwind CSS v4 for newly implemented reference-driven UI, and Lucide as the preferred icon system.
- Existing Tabler/Bootstrap markup may remain temporarily during the screen-by-screen migration. Do not remove or rewrite unrelated legacy UI solely to complete the migration.
- Do not introduce React, Next.js, Vue, or another SPA framework solely to use a component library.
- Text presented to users is Spanish and encoded as UTF-8.

## Trabajo de grado

When writing or revising the trabajo de grado — Tomo I, tomo final, capítulos, resumen, referencias, anexos, or academic prose in `docs/tesis/` and `REFERENCIAS-TESIS/00-mi-tesis/` — read the three sources below and follow them. These rules govern academic text only. They do not change the application architecture, routes, or UI rules in the rest of this guide.

Apply them in this order when they disagree. Do not invent a third rule to reconcile them:

1. `REFERENCIAS-TESIS/01-normas-ujap/tutor-tesis.txt` — lineamientos del tutor David y correcciones del Ing. José Miranda (mayo 2026) para esta tesis.
2. `REFERENCIAS-TESIS/01-normas-ujap/MANUAL DE NORMAS TRABAJO DE GRADO VERSIÓN FINAL (1).pdf` — Manual UJAP, Valencia, marzo 2020.
3. `REFERENCIAS-TESIS/01-normas-ujap/MANUAL DE TRABAJOS DE GRADO UPEL 5ta EDICION 2016 (1).pdf` — Manual UPEL, 5.ª edición, 2016.

Excepción — formato del `.docx`: en papel, márgenes, estilos Word, numeración, encabezados/pies, cuadros/tablas y compatibilidad mandan las propiedades de Angelo en `### Maquetación .docx`, por encima de lo que digan tutor, UJAP o UPEL sobre esos mismos puntos de formato. El resto académico sigue el orden 1–3.

The UJAP manual adopts the UPEL manual (2016) and APA 6.ª edición for degrees outside the health sciences. Use UPEL for what UJAP does not specify. Do not upgrade citations to APA 7. Ignore the commercial watermark in the UPEL file. Paths inside `tutor-tesis.txt` that point to `references/plantilla-estructura-tomo-i.md` or `references/tg-*.md` are not in this repository; do not follow them. Notes under `REFERENCIAS-TESIS/01-normas-ujap/capitulos/` do not override the three sources.

La voz del texto se mezcla de las cuatro tesis de referencia de 2025 indicadas en «Voz y estilo». Esas tesis enseñan ritmo y concreción. Estructura, citas, extensión y terminología siguen el orden de las tres fuentes de arriba.

Para contenido y estilo de esas cuatro tesis, lee `REFERENCIAS-TESIS/_texto/` (texto plano). `--- p. N ---` es la hoja del archivo; el número para citar es el impreso al pie de esa hoja. Los regenera `python scripts/extract_tg_texto.py`. Abre el PDF o el Word solo para márgenes, numeración, cuadros o confirmar una página. El borrador de esta tesis se edita en Markdown bajo `docs/tesis/`; el `.docx` se maqueta al final.

### Reglas de esta tesis (tutor)

- La solución no entra en la pregunta de investigación. Un problema no es la falta de una solución: es la consecuencia grave observable. Las causas explican por qué existe; van aparte.
- Planteamiento en siete párrafos, de lo general a lo particular: mundo, América Latina, Venezuela o sector, problema en la empresa con indicadores, causas, consecuencias si continúa, y solo al final el aporte. En los seis primeros párrafos no se nombra el sistema ni la tecnología propuesta. El párrafo 4 usa datos reales del diagnóstico; si el diagnóstico aún no existe, describir en cualitativo, sin porcentajes inventados.
- Toda afirmación estadística o contextual lleva autor y año. El diagnóstico propio indica quién lo hizo, con qué instrumento, a quién y qué arrojó.
- Objetivo general: un solo verbo en infinitivo. No usar «conocer» ni «comprender». Los objetivos específicos siguen Diagnosticar, Analizar o Diseñar, Desarrollar, Evaluar o Validar. «Implementar» solo si el sistema entra en producción.
- Justificación después del problema, en cuatro dimensiones: práctica, teórica, institucional y social.
- Alcance operativo: qué cubre, qué tecnología usa y qué no cubre. No repite el título ni los objetivos. Para esta tesis, las limitaciones son factores externos que no dependen del investigador. Lo que el estudio no hará va en el alcance.
- Coherencia obligatoria entre título, objetivo general, pregunta, problema y alcance. El objetivo general del resumen es idéntico al del Capítulo I.
- Terminología: «control de inventario», no «gestión de inventario», salvo al citar un antecedente o al explicar la diferencia. El sistema no es un ERP. Demanda se asocia a ventas y a la decisión de compra, no al stock. El diferenciador es movimiento de mercancía, análisis, pronóstico de demanda y recomendación de reabastecimiento. Catálogo visual, notas de entrega y chatbot son soporte.
- Antecedentes: mínimo los que exige el manual UJAP (dos locales, dos nacionales, uno internacional; vigencia de cinco años; del más reciente al más antiguo). Cada uno con objetivo, conclusión y aporte. No afirmar que no existen trabajos previos.
- Bases teóricas: desarrollo de cada base, no un glosario; mínimo dos párrafos por base. Definiciones de términos: como máximo diez, en orden alfabético.
- Operacionalización solo del primer objetivo específico. La variable es lo que se mide, no el nombre del proyecto. Indicadores de una o dos palabras, cada uno con ítem. El instrumento es un guion de entrevista de preguntas abiertas que diagnostican la situación actual, no un cuestionario ni una opinión sobre el sistema futuro.
- Fases en sustantivos alineados con los objetivos específicos. Cronograma en diagrama de Gantt. Tiempo del proyecto: dos semestres (ocho meses), salvo que el tutor fije otro.

### Voz y estilo

Mezclar el estilo de estas cuatro tesis para que la redacción suene natural, como un trabajo de grado escrito por una persona. Si una de ellas contradice al tutor, al manual UJAP o al manual UPEL, manda la norma en contenido académico. En formato `.docx` manda Angelo (ver excepción de jerarquía y `### Maquetación .docx`). Corregir la gramática y no imitar erratas, tiempos verbales mezclados ni frases copiadas.

- `REFERENCIAS-TESIS/_texto/2025-UJAP-Plasencia-Valdes-Sistema-Predictivo-Inventario-ML.txt` — la escena operativa: quién hace el trabajo, con qué herramienta y qué falla. Las cifras del diagnóstico llevan informe, cargo y año. Cada antecedente se cuenta en prosa: autores, universidad, título, qué hicieron y qué aporta a este estudio.
- `REFERENCIAS-TESIS/_texto/2025-UJAP-Odriozola-Asistencia-Web-Inteligencia-Artificial.txt` — explica el concepto antes de bajar al caso. La cita entra dentro de la frase, con página cuando es textual. Los párrafos cambian de arranque: Según, Por otra parte, En ocasiones, Por ello, De este modo, En este sentido.
- `REFERENCIAS-TESIS/_texto/2025-UJAP-Lopez-Africano-Control-Acceso-Inscripciones.txt` — el problema se ve en lo que ocurre un día normal. El dato de la entrevista o del reporte va dentro del relato. La justificación son párrafos cortos, cada uno con un ángulo distinto, sin repetir el objetivo.
- `REFERENCIAS-TESIS/_texto/TG-DAmbrosio-Rodriguez-Chatbot-2025.txt` — el embudo de lo general a lo particular se escribe como ensayo: mundo, América Latina, Venezuela, región y empresa. La empresa aparece como un lugar real antes de enumerar las fallas. La justificación fluye por dimensiones, sin titulares rígidos.

Para que no suene robótico ni repetitivo:

- Variar el largo de las oraciones y de los párrafos. Una frase corta puede seguir a una larga.
- Un párrafo desarrolla una idea; el siguiente avanza. No resume el anterior ni cierra siempre con la misma moraleja.
- Rotar los conectores. No abrir todos los párrafos con el mismo. Evitar como muletilla «cabe destacar», «es importante mencionar», «es fundamental», «a continuación se presenta» y cadenas de sinónimos (fundamental, crucial, indispensable).
- Nombrar lugar, cargo y hecho. Evitar abstracciones vacías del tipo «la optimización de los procesos estratégicos de la organización».
- Los antecedentes no usan la plantilla «Objetivo: … Conclusión: … Aporte: …» en cada ficha. Van en prosa, y al cierre de cada uno quedan claros el objetivo, la conclusión y el aporte que pide el tutor.
- Releer el tramo recién escrito y cambiar cualquier frase que repita el arranque, el verbo o la conclusión del párrafo anterior.

### Formato UJAP

- Proyecto en cuatro capítulos: I El problema (planteamiento, formulación, objetivos, justificación, alcance y limitaciones); II Marco teórico (antecedentes, bases teóricas, bases legales, definición de términos, cuadro técnico-metodológico); III Marco metodológico; IV Recursos (humanos, materiales, económicos y cronograma).
- Redacción en tercera persona, lenguaje formal y directo, sin yo, tú, nosotros, mí. El autor o la autora solo para destacar un aporte propio. Siglas expandidas la primera vez: Universidad José Antonio Páez (UJAP). Términos en latín o inglés en cursiva.
- Citas APA 6.ª: autor y año en el texto; cita textual menor de 40 palabras entre comillas, con página o párrafo; cita de más de 40 palabras en bloque, sin comillas, sangría de 2,54 cm. Las citas van a doble espacio. No usar citas de más de 500 palabras ni notas al pie para citar fuentes. Con tres o más autores, la primera cita nombra a todos y las siguientes usan «et al.» en cursiva. Toda fuente citada, excepto comunicación personal, va en las referencias, en orden alfabético.
- Transcripción: Times New Roman 12 puntos; cuadros y gráficos a 10 puntos; interlineado de espacio y medio; sin espacio extra entre párrafos; sangría de cinco espacios en la primera línea. Preliminares en romanos minúsculos; el resto en arábigos, centrados abajo. Cuadros, tablas y gráficos en el lugar del texto, numerados en serie continua. Número y título arriba en cuadros y tablas, abajo en gráficos; la fuente abajo. En el `.docx` final, lo que choque con `### Maquetación .docx` se resuelve a favor de Angelo.
- Título de hasta 19 palabras. Resumen en español, un solo párrafo, a un espacio, máximo 300 palabras, con objetivo general, soporte teórico y metodología, y de tres a cinco descriptores.
- Soportes teóricos y antecedentes con vigencia de los últimos cinco años al momento de presentar el estudio.

### UPEL, donde el manual UJAP no detalla

- Esta tesis es un Proyecto Especial: creación tangible (software o prototipo) que responde a una necesidad demostrada, con demostración de esa necesidad, fundamentación teórica, metodología y resultado concreto. La organización de los resultados responde a los objetivos; debe verse el aporte original.
- Márgenes: 4 cm a la izquierda; 3 cm a la derecha, arriba y abajo; 5 cm arriba en la primera página de cada capítulo. Para el `.docx`, esta regla queda subordinada a Angelo: aplican 2,54 cm parejos según `### Maquetación .docx`.
- Referencias con sangría francesa. Capítulos, referencias y anexos empiezan en página nueva. Encabezado de capítulo: «CAPÍTULO» y número romano, luego el título en mayúsculas.
- Decimales con coma, salvo que el programa del cuadro solo permita el punto. Anexos como Anexo A, Anexo B; partes A-1, A-2.

### Maquetación .docx — propiedades Word del trabajo de Angelo (requisito estricto)

- Regla estricta: el `.docx` del trabajo de grado debe tener las mismas propiedades Word que el trabajo de Angelo. Lo que sigue es la medición textual de ese archivo y es el objetivo de maquetación. No se copia su contenido, autores, tutor, empresa, metodología, objetivos ni resultados.
- Fuente medida: `REFERENCIAS-TESIS/02-tg-referencia/04-otras-referencias/TG-DAmbrosio-Rodriguez-Chatbot-2025.docx`. La ruta `REFERENCIAS-TESIS/02-tg-referencia/TG-Angelo-DAmbrosio-30641266-Julio-Rodriguez-31078856.docx` citada en `docs/tesis/PLAN_MAESTRO_TOMO_FINAL.md` no existe en el árbol; la única válida es la de `04-otras-referencias/`.
- Conflicto resuelto a favor de Angelo, no silenciado: lo medido trae carta con 2,54 cm parejos; el Manual UJAP/UPEL de arriba pide 4 cm izquierda, 3 cm derecha/arriba/abajo y 5 cm arriba en la primera página de cada capítulo. Para el `.docx`, manda Angelo en todo lo listado aquí, por encima de tutor, UJAP y UPEL en esos puntos de formato. Solo un cambio explícito de esta subsección puede revertirlo.
- Identidad del archivo medido (contexto, no requisito de igualar conteos): `Application=Microsoft Office Word`, `AppVersion=16.0000`, `Template=Normal`, `Company=` vacía, `TotalTime=1335`, `Pages=157`, `Words=26442`, `Characters=145436`, `CharactersWithSpaces=171535`, `Lines=1211`, `Paragraphs=343`, `DocSecurity=0`, `ScaleCrop=false`, `LinksUpToDate=false`, `SharedDoc=false`, `HyperlinksChanged=false`. `core.xml` solo trae `dc:creator=Angelo`, `revision=11`, `created=2026-06-10T02:38:00Z`, `modified=2026-06-12T06:39:00Z`; `title/subject/keywords/category/comments` vacíos. No se copian autor, `lastModifiedBy`, fechas ni revisiones.
- Página y secciones: 9 secciones, todas `Letter` 8,5 x 11 pulg (612,0 x 792,0 pt), vertical (`PORTRAIT`), `gutter=0`. `w:pgSz` carta en las 9. `start_type`: secciones 0, 1, 2, 6, 7 en `NEW_PAGE`; 3, 4, 5, 8 en `CONTINUOUS`. `different_first_page_header_footer=False` en todas salvo la sección 2 (`True`).
- Márgenes y `pgMar`: en las 9 secciones `top=bottom=left=right=2,54 cm` (`w:top=1440 w:right=1440 w:bottom=1440 w:left=1440`, 1440 twips = 2,54 cm). `w:pgMar w:header=720 w:footer=720` en la sección 0 (1,27 cm) y `w:header=709 w:footer=709` en las secciones 1–8 (1,25 cm). `header_distance` medido 1,27 cm en sección 0 y 1,25 cm en el resto; `footer_distance` igual. No hay margen de encuadernación.
- Encabezados y pies medidos: `header` vacío en las 9 secciones. `footer` con número a pie, centrado abajo: sección 0 `first_page_footer=v`; secciones 3–5 `footer=vi`; sección 6 `footer=vii`; secciones 7–8 `footer=1` con `w:pgNumType w:start=1` (reinicio a 1 al empezar el cuerpo). Preliminares en romanos minúsculos (`v`, `vi`, `vii`), cuerpo en arábigos desde 1. `even_page_header/footer` vacíos.
- `docDefaults` (`styles.xml`): `w:rPrDefault/w:rPr` con `w:rFonts w:ascii=Arial w:eastAsia=Arial w:hAnsi=Arial w:cs=Arial`, `w:sz w:val=22` (11 pt), `w:szCs w:val=22`, `w:lang w:val=es w:eastAsia=es-VE w:bidi=ar-SA`. `w:pPrDefault/w:pPr` con `w:spacing w:line=276 w:lineRule=auto` (1,15 líneas). Es el valor base; el estilo `Normal` lo sobrescribe (ver abajo).
- Estilo `Normal` (el que rige el cuerpo, `w:styleId=Normal`, `w:default=1`): `w:rFonts w:ascii=Times New Roman w:hAnsi=Times New Roman`, `w:sz w:val=24` (12 pt), `w:pPr` con `w:spacing w:before=120 w:after=120 w:line=360 w:lineRule=auto` (6 pt antes, 6 pt después, 1,5 líneas = espacio y medio) y `w:jc w:val=both` (justificado). Visto por `python-docx` como `font=Times New Roman 12,0 pt`, `align=JUSTIFY`, `space_before=6,0 pt`, `space_after=6,0 pt`, `line_spacing=1,5`.
- Otros estilos definidos (heredan de `Normal`, `next=Normal` salvo lista): `Heading 1` 20 pt, `space_before=20 pt`; `Heading 2` 16 pt, `space_before=18 pt`; `Heading 3` 14 pt `color=434343`, `space_before=16 pt`, `space_after=4 pt`; `Title` 26 pt, `space_after=3 pt`; `Subtitle` 15 pt `color=666666`, `space_after=16 pt`; `Quote` itálica `color=404040`, centrada, `space 8/8 pt`, `line 1,08`; `Intense Quote` itálica `color=365F91`, centrada, `left=right=1,524 cm`, `space 18/18 pt`; `List Paragraph` `left=1,27 cm`, `space_after=8 pt`; `Header` y `Footer` `line 1,0`, sin espacios. Lista completa de estilos en el archivo: `Normal`, `Heading 1`–`9`, `Default Paragraph Font`, `Normal Table`, `No List`, `TableNormal`, `Title`, `Subtitle`, `List Paragraph`, `Grid Table Light`, `Quote`, `Intense Emphasis`, `Intense Quote`, `Intense Reference`, `Normal (Web)`, `No Spacing`, `Header`, `Footer`, `Strong`, `Emphasis`, `Table Grid`, más `Car` asociados. `Caption` y `Footnote Text` no existen como estilos.
- Observado en el cuerpo (formato directo sobre `Normal`): el texto corrido hereda Times 12 justificado (2193 runs heredados de 2256 en los primeros 800 párrafos; 147 runs a 12 pt explícito, 129 a 10 pt en portadas/pies). Alineación: mayoría heredada (justificada), 92 párrafos centrados (portadas, títulos de capítulo, resumen/abstract). Planteamiento (Cap. I) con `space 12/12 pt` directo y primera línea con tabulador literal `"\t"`, sin `first_line_indent`. Títulos de capítulo y apartados en Times 12 negrita por formato directo aunque el estilo `Heading` defina 20/16/14 pt: `CAPÍTULO I`, `EL PROBLEMA`, `INTRODUCCIÓN`, `RESUMEN` centrados 12 pt negrita; `1.1. Planteamiento del Problema`, `1.3. Objetivos`, `1.4. Justificación` a 12 pt negrita (izquierda o centrada según apartado). `RESUMEN` con `space_before=24 pt`; cuerpo del resumen a 1,0 línea y `Descriptores:` en negrita. Tablas del cuerpo a 10 pt donde aplica.
- Cuadros y tablas: 37 tablas. Estilos `TableNormal` o sin estilo. Índice de cuadros de 34 x 3 (`CUADRO | DESCRIPCIÓN | pp.`), índice de figuras de 62 x 3 (`FIGURA | DESCRIPCIÓN | pp.`), más cuadros de contenido (p. ej. Alfa de Cronbach, estados del flujo). Número y título arriba en cuadros, fuente abajo, como ya pide UJAP.
- `settings.xml` y compatibilidad: `w:zoom 100%`, `w:embedTrueTypeFonts` (fuentes incrustadas), `w:proofState spelling=clean`, `w:defaultTabStop w:val=720` (0,5 pulg / 1,27 cm), `w:hyphenationZone w:val=425`, `w:characterSpacingControl=doNotCompress`, `footnotePr/endnotePr` por defecto. `w:compat` con `compatibilityMode=15`, `overrideTableStyleFontSizeAndJustification=1`, `enableOpenTypeFeatures=1`, `doNotFlipMirrorIndents=1`, `differentiateMultirowTableHeaders=1`, `useWord2013TrackBottomHyphenation=1`.
- Idioma y escritura: `es` / `es-VE` en `docDefaults`; cuerpo en español. Zoom 100 %. Sin orientación horizontal; todo vertical.
- Para el `.docx` nuevo: igualar papel, márgenes, distancias de encabezado/pie, `Normal` Times 12 justificado 6/6 pt 1,5 líneas, `docDefaults` Arial 11 / 1,15 solo como base, jerarquía `Heading 1`–`3`/`Title`/`Quote`/`List`/`Header`/`Footer` de arriba, pies con romanos luego arábigos reiniciando en 1, 9 secciones o las que pida la estructura con los mismos `start_type` y `different_first_page`, tablas `TableNormal`, cuerpo a 10 pt en cuadros, `defaultTabStop` 0,5 pulg, fuentes incrustadas y `compatibilityMode=15`. Verificar abriendo el Word solo para márgenes, numeración, cuadros o confirmar una página, como ya dice esta guía.

### Límites de extensión que no coinciden

No mezclar estos topes en silencio. Decir cuál fuente se está aplicando:

- `tutor-tesis.txt`: Tomo I entre 30 y 50 páginas, con mínimos y máximos por capítulo en ese archivo.
- Manual UJAP: el proyecto tiene como mínimo 20 páginas y no pasa de 35, preliminares y referencias incluidas. El planteamiento se redacta en alrededor de cinco páginas.
- Manual UPEL: el proyecto no pasa de 40 páginas, preliminares incluidas.

## Architecture boundaries

Follow the existing flow:

```text
Flask route -> controller -> service -> SQLAlchemy model -> MySQL
```

- Routes define HTTP contracts and delegate request handling.
- Controllers parse requests and prepare responses or templates.
- Services own business rules, authorization-sensitive operations, and transactions.
- Models define persistence. Do not move business logic into JavaScript or Jinja templates.
- Some page routes render templates directly and some existing dashboard queries are route-level. Reuse the current architecture; do not rewrite unrelated code just to make it uniform.

## Change rules

1. Apply KISS: prefer the smallest solution that fully satisfies the approved requirement.
2. Apply YAGNI: do not add speculative endpoints, screens, modules, layers, components, dependencies, or configuration.
3. Do not invent routes or expose menu links unless the route exists and the current user is authorized to use it.
4. Preserve existing routes, status codes, API payloads, form field names, validation, authentication, role/permission checks, and JavaScript behavior.
5. Before changing markup, trace every affected DOM ID, name, `data-*` attribute, selector, and event handler. Treat them as contracts unless the corresponding JavaScript is updated and verified in the same work unit.
6. Preserve inventory invariants: stock changes go through the existing services, create stock movements, enforce availability, and retain the current transaction and locking behavior.
7. Do not hardcode mockup metrics, inventory, sales, names, dates, forecasts, or statuses. Visible data must come from the backend or a truthful application state.
8. Never add `try`/`catch` or `try`/`except` around imports.

## Code quality guardrails

Apply these limits to newly created or materially modified handwritten code.
Do not refactor unrelated legacy code solely to satisfy a metric.

- Cyclomatic complexity: <= 20 per function or method; prefer <= 10.
- Cognitive complexity: <= 15 per function or method.
- Halstead difficulty: < 80 is a review signal, not an automatic failure.
- Source files should normally remain <= 500 logical lines.
  Do not split cohesive files merely to satisfy this number.
- Introduce no demonstrably dead code.
- Introduce no demonstrably redundant code.
- Prefer simplification over adding abstractions solely to reduce a metric.
- Metric compliance must not override KISS, YAGNI, architecture boundaries,
  correctness, authorization, or existing behavior.

### CRAP score

Do not enforce CRAP as a hard gate until reliable per-function test coverage
exists.

Once reliable coverage infrastructure is established:

- Target CRAP < 30 for newly created or materially modified functions.
- Tighten the target to CRAP < 25 once the test suite and coverage reporting
  are mature.
- Do not refactor unrelated legacy code solely to reduce CRAP.

### Type safety

The current frontend uses native JavaScript, not TypeScript. Do not introduce
TypeScript solely to satisfy these rules.

If TypeScript is introduced in an explicitly approved future change:

- No implicit `any`.
- Avoid explicit `any`; allow it only at a justified interoperability boundary
  when a safer type cannot reasonably express the contract.
- `unknown` is allowed when semantically appropriate and must be narrowed
  before use.

## Visual source and accessibility

- Use `references/` as the approved visual direction and read `references/README.md` plus `references/MANIFEST.csv` before implementing a referenced screen.

### Reference-driven frontend priority

When implementing a screen that has an approved visual reference:

- The approved reference controls the target macro-layout, visual hierarchy,
  proportions, spacing, density, component placement, and visual language.
- Existing backend behavior is a functional contract; the existing visual
  arrangement is not.
- Agents may substantially restructure Jinja markup and CSS to match the
  reference, provided required routes, permissions, form fields, DOM IDs,
  names, `data-*` attributes, selectors, event handlers, and API contracts
  remain functional.
- Do not preserve an old dashboard layout merely because it already exists.
- Existing secondary widgets may be relocated, collapsed, or moved below the
  primary viewport when necessary to match the approved reference.
- Missing backend data or functionality must not be fabricated. Use truthful
  values or truthful empty/insufficient-data states while preserving the
  reference's intended geometry.
- Backend-data differences from a mockup are acceptable.
  Macro-layout differences from the approved reference are not acceptable
  unless a documented functional or accessibility constraint requires them.
- Visual fidelity must be evaluated using rendered screenshots at the same
  viewport size and scale as the approved reference.
- Do not claim visual parity until the rendered screen has been directly
  compared with the approved reference.

  ### Approved UI sources

For reference-driven frontend implementation:

- `references/` remains the final visual authority.
- Use Tailwind CSS v4 utilities as the preferred styling system for newly
  implemented or substantially redesigned reference-driven UI.
- Use Lucide as the preferred icon system.
- shadcn/ui `dashboard-01` is an approved structural and component reference
  for dashboards.
- Cruip Mosaic is an approved dashboard and layout reference. Prefer its
  HTML/Tailwind patterns when adapting ideas to Jinja.
- 21st.dev and Origin UI are approved sources for individual UI patterns and
  components.
- React/TSX examples from shadcn/ui, 21st.dev, or Origin UI must be adapted to
  server-rendered Jinja/Tailwind rather than introducing React solely to use
  them.
- Do not copy mockup data, fake functionality, or unavailable routes from any
  component source.
- Component libraries are implementation references, not visual authorities.
  When they differ from an approved screenshot in `references/`, the approved
  screenshot wins.
- Do not install additional component-library runtime dependencies unless they
  are required by the adapted implementation and explicitly approved.

  Use Lucide icons for all newly redesigned reference-driven UI.
Do not mix Tabler Icons and Lucide within the same redesigned component unless
required temporarily by preserved legacy markup.

- **Never read, copy, package, or use anything in `references/90_archivo_no_usar/`.**
- The official logo source is `references/00_marca/logo_oficial_el_conejo.png`. Preserve that source; copy it into `app/static/` only when the browser needs a static asset.
- Use a light application background and sidebar, white cards, rounded surfaces, subtle borders/shadows, consistent spacing, and clear hierarchy. Use blue for navigation and primary actions, green for positive/available states, orange for warnings, and red for critical/destructive states.
- Keep layouts, tables, forms, modals, and navigation usable on desktop, tablet, mobile, and browser zoom.
- Keyboard navigation must work. Provide visible focus, semantic landmarks, labels for form controls, accessible names for icon-only controls, meaningful table headers, and `aria-current` for active navigation where applicable.
- Provide coherent loading, empty, insufficient-data, error, and success states. Do not present predictions when the backend reports that a model or sufficient data is unavailable.

## Authorization and privacy

- Backend permission checks are authoritative. UI visibility complements them; it never replaces them. Preserve restrictions for `admin`, `inventario`, `vendedor`, and granular permissions.
- Do not read, print, package, upload, or commit `.env`, untracked environment overrides, `instance/`, `uploads/`, `private_imports/`, user files, credentials, tokens, database dumps, or generated reports containing private data. A versioned `.env.example` may be read only as a configuration schema and must never contain real secrets.
- Treat `.codegraph/`, `repomix-output.*`, logs, test artifacts, and generated analysis as local/sensitive until reviewed. Keep secrets and private paths out of prompts, screenshots, diffs, issues, and pull requests.
- Do not expose internal exceptions, configuration values, historical imports, or user data in UI or API changes.

## Verification by risk

Start with the smallest relevant checks. Never report a check as passed unless it ran successfully.

### Safe, non-data-mutating checks

```powershell
git status --short --branch
git diff --check
npm run codegraph:doctor
npm run openspec:doctor
```

Use `node --check` on each changed JavaScript file. Review the full diff and confirm no secret, private file, generated output, or unrelated change was included.

### Isolated disposable SQLite suites

These suites create their own temporary SQLite data:

```powershell
python scripts/test_delivery_note_pdf.py
python scripts/test_historical_imports.py
python scripts/test_prediction_readiness.py
```

### Requires an explicitly disposable database or environment

- Treat every other script under `scripts/` as stateful until its source is inspected. This includes initialization, migrations, cleanup scripts, live-server HTTP suites, and PowerShell suites.
- Do not run `scripts/init_db.py`, `scripts/migrate_*.py`, `scripts/cleanup_*.py`, or live HTTP tests against valuable data.
- Before a stateful check, verify the target database, seed users, server, file storage, and cleanup behavior. Record the exact command and result.

## Tools and specifications

- `AGENTS.md` owns repository-wide assistant instructions. Cursor rules and tool configuration must point here rather than duplicate it.
- OpenSpec stores approved requirements, scenarios, design decisions, and acceptance criteria. It does not duplicate this guide or grant implementation approval.
- Use the existing package scripts for CodeGraph, Repomix, and OpenSpec. `npm run repomix:pack` writes `repomix-output.xml`; inspect exclusions and sensitive content before running or sharing it.
- Archify diagrams must reflect repository evidence and exclude private/generated data.
- Do not install, configure, invoke, or document Engram or Gentle AI.
- Pi is a secondary read-only reviewer in Phase 1. Agents must invoke Pi through `.cursor/pi-review.sh`. Raw Pi coding mode, write/edit/bash tools, Pi packages, MCP, and subagents are not approved.
- Ponytail reinforces KISS/YAGNI and does not require repository scaffolding. Do not add a tool when an installed or native capability already covers the need.
- RTK is undefined for this project. Do not assume Redux Toolkit or configure RTK until the user identifies the exact tool.

### zg Phase 2B security exception

**Decision: ADOPT WITH DOCUMENTED SECURITY EXCEPTION.** The approved evaluation rated utility **3/5** and privacy **PASS**. This exception is limited to `@zvec/zvec-grep@0.2.1`, the model `local/potion-code-16m-v2`, and text/code content through `npm run zg:index`.

- Finding: `zg -> @huggingface/transformers@3.8.1 -> sharp@0.34.5` reaches `GHSA-f88m-g3jw-g9cj` (upstream severity: **High**; patched upstream in `sharp >=0.35.0`). The three High audit package entries derive from this single root GHSA.
- Approved reachability: **NOT LOADED**. The allowed Potion Model2Vec text/code path does not load the Transformers.js image backend.
- Image extraction, image processing, and multimodal indexing are prohibited. In `@zvec/zvec-grep@0.2.1`, only GIF, JPEG/JPG, PNG, and WebP can enter `ImageExtractor`; those raster formats are excluded by default unless explicitly selected. The approved command keeps the existing case-insensitive `--iglob` exclusions for those formats plus `.tif`/`.tiff`/`.vips` as defense in depth. SVG, AVIF, HEIC, and BMP are not ImageExtractor formats in 0.2.1 and do not reach the sharp/libvips image-processing path. `.repomixignore` remains the mandatory privacy boundary and is validated fail-closed (required exclusions present, no `!` re-inclusion) before either a missing-index pass or an existing-index check. Remote embeddings, multimodal operation, the Transformers.js backend, `--allow-remote`, API keys, any other model, and a `sharp` override are prohibited. Do not use `--hidden`, `--no-ignore`, or `zg install`; no supported upstream mitigation currently permits overriding to `sharp >=0.35.0`.
- MCP is not currently approved; do not configure or use zg MCP.
- A pre-existing `.zvec-grep/` index must pass `npm run zg:preflight` before use. Mismatch or unknown state blocks `npm run zg:index` and `npm run zg:status`. Rebuild, reset-paths, or drop require an explicit operator decision; never automatically repair an incompatible index.

Require a new security review before use if any of these occurs:

1. `@zvec/zvec-grep` changes from `0.2.1`.
2. `@huggingface/transformers` or `sharp` changes.
3. Another High or Critical GHSA appears.
4. The embedding model changes.
5. Image indexing or processing is enabled.
6. Remote embeddings or multimodal operation is enabled.
7. zg MCP configuration is attempted.
8. An upstream zg release supports `sharp >=0.35.0`.

## Git workflow obligatorio

Para operaciones Git no triviales, consultar `docs/GIT_WORKFLOW.md`.

Git es la fuente de verdad para distinguir trabajo previo, cambios de la tarea actual, archivos nuevos, temporales y documentos que requieren revisión manual.

### 1. Inspección inicial

Antes de una tarea que vaya a modificar archivos, comprobar como mínimo:

```powershell
git status --short
git branch --show-current
```

Y cuando sea relevante:

```powershell
git diff --stat
git diff --cached --stat
git ls-files --others --exclude-standard
```

No exigir una auditoría completa de todo el repositorio para un cambio trivial.

### 2. Preservar trabajo previo

Nunca asumir que los cambios existentes pertenecen a la tarea actual. Este repositorio puede tener working tree sucio. Está prohibido destruir trabajo previo mediante `git reset --hard`, `git restore .`, `git checkout -- .` o `git clean -fd`. No revertir, borrar ni reemplazar cambios ajenos a la tarea.

### 3. Archivos nuevos

Todo archivo nuevo debe terminar clasificado deliberadamente como:

- TRACK — código, tests, scripts, templates, documentación fuente real.
- IGNORE — temporales, caché, SQLite desechable, logs, locks y outputs regenerables.
- REVIEW — DOCX, PDF, datasets, respaldos, referencias académicas, dumps, archivos grandes o con datos personales.
- DELETE — solo temporales inequívocos y solo con autorización.

Código fuente real no debe permanecer indefinidamente como `??`.

### 4. Staging seguro

En un working tree con cambios preexistentes, NO usar por defecto `git add .` ni `git add -A`. Stagear únicamente archivos relacionados con el work unit actual. Antes de commit revisar `git status --short`, `git diff --cached --stat` y `git diff --cached`. Si no es posible aislar con seguridad los cambios de la tarea, no hacer commit y reportarlo.

### 5. Commits locales

Los commits locales pequeños SON deseables después de completar un work unit estable y probado. No seguir una política permanente de "no commit" si eso hace que código real permanezca durante semanas como untracked. Cada commit debe corresponder a un work unit coherente, ser pequeño y reversible, contener solo cambios relacionados, tener pruebas relevantes ejecutadas y pasar `git diff --check`. Mensajes en español sencillo y natural. Ejemplos: `agrega perfil de demanda intermitente`, `integra detalle predictivo experimental`, `agrega pruebas del modelo de dos partes`, `corrige documentación metodológica del tomo`. No usar `--amend` sobre commits anteriores salvo instrucción explícita. IMPORTANTE: si el prompt actual dice expresamente "no commit", esa instrucción específica tiene prioridad y no se debe crear commit.

### 6. Push

Nunca hacer `git push` ni crear PR, publicar ramas o modificar remotos sin autorización explícita. Commit local NO significa permiso de push.

### 7. Tests

Distinguir siempre test escrito, test ejecutado y test aprobado. No afirmar que una prueba pasó solo porque existe. Antes de cerrar un work unit de código: ejecutar sus pruebas relevantes, ejecutar `git diff --check` e informar fallos reales.

### 8. Archivos grandes y tesis

No agregar automáticamente a Git DOCX, PDF, datasets, dumps, respaldos, archivos con datos personales ni tesis/publicaciones de terceros. Clasificarlos primero como REVIEW. Preferir versionar Markdown fuente, scripts, código, tests, documentación técnica y figuras fuente cuando proceda.

### 9. .gitignore

Si un artefacto regenerable aparece repetidamente como `??`, proponer una regla específica de `.gitignore`. No crear reglas amplias que oculten código real. Usar `.git/info/exclude` cuando la exclusión deba ser solo local.

### 10. Finalización

Al terminar una tarea que modificó archivos informar: rama actual, archivos modificados, archivos nuevos, tests ejecutados, commits creados (si hubo), archivos nuevos que continúan untracked, cambios preexistentes preservados y estado final del working tree. No decir simplemente "listo" si quedan archivos sin clasificar.

### Regla crítica

Nunca sacrificar trabajo existente solo para obtener un `git status` limpio. El objetivo es que cada archivo tenga un estado deliberado: TRACKED, IGNORED o REVIEW.

## Per-phase handoff

Before editing, report: objective, diagnosis, planned files, risks, and planned checks.

After editing, report: summary, files changed, KISS/YAGNI decisions, exact checks and results, screenshots taken, limitations, and—only when actually created—the commit hash and pull-request identifier/link.
