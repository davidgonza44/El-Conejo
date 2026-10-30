# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Personal de Ferretería y Construcciones El Conejo C.A. (Acarigua, Portuguesa, Venezuela) con cinco roles: administrador, inventario, asistente administrativo, vendedor y soporte TI. Uso mixto: operaciones repetidas en computadora de mostrador u oficina durante la jornada, y consultas o aprobaciones rápidas desde el celular en el piso de venta o el almacén.

## Product Purpose

Sistema de control de inventario: registra el movimiento de mercancía (entradas, salidas, notas de entrega, compras y recepciones), analiza la demanda, la pronostica y recomienda reabastecimiento. Éxito: stock correcto y trazable, compras a tiempo y decisiones de reposición basadas en datos. Es el producto de un trabajo de grado (UJAP).

## Positioning

No es un ERP. El diferenciador es movimiento de mercancía trazable, análisis, pronóstico de demanda y recomendación de reabastecimiento con estados honestos cuando no hay datos suficientes. Catálogo visual, notas de entrega y chatbot son soporte.

## Operating Context

Separación de funciones: el asistente propone (productos, categorías, proveedores, cotizaciones, órdenes), el administrador aprueba, inventario mueve stock y recibe mercancía, el vendedor emite notas de entrega. Documentos: notas de entrega, órdenes de compra, recepciones y reportes en PDF, Excel o impresión con membrete (logo, nombre y RIF). Importación histórica CSV v1 de ventas 2025. Monedas USD y VES por registro, sin conversión.

## Capabilities and Constraints

Flask + Jinja2 renderizado en servidor, JavaScript nativo, Chart.js, Tailwind v4 para pantallas rediseñadas e iconos Lucide; sin SPA. Rutas, permisos, IDs del DOM y contratos de API son contratos funcionales. Todo dato visible sale del backend; sin cifras de maqueta. Texto en español. Ventas a clientes está retirada en esta versión.

## Brand Commitments

Nombre: Ferretería y Construcciones El Conejo C.A. Logo oficial: `references/00_marca/logo_oficial_el_conejo.png` (copia en `app/static/img/`). Interfaz clara y sobria; el acento principal se toma de la marca (decisión del usuario, 30/10/2026); verde, naranja y rojo solo para estados (disponible, advertencia, crítico). El usuario pidió un estilo moderno, limpio, funcional y operativo que no parezca generado por IA, y sin sobrecarga de contenido.

## Evidence on Hand

Logo oficial. No hay fotografías de productos ni datos reales en el repositorio público; las pruebas usan datos sintéticos (`e2e/harness/seed.py`). No inventar clientes, ventas ni métricas.

## Product Principles

- Cada pantalla operativa hace un trabajo: lista y acciones; el análisis vive en «Reportes y análisis».
- Ningún número sin fuente: estados vacíos o «historial insuficiente» antes que cifras inventadas.
- Quien propone no aprueba; la interfaz deja claro qué espera aprobación.
- Igual de útil en el mostrador que en el celular.

## Accessibility & Inclusion

Navegación por teclado, foco visible, etiquetas en controles, contraste AA, uso en celular y con zoom del navegador.
