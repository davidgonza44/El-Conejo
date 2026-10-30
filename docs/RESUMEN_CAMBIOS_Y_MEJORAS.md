# Resumen de cambios y mejoras — Ferretería y Construcciones El Conejo C.A.

Fecha: 30/10/2026 · Rama: `mejoras/calidad-e2e-documentos`

Este documento resume qué se corrigió y mejoró en el sistema, cómo estaba antes y cómo está ahora,
y cómo se verificó. Ninguna sección existente se eliminó: las pantallas que estaban fuera del menú
principal ahora son accesibles desde él.

## 1. Verificación automatizada

| Aspecto | Antes | Ahora |
| --- | --- | --- |
| Pruebas de extremo a extremo (E2E) | No existían como suite. Había scripts sueltos que dependían de MySQL o de servidores manuales. | Suite Playwright en `e2e/` con 12 archivos que recorren el sistema real en Chrome contra una base SQLite desechable. Comando: `npm run e2e`. |
| Cobertura por rol | Sin verificación sistemática de permisos en pantalla. | Cada pantalla se prueba con los 5 roles (abre o bloquea con 403), y cada enlace del menú de cada rol abre una página autorizada. |
| Flujos de negocio | Sin prueba integral. | Producto (propuesta → aprobación → stock inicial), movimientos, notas de entrega (emisión, cancelación, PDF), proveedores y categorías, ciclo completo de compra (solicitud → cotización → orden → aprobación → recepción), importación histórica, reportes, configuración, perfil, chatbot y catálogo. |
| Análisis de calidad del código | Sin informe. | Lint (ruff), complejidad ciclomática (radon), código muerto (vulture), sintaxis de los 64+ archivos JS, búsqueda de XSS y SQL dinámico. Ver sección 6. |

Resultado de la última ejecución completa (base limpia): **244 pruebas aprobadas, 0 fallidas, 0 inestables** (7,9 minutos).

## 2. Correcciones solicitadas (archivo «Correcciones El Conejo»)

Cada punto se comprobó contra el código actual antes de cambiarlo.

### Compras

| # | Problema | Estado | Antes → Ahora |
| --- | --- | --- | --- |
| 1 | Inventario podía deducir precios de compra en Recepciones | Corregido | Filtros por monto mínimo/máximo y orden por monto aceptados para cualquier rol → el servidor los rechaza (403) sin permiso de costos y la pantalla ya no los ofrece. Impresiones y exportaciones ya ocultaban montos. |
| 2 | Cambios de proveedores sin aprobación | Corregido | «Proponer modificación» sobrescribía de inmediato los datos oficiales y desactivaba al proveedor → la modificación se guarda como propuesta separada (`supplier_changes`); los datos oficiales no cambian hasta que el administrador aprueba, y un rechazo los deja intactos. Quien propone no puede decidir. El administrador ve «valor vigente → propuesto». |
| 2b | Identidad del proveedor en órdenes históricas | Pendiente | Requiere guardar una copia de los datos del proveedor en cada orden (migración de esquema y de datos). Se recomienda como siguiente paso. |
| 3 | Órdenes antiguas OC-000016/17/18 sin cotización | No aplica al código | Son registros de la base de demostración. El sistema ya permite anular un borrador con motivo, sin inventar cotizaciones ni tocar existencias. |
| 4 | Órdenes completadas distintas en Compras y Proveedores | Corregido | Proveedores contaba solo `completed` y Compras sumaba `closed_complete`; además el porcentaje usaba otro numerador → criterio único `COMPLETED_STATUSES` en ambos módulos. |

### Inventario y movimientos

| # | Problema | Estado | Antes → Ahora |
| --- | --- | --- | --- |
| 1 | Identificadores decimales o booleanos | Corregido | `15.5` y `true` se convertían a un producto válido → solo enteros positivos; lo demás responde 400. |
| 2 | Stock fuera de rango producía error 500 | Corregido | Sin límite superior → cantidades y stock resultante limitados al máximo de la columna; error 400 claro y sin cambios en existencias. |
| 3 | Escrituras sin validación CSRF | Corregido en todo el sistema | Solo Autenticación, Configuración, Usuarios e Importación lo exigían → el servidor exige el token en toda escritura con sesión, y `auth.js` lo añade automáticamente a las peticiones del propio sitio. |
| 4 | Mensaje de stock insuficiente fuera de la vista | Corregido | Aviso azul al inicio del formulario → aviso rojo junto al campo Cantidad, enlazado con `aria-describedby`, visible y con foco al fallar. |

### Notas de entrega

