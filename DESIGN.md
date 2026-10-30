---
name: El Conejo — Sistema de inventario
description: Herramienta administrativa clara y sobria para controlar inventario, compras y despachos de una ferretería.
colors:
  ground-paper: "#f3f4f1"
  rail-paper: "#eceee9"
  surface: "#ffffff"
  tint: "#f7f8f5"
  rule: "#e2e4df"
  rule-strong: "#cdd1ca"
  ink: "#191c1a"
  ink-2: "#3a403c"
  muted: "#626a65"
  primer-orange: "#b45309"
  primer-orange-deep: "#8f4207"
  primer-orange-vivid: "#e07b10"
  primer-orange-soft: "#fbf4ec"
  primer-orange-line: "#ecd3b9"
  logo-sky: "#1f6fa8"
  logo-sky-soft: "#e8f2f9"
  state-ok: "#1e7a45"
  state-ok-soft: "#e6f4eb"
  state-warn: "#c98c00"
  state-warn-ink: "#7a5600"
  state-warn-soft: "#fff5d6"
  state-danger: "#b42318"
  state-danger-soft: "#fdeceb"
typography:
  headline:
    fontFamily: "Hanken Grotesk, Segoe UI, system-ui, sans-serif"
    fontSize: "26px"
    fontWeight: 700
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Hanken Grotesk, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 650
    letterSpacing: "-0.01em"
  metric:
    fontFamily: "Hanken Grotesk, Segoe UI, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 700
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Hanken Grotesk, Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
  code:
    fontFamily: "JetBrains Mono, Cascadia Mono, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 500
rounded:
  sm: "6px"
  control: "8px"
  card: "10px"
  swatch: "3px"
components:
  button-primary:
    backgroundColor: "{colors.primer-orange}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
  button-primary-hover:
    backgroundColor: "{colors.primer-orange-deep}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.card}"
  nav-item-active:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
  kpi-swatch:
    backgroundColor: "{colors.primer-orange}"
    rounded: "{rounded.swatch}"
    size: "12px"
---

# Design System: El Conejo — Sistema de inventario

## Overview

**Creative North Star: "La carta de colores"**

El sistema toma su gesto de la carta de muestras de pintura que cualquier ferretería tiene en el mostrador: cada estado, categoría, indicador y reporte se reconoce por una pequeña muestra cuadrada de color junto a su nombre y su cifra. Todo lo demás es papel frío, tinta oscura y reglas de 1px. La marca aparece en el naranja de plantilla del logo, reservado para la acción y la selección, y en el azul cielo del logo para los enlaces que llevan a un dato.

Es una herramienta de trabajo, no una vitrina. Se usa en PC y en celular, con turnos de mostrador y de almacén; la densidad es media, las tablas son el contenido principal y las gráficas viven aparte, en «Reportes y análisis», para que cada pantalla operativa muestre solo lo que hay que hacer. Rechazos confirmados por el usuario: aspecto «hecho por IA» (degradados, tarjetas con sombra flotante, iconos en círculos de colores, rejillas de tarjetas idénticas) y sobrecarga de contenido.

**Key Characteristics:**
- Superficies planas sobre fondo papel; profundidad por regla y tono, nunca por sombra.
- Indicadores en tira continua con divisores, marcados por una muestra de 12px.
- Un solo acento de acción (naranja de plantilla); estados en verde, ámbar y rojo.
- Códigos de producto y referencias en monoespaciada.
- Inicio operativo; análisis y gráficas en una sección propia.

## Colors

Neutros fríos casi grises con un único acento cálido de marca; el color de estado se usa solo para estados.

