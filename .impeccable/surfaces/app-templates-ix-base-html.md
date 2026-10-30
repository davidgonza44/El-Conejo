---
version: 1
slug: "app-templates-ix-base-html"
primary_target: "app/templates/ix_base.html"
related_targets: ["app/templates/base_app.html"]
---

# Shell de la aplicación (todas las pantallas)

Modo: Operate. Uso mixto PC de mostrador/oficina y celular en piso de venta y almacén.

## Direction contract

THESIS: Un sistema de control de inventario que se lee como una carta de colores de pintura: cada estado, categoría y módulo es una muestra con su código, ordenada en tiras. Rechaza el tablero SaaS genérico (tarjetas blancas idénticas, azul de plantilla, KPI con icono redondo en cada pantalla).

OWN-WORLD: Fondo papel neutro frío, superficies planas con regla de 1px y sin sombras de tarjeta; acento naranja de plantilla del logo para acción y selección, azul cielo del logo para enlaces de dato; verde, ámbar y rojo solo para estados. Tipografía de trabajo Hanken Grotesk con cifras tabulares; códigos (SKU, NE-, OC-, MOV-) en JetBrains Mono. Muestras cuadradas de 10–12px con código como unidad de identidad.

STORY: El personal abre una pantalla y ve primero su trabajo (lista y acciones); lo pendiente y lo crítico aparece como muestra de color; el análisis y las gráficas viven en «Reportes y análisis».

FIRST VIEWPORT: Riel lateral claro con el logo y los módulos; el módulo activo lleva su muestra naranja. Cabecera compacta: título, una línea de contexto, acción primaria naranja a la derecha. Debajo, la tabla o lista de trabajo ocupa el ancho; indicadores como una tira de muestras compacta, no tarjetas grandes.

FORM: Carta de colores de pintura (abanico de muestras), candidato 4 de 7; seed: 507e4cdc1da1c79b. Raises: «Jerarquía por escala, sin ornamento» (de la muestra tipográfica); «cromo eliminado: superficies planas, sin doble borde y sombra» (del mosaico metro); «el cambio conserva su color hasta verlo» (del tablero de salidas, competitivo); «reglas de 1px y pocos valores» (de la composición de referencia).

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