| # | Problema | Estado | Antes → Ahora |
| --- | --- | --- | --- |
| 1 | Selector con productos en monedas no admitidas | Corregido | Se ofrecían productos en VES que el servidor luego rechazaba → solo productos con precio en USD y existencia. |
| 2 | Precios desactualizados al emitir | Corregido | El servidor usaba el precio nuevo sin avisar → la pantalla envía el precio mostrado; si cambió, el servidor responde 409 con el detalle y la nota pide confirmar el importe nuevo. La verificación ocurre con las filas bloqueadas, así que no puede cambiar entre la confirmación y la emisión. |
| 3 | Sin CSRF | Corregido | Ver punto 3 de Inventario. |
| 4 | Longitudes no validadas en el servidor | Corregido | Nombre 150, documento 30, teléfono 30, dirección 255 → error 400 claro. |
| 5 | Documento y teléfono sin formato | Corregido | Documento con letras, números, puntos y guiones (V-12345678, J-12345678-9); teléfono con dígitos, espacios, guiones y + inicial. |
| 6 | Emisión sin confirmación final | Corregido | Un clic emitía → paso «Revisar nota» con cliente, productos, precios, total y aviso de salida de inventario; luego «Confirmar emisión». El botón se bloquea para evitar dobles emisiones. |
| 7 | Código JS innecesario (`alreadyInNote`) | Corregido | Eliminado. |

### Reporte de ventas

| # | Problema | Estado | Antes → Ahora |
| --- | --- | --- | --- |
| 1 | Importes presentados como dólares | Corregido | Símbolo «$» en todo el reporte → importes sin símbolo y aviso «sin moneda de origen registrada» en la pantalla y en el PDF. El CSV se mantiene como dato puro para poder reimportarlo. |
| 2 | Ticket promedio con fórmulas distintas | Corregido | Métodos de pago usaba la media simple de los promedios → promedio ponderado por ventas («Ticket promedio por venta»). El indicador de productos se llama «Ingreso prom. por producto» porque mide otra cosa. |
| 3 | Ventas históricas cambian de categoría | Parcial | Se advierte en pantalla que la agrupación usa la categoría actual. Guardar la categoría en cada venta requiere una migración de `sale_lines` (el módulo de ventas está retirado en esta versión). |
| 4 | «Ver detalle» de categoría por texto | Corregido | Búsqueda por nombre → filtro por id exacto de la categoría, con indicador «Categoría: …» y exportación con el mismo filtro. |
| 5 | «Ingresos cobrados» incluía ventas por cobrar | Corregido | Renombrado a «Ventas registradas», que es lo que realmente mide. |
| 6 | «Público en general» como cliente recurrente | Corregido | Se excluye de clientes identificados, recurrencia y frecuencia; el KPI se llama «Clientes identificados». |

## 3. Errores adicionales encontrados y corregidos

| Área | Antes | Ahora |
| --- | --- | --- |
| Reportes | Las plantillas `reports/sales.html`, `reports/history.html` y `reports/dashboard.html` no existían: el menú «Reportes» llevaba a un error 500. | Plantillas recreadas a partir del controlador, el CSS y el JS existentes; las cinco pestañas, el histórico y la exportación funcionan. |
| Errores de página | Un parámetro inválido o una ruta inexistente mostraban JSON crudo (`{"error": "..."}`) en el navegador. | Página de error con la marca, el mensaje claro y «Ir al inicio». La API conserva sus respuestas JSON. |
| Acceso restringido (403) | Texto duplicado y botón «Volver al login» aunque el usuario ya había iniciado sesión. | Mensaje único, «Ir al inicio» del rol y «Cerrar sesión». |
| Usuarios | Al crear un usuario el aviso decía «Usuario undefined creado (U-undefined)» y no quedaba seleccionado. | El JS lee la respuesta real `{message, user}`; el aviso nombra al usuario y lo selecciona. |
| Chatbot | «¿Cuál es el stock del producto E2E-001?» respondía «no encontré»: solo buscaba códigos si se escribía la palabra «código». | Una entidad de una sola palabra se busca primero como código exacto. Función refactorizada (complejidad 30 → 18). |
| Catálogo visual | Sin permiso de precio mostraba «PRECIO 0,00» (dato falso); el texto escrito se borraba al terminar de cargar; los filtros se veían truncados. | El precio solo aparece si el rol puede verlo (y se quita el orden por precio); la búsqueda escrita se conserva; columnas redistribuidas. |
| Navegación | Catálogo visual, Importación histórica y Diagnóstico de historial no aparecían en el menú moderno; esas tres pantallas usaban otro menú y otro estilo. | Están en sus grupos del menú (según permisos) y usan el mismo shell: barra lateral, búsqueda global, alertas y cuenta. |
| Nota de entrega en PDF | El campo «Documento» se salía del margen y quedaba cortado. | Cada dato del cliente en su propia línea. |
| Reporte en móvil | El menú y la campana se montaban sobre el subtítulo. | Cabecera en flujo normal en tablet y móvil, sin desbordamiento horizontal. |
| Ingreso | «Continuar con Google» aparecía sin credenciales configuradas y terminaba en un error. | Solo se muestra cuando Google está configurado. |
| Favicon | Varias páginas pedían `/favicon.ico` inexistente (error 404 en consola). | Todas declaran el logo como ícono. |
| Seguridad (XSS) | Inicio insertaba mensajes del servidor con `innerHTML`. | Se insertan como texto. |
| Código muerto | 9 importaciones sin uso. | Eliminadas. |
| Avatar | Iniciales «E2» para «E2E Administración». | Iniciales de nombre y apellido («EA»). |