### Primary
- **Naranja de plantilla** (#b45309): botón primario, elemento activo del menú (barra lateral de 3px y cuadro de 9px en el hijo activo), paginación activa, foco. Variante profunda #8f4207 para hover; viva #e07b10 para series de gráfica y anillo de foco.

### Secondary
- **Azul cielo del logo** (#1f6fa8): enlaces de dato (referencias de nota, «ver reporte»). Nunca en botones ni en estados.

### Neutral
- **Papel frío** (#f3f4f1): fondo de la aplicación. **Riel** (#eceee9): barra lateral.
- **Superficie** (#ffffff) y **tinte** (#f7f8f5): tarjetas y cabeceras de tabla.
- **Regla** (#e2e4df) y **regla fuerte** (#cdd1ca): bordes y divisores.
- **Tinta** (#191c1a), **tinta 2** (#3a403c) y **apagado** (#626a65): texto principal, secundario y auxiliar.

### Estados
- **Disponible / positivo** (#1e7a45 sobre #e6f4eb).
- **Advertencia / bajo stock** (#c98c00, texto #7a5600 sobre #fff5d6).
- **Crítico / agotado / destructivo** (#b42318 sobre #fdeceb).

### Identidad de categoría
- Cada categoría conserva el color que el usuario le asignó (azul #2f7fc1, verde, morado, turquesa, etc.). Se muestra solo como muestra o barra junto al nombre de la categoría; nunca como color de estado ni de botón.

### Named Rules
**La regla de una voz.** El naranja de plantilla marca la acción principal y la selección; no decora. Si una pantalla tiene dos botones naranjas, uno sobra.
**La regla del estado honesto.** Verde, ámbar y rojo significan estado del inventario o de un documento. No se usan como color de categoría ni de adorno.

## Typography

**Body Font:** Hanken Grotesk (con Segoe UI / system-ui)
**Mono Font:** JetBrains Mono, peso 500, para códigos de producto, números de documento y referencias.

**Character:** grotesca humanista de oficina, firme en títulos y legible a 13px en tablas; la mono separa lo que se teclea o se busca de lo que se lee.

### Hierarchy
- **Headline** (700, ~26px, -0.025em): título de pantalla, uno por página.
- **Title** (650, 15px): título de tarjeta o sección.
- **Metric** (700, 22px; 19px en celular): cifra de indicador, con cifras tabulares.
- **Body** (400, 13px en tablas, 14px en formularios y guía).
- **Label** (600, 12px): etiquetas de filtro y de indicador.

## Layout

Shell de dos columnas: riel lateral fijo de ~244px con grupos plegables (Inicio, Inventario, Ventas, Compras, Chatbot, Reportes y análisis, Configuración, Guía de uso) y área principal fluida con margen de 24–28px. Cada pantalla operativa sigue el mismo orden: cabecera (título, una línea de contexto, acciones), tira de indicadores, filtros, tabla. Las gráficas y los KPI de análisis viven en **Reportes y análisis › Panorama** (`/reportes`); las pantallas operativas enlazan a ellas con un enlace discreto bajo los indicadores.

En ≤640px el menú se abre con botón, las tiras de indicadores pasan a 2 × 2 y las tablas se desplazan en su propio contenedor.

## Elevation & Depth

Plano por defecto. Las tarjetas no llevan sombra: se separan del papel por una regla de 1px y el cambio de tono blanco sobre gris frío. Solo los elementos superpuestos (menús desplegables, diálogos, cajón de producto) usan sombra corta y fondo atenuado.

**La regla del papel plano.** Si algo necesita sombra para distinguirse, falta una regla o sobra un contenedor.

## Shapes

Radios contenidos: 10px en tarjetas, 8px en controles y botones, 6px en chips, 3px en las muestras de color. Los iconos de indicador no son círculos: son muestras cuadradas de 12px con un borde interior sutil.

## Components

- **Tira de indicadores** (`.ix-kpis`, `.po-kpis`, `.sp-kpis`): una sola tarjeta con celdas separadas por divisores; cada celda lleva muestra, etiqueta, cifra y una línea de contexto.
- **Muestra** (`.cj-swatch`): cuadro de color con su nombre y cifra; base de la leyenda de salud, de las barras por categoría y del índice de reportes.
- **Tira de salud** (`.cj-strip`): barra segmentada proporcional (disponible, bajo stock, agotado) con leyenda de muestras debajo.
- **Botón primario**: naranja de plantilla, texto blanco, radio 8px, sin sombra; hover al tono profundo.
- **Menú lateral**: el activo es una superficie blanca con barra naranja a la izquierda; el hijo activo, un cuadro naranja de 9px.
- **Tablas**: cabecera en tinte, texto 13px, encabezados que pueden partirse en dos líneas, cifras alineadas a la derecha.
- **Estados vacíos**: texto que dice qué falta y por qué («Todavía no hay productos activos en el catálogo»), nunca cifras inventadas.

## Do's and Don'ts

- **Do** usar una muestra de color para marcar cada estado, categoría o indicador.
- **Do** mantener Inicio operativo: propuestas, alertas, movimientos y bajo stock.
- **Do** escribir los códigos en JetBrains Mono.
- **Don't** añadir gráficas a pantallas operativas; van en Reportes y análisis.
- **Don't** usar sombras de tarjeta, degradados ni iconos dentro de círculos de color.
- **Don't** usar el naranja de acción como color de advertencia: la advertencia es ámbar.
- **Don't** tocar el modo referencia (`html[data-reference-mode="1"]`), que reproduce las maquetas aprobadas de `references/`.