## 4. Documentos con membrete (PDF, Excel e impresiones)

| Documento | Antes | Ahora |
| --- | --- | --- |
| PDF de listados (movimientos, categorías, productos) y reporte de ventas | Nombre de la empresa en texto, sin logo ni RIF. | Membrete en cada página: logo, nombre, RIF, fecha de generación y línea azul; número de página al pie. |
| Nota de entrega (PDF) | Banda azul con el nombre. | Banda con el logo en recuadro blanco, nombre y RIF. |
| Excel | Tabla desde la fila 1. | Logo, empresa, RIF, título y fecha arriba; encabezado de tabla resaltado, fijo y con filtros. |
| Impresiones (orden de compra, recepción, movimientos, pedido) | Solo el nombre como título. | Membrete con logo, nombre y RIF. |
| CSV | — | Sin cambios a propósito: es un formato de datos para reimportar o analizar. |

El RIF se configura en **Configuración › General** (campo nuevo, formato `J-12345678-9`). Si está vacío no se imprime: el sistema no inventa un RIF. El logo usado es el subido en Configuración o, si no hay, el logo oficial.

## 5. Guía de uso y mejoras visuales

- **Guía de uso** (`/ayuda`, enlace «Guía de uso» en el menú): primeros pasos, separación de funciones por rol, instrucciones paso a paso de cada módulo, ciclo de compra, documentos y preguntas frecuentes. Cada rol ve solo las secciones de los módulos que puede usar. Incluye búsqueda dentro de la guía, índice con la sección activa y versión imprimible.
- **Shell único**: todas las pantallas comparten la misma barra lateral clara, cabecera, búsqueda global, alertas y menú de cuenta.
- **Acabados globales** (`ix_ui.css`): color de selección de texto, barras de desplazamiento discretas, foco visible para teclado en todos los controles, cifras tabulares en tablas e indicadores, fondo atenuado en diálogos y transiciones cortas que respetan «reducir movimiento».
- **Notas de entrega**: tarjeta de revisión con resumen legible y aviso de inventario.
- **Reporte de ventas**: notas de calidad de datos visibles, filtro de categoría como etiqueta, cabecera adaptable.

## 6. Análisis de calidad del código

| Revisión | Resultado |
| --- | --- |
| Errores de Python (nombres indefinidos, sintaxis) | 0 |
| Importaciones sin uso | 9 eliminadas (las de `app/models/__init__.py` se conservan: registran los modelos). |
| Código muerto (vulture, confianza ≥ 80 %) | 0 |
| Sintaxis de JavaScript (`node --check`) | Todos los archivos válidos. |
| SQL construido con texto / `eval` / `shell=True` | 0 casos; `subprocess` usa listas de argumentos. |
| Complejidad ciclomática media | A (4,4) en 2 140 bloques. |
| Funciones con complejidad > 20 (heredadas) | 22, la mayor `_rebuild_dynamic_issues` (105) en importación histórica. No se refactorizaron: `AGENTS.md` pide no reescribir código heredado solo por la métrica sin pruebas específicas. Se recomienda abordarlas con pruebas unitarias dedicadas. |

## 7. Cómo verificar

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
npm ci
npm run e2e
```

Resultado E2E: 244/244 aprobadas.

Suites aisladas existentes ejecutadas: `test_delivery_note_pdf.py` (41/41) y `test_historical_imports.py` (OK).
`test_prediction_readiness.py` necesita MySQL local; `test_report_sales_reference.py` falla antes y después de estos cambios porque su arnés antiguo no registra la base de datos que hoy consulta el control de permisos.

## 8. Pendientes recomendados

1. Aplicar la migración `scripts/migrate_supplier_changes.py --apply` (o `migrations/versions/0009_supplier_changes.py`) en la base MySQL antes de usar propuestas de proveedores. Sin la tabla, el sistema rechaza la edición de un proveedor aprobado con un mensaje claro en lugar de sobrescribirlo.
2. Guardar una copia de los datos del proveedor en cada orden de compra y la categoría en cada línea de venta.
3. Actualizar `docker_support/demo_operations.py`: el sembrado del demo ya no cumple la separación de funciones actual (el administrador no puede proponer categorías) ni exige moneda de precios.
4. Reducir con pruebas dedicadas la complejidad de las funciones heredadas señaladas.
