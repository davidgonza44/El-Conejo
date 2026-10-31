# Plan de robustez El Conejo — defensa de tesis y uso empresarial

31/10/2026 · Auditoría en navegador sobre el laboratorio 5099 (rama `integracion/inventario-compras-notas`, commit `fb3744c`).

La auditoría encontró **58 problemas: 8 graves, 24 medios y 26 menores**. En uso normal no hubo errores de JavaScript ni fallos del servidor, con los 5 roles y unas 90 pantallas y 150 ventanas abiertas.

El núcleo del sistema guarda bien los datos: stock, movimientos, compras, notas de entrega y conciliación. Lo que falla es cómo la interfaz los **muestra, valida y conecta**.

Este plan ordena el trabajo en 4 fases:

- **Fases 0 a 2:** obligatorias para la defensa. Son unos 8 a 11 días de trabajo de una persona (estimado).
- **Fase 3:** da la capacidad empresarial. Si no entra antes de la defensa, se declara en el alcance de la tesis.

---

## Hoja de ruta

```
Fase 0  Bloqueantes (8 graves)                 ~3-4 días
   │    Paso: los 8 casos de aceptación pasan en el navegador y existe una prueba automática de cada uno
   ▼
Fase 1  Quitar o conectar lo que no existe      ~1-2 días
   │    Paso: ningún botón, interruptor ni campo de la interfaz queda sin función
   ▼
Fase 2  Coherencia, formatos y validaciones     ~3-4 días
   │    Paso: el recorrido de auditoría por los 5 roles no deja «—» falsos, fechas técnicas ni mensajes con nombres de campo
   ▼
Fase 3  Capacidad empresarial                   ~8-12 días (antes de la defensa solo si sobra tiempo)
        Paso: tasa de cambio, cantidades decimales y recepciones múltiples con pruebas en MariaDB
```

---

## Cómo trabajar

- **Rama:** partir de `integracion/inventario-compras-notas` (commit `fb3744c`) y hacer **un commit por hallazgo**, con su número (por ejemplo, `F0-3: solicitud manual de reposición`).
- **Entorno:** probar solo en el laboratorio aislado (5099) o en uno nuevo. **No tocar 5001 ni 5011**, y no hacer push sin acuerdo.
- **Pruebas:**
  - cada corrección lleva su prueba en `scripts/test_*.py`;
  - si toca el navegador, lleva además un caso Playwright en `e2e/specs/`;
  - los scripts de la auditoría, que ya recorren estos flujos, están en [`docs/auditoria-20261031/`](auditoria-20261031/) junto con los 58 hallazgos (`HALLAZGOS.md`).
- **Base de datos:** si una corrección cambia la base, va en una migración Alembic nueva (`0015_…`), y se actualiza `HEAD` en `docker_support/bootstrap.py`.
- **Datos de prueba:** el laboratorio ya tiene usuarios `aud_admin`, `aud_admin2`, `aud_asistente`, `aud_inventario`, `aud_vendedor` y `aud_soporte_ti` y datos `AUD-*` para reproducir cada caso. La contraseña se comparte aparte; en un laboratorio nuevo se crean con `docs/auditoria-20261031/scripts/seed_users.py`.

---

## Fase 0 — Los 8 bloqueantes

Un jurado los encuentra en una demostración de 10 minutos. Van primero.

| # | Problema verificado | Dónde está | Qué hacer | Cómo comprobarlo |
|---|---|---|---|---|
| F0-1 | Crear un producto con precio 99999999999999 o con nombre de 300 caracteres da **error 500**, y la pantalla dice "No se pudieron cargar los productos". | `app/services/product_service.py` (validación); `app/models/product.py` (`name` es `String(150)` y `sale_price` es `Numeric(12,2)`); `_product_drawer.html` | Validar en el servidor el largo del nombre (≤150) y el tope de los montos (≤ 9.999.999.999,99) antes de guardar. Agregar `maxlength` y `max` a los campos. En el JS, mostrar el `error` que devuelve la API y no el mensaje genérico. | Los dos casos responden **400** con un mensaje claro y el panel sigue abierto. |
| F0-2 | El precio **1.999 se guarda como 2.00** sin avisar. | `_non_negative_price` en `product_service.py` y los demás lectores de montos | Rechazar los montos con más de 2 decimales (400 "Use como máximo 2 decimales"), igual que ya lo hace la nota de entrega. | 1.999 responde 400; 1.99 se guarda. |
| F0-3 | **No se puede iniciar una compra** desde la interfaz si no hay pronóstico: "Generar solicitud" queda deshabilitado y Solicitudes no tiene "Nueva". | `templates/purchases/requests.html`, `static/js/ix_replenishment_requests.js`; la API ya existe: `POST /api/replenishment-requests` (`routes/tutor_cycle.py`) | Agregar el botón **"Nueva solicitud"** para el rol Inventario: un modal con producto (sin servicios ni inactivos), cantidad entera mayor que 0 y nota opcional. Reutilizar la API. | Con la base recién instalada, Inventario crea la solicitud y el Asistente la ve "Abierta". |
| F0-4 | Recepción: con 250 cuando se pidieron 200, dice **"Llega menos de lo pedido"** y bloquea Guardar sin explicar. | `static/js/ix_receptions.js` (cálculo del estado y del bloque "Cierre definitivo") | Separar los tres casos: **excede** (error en la fila: "Máximo 200 pendientes"), **parcial** (cierre con faltante) y **completa**. Siempre que Guardar esté deshabilitado, decir por qué. | 250 muestra el error en la fila; 120 pide el cierre; 200 queda "Completa". |
| F0-5 | **Las notas de entrega no muestran la moneda** en la lista, el detalle, los KPI ni el PDF ("Total general: 75,00"). Lo mismo pasa en el catálogo y el chatbot. | `services/delivery_note_pdf_service.py:342` (`total_label`), `static/js/delivery_notes.js`, `static/js/catalog.js`, `services/chatbot_service.py` | Mostrar el monto **con su moneda en todas partes** ("USD 75,00" o "$ 75,00"). Usar un solo formateador de dinero en JS y otro en Python que reciban monto y moneda. | Ningún monto del sistema aparece sin moneda; el PDF dice "Total general: USD 75,00". |
| F0-6 | **El servicio E2E FLETE aparece como mercancía agotada** en el Inicio, en "Productos críticos" (recomienda comprarlo), en notificaciones duplicadas, en el catálogo y en el chatbot. | `Product.stock_status` en `app/models/product.py:64`; filtros en `notification_service.py`, `predictive_*_service.py`, `dashboard`, `catalog`, `chatbot_service.py` | Que `stock_status` devuelva **"servicio"** cuando `is_service` es verdadero. Excluir los servicios de alertas, KPI de stock, productos críticos, notificaciones y del selector de producto en Movimientos. | El flete deja de aparecer en alertas y críticos, y el catálogo dice "Servicio". |
| F0-7 | **Proveedores acepta RIF "12345" y teléfono "no tiene".** Clientes valida ambos y Perfil acepta "abc". | `services/supplier_service.py` (`validated`), `services/profile_service.py`; el validador bueno es `normalize_tax_id` en `customer_service.py` | Crear `app/utils/validators.py` con `rif()`, `phone()` y `email()` y usarlo en clientes, proveedores, perfil y usuarios. Revisar en la base los proveedores con datos inválidos (en el laboratorio, los ids 3 y 5). | Los mismos casos se rechazan con el mismo mensaje en las 4 pantallas. |
| F0-8 | **El "—" por permisos**: el rol Inventario ve "Precio —" en todos los productos, y "—" en Costo, Importe y Total de compras y recepciones, aunque los datos existen. | `static/js/ix_products_live.js`, `ix_purchases.js`, `ix_receptions.js`, `delivery_notes.js` (KPI) | Si el rol no puede ver un dato, **ocultar la columna o el campo**, como ya hace el catálogo. Reservar "—" para "no hay dato". En los KPI sin notas, mostrar "USD 0,00". | Con Inventario no queda ningún "Precio —"; con Admin se ven los valores. |

---

## Fase 1 — Quitar o conectar lo que no existe

Todo lo que la interfaz muestra debe funcionar. Lo que no funciona se quita.

**Quitar:**

- **Configuración → General:**
  - el panel "Unidades y conversiones" (todo dice "No disponible");
  - los 4 interruptores "Ajuste global no implementado" (estacionalidad, outliers, redondeo, impuestos);
  - "Actualización automática de modelos";
  - el bloque "Actividad reciente" con "Ver historial completo" inerte;
  - archivo: `templates/settings/live/_general.html`.
- **Detalle de movimiento:** las líneas "Subcategoría: no disponible" y "Código de barras: no disponible" (`ix_movements_live_layers.js:54`). Esos campos no existen en el modelo.
- **Inicio del administrador** (`templates/dashboard.html:119-120`):
  - "Registrar venta" (el módulo Ventas está retirado);
  - "Registrar compra": conectarlo a la nueva solicitud de la Fase 0 (F0-3) o quitarlo;
  - "Nuevo producto": mostrarlo solo al Asistente, que es el único rol que puede crear productos.
- **Conciliación:** el texto "En DEMO, use «Simular corte»" cuando el botón no existe. Mientras la programación automática esté desactivada, mostrar los horarios 08:00, 12:00, 16:00 y 18:30 como "referencia", no como si estuvieran programados.
- **Reportes de ventas:**
  - la pestaña "Métodos de pago" y el "Ticket promedio", si no hay ventas con pago;
  - el título debe decir que la fuente son las **notas de entrega**.
- **Diagnóstico de historial** (`/predictions`):
  - el aviso "Aún no se generan pronósticos" (es falso);
  - de la tabla, los productos rechazados o pendientes de alta.
- **Integraciones:** no mostrarle a un usuario de negocio los nombres de variables de entorno (`MAIL_PASSWORD`, …). Basta con "Configurado / No configurado".
- **Producto:**
  - la unidad duplicada "Unidad"/"unidad";
  - el campo "Estado", que parece editable pero se calcula solo: mostrarlo como etiqueta;
  - unificar el tamaño máximo de imagen (el alta acepta 5 MB, la modificación 2 MB).

**Conectar o aclarar:**

- **"Agregar proveedor" dentro de "Proponer modificación"** se guarda al instante, sin aprobación, y luego "Enviar propuesta" da 400 "sin cambios". Hay dos opciones: que la asociación forme parte de la propuesta, o sacarla a un botón propio, "Gestionar proveedores". Recomiendo la segunda.
- **El formulario de alta de producto** descarta en silencio el proveedor si falta el costo. Debe marcar el error en el bloque del proveedor y no guardar.
- **La cotización** se muestra aunque no haya ningún proveedor válido. En ese caso, mostrar solo el aviso con un enlace para asociar un proveedor al producto. Además, la etiqueta "Precio de … ( / Bs. / )" está partida en 3 líneas.

---

## Fase 2 — Coherencia, formatos y validaciones

**Formatos únicos** (un formateador compartido en `static/js`, por ejemplo `ix_format.js`, y su equivalente en Python):

| Qué | Hoy se ve | Debe verse |
|---|---|---|
| Fecha y hora | `2026-10-31T07:04:01-04:00`, `2026-10-31 11:46:34 UTC`, `31/10/26, 12:00:00 p. m.`, `06:07 a. m..` | `31/10/2026 07:04 a. m.` (hora de Caracas) |
| Fecha | `2026-10-31` (orden, recepción, decisión de proveedor) | `31/10/2026` |
| Estados | `COR-2 · pending`, `approved`, tipo `entrada` | "Pendiente", "Aprobada", "Entrada" |
| Referencias | `MOV-16`, `REC-00001` | `MOV-000016`, `REC-000001` (6 dígitos, como OC, SR y NE) |
| Montos | `3,75`, `$ 3.75`, `12,50` | Siempre con moneda y el mismo separador |
| Unidades | "200 unidades" en un producto en metros | La unidad del producto ("200 m") |

**Validaciones y mensajes:**

- **Mensajes técnicos:** traducir en un solo lugar los nombres de campo que hoy llegan al usuario: `'sale_price_currency'`, `'current_stock'`, `'email'`, `username`, `name: texto requerido…`. Opción: un diccionario campo → etiqueta en el `ValidationError`.
- **Validación en el formulario:**
  - producto: marcar como obligatorios la moneda de venta y la de costo (ya tienen asterisco), y rechazar negativos y decimales antes de enviar;
  - siempre que un botón quede deshabilitado, mostrar el motivo.
- **Salida de inventario:** hacer **obligatorio el motivo** (hoy una pérdida de stock se registra sin justificación).
- **Contraseñas:** subir el mínimo a 8 caracteres, con al menos una letra y un número.
- **Fecha de recepción:** si solo se permite hoy, mostrarla como texto y no como un campo editable.

**Datos maestros:**

- **Cliente:** agregar **Dirección** al maestro (migración nueva) y copiarla a la nota de entrega al elegir el cliente.
- **Movimiento de reversa:** al anular una nota, copiar el motivo escrito por el administrador al movimiento.
- **Indicadores:** en Panorama, "productos activos" y "productos totales" deben salir de la misma consulta (hoy dicen 4 y 3).

**Visual:**

- El fondo gris del menú lateral se corta a 900 px en las páginas largas (catálogo, conciliación, diagnóstico).
- La casilla "Es un servicio" se dibuja como un cuadro blanco grande separado de su texto.
- Los indicadores aparecen truncados ("Productos con hi…") y la tabla de /predictions se corta por la derecha.
- Los errores del modal de cliente se ven en gris, como si fueran ayuda: deben verse en rojo.

---

## Notas de entrega — pendientes de la revisión anterior

Una revisión anterior de Notas propuso 8 mejoras. Verificadas en código y navegador el 31/10/2026:

- **Ya resueltas** (no hay que tocarlas):
  - enlace de la nota con el cliente (`customer_id` opcional y copia de sus datos);
  - motivo obligatorio al cancelar, y solo cancela quien tiene el permiso;
  - clave única por envío contra notas duplicadas (probada en MariaDB con envíos simultáneos);
  - paginación de la pantalla de notas, con el creador traído en la misma consulta.

**Lo que falta:**

| # | Pendiente | Dónde está | Qué hacer | Cómo comprobarlo | Esfuerzo (estimado) |
|---|---|---|---|---|---|
| N-1 | **Moneda de la nota.** La migración 0008 está aplicada y la columna `delivery_notes.currency` existe, pero el servidor rechaza todo producto con precio en bolívares ("solo admite precios USD explícitos") y no guarda la moneda. | `services/delivery_note_service.py:249`; `models/delivery_note.py` (`currency` con su CHECK) | Al emitir, tomar la moneda de los productos y guardarla en `note.currency`. Aceptar VES. Rechazar una nota que mezcle monedas, con un mensaje claro. Mostrar la moneda en la lista, el detalle, los KPI y el PDF (une con F0-5). Las notas antiguas sin moneda se completan como USD en una migración. | Se emite una nota solo con productos en VES; una nota mixta USD + VES responde 400; el PDF dice "Total general: Bs. …". | 1 día (sin tasa); con tasa de cambio, ver Fase 3 |
| N-2 | **Devoluciones parciales.** Hoy la única corrección es cancelar la nota entera y emitir otra. Eso cambia el número de la nota y distorsiona la demanda que usan los pronósticos. | `services/delivery_note_service.py`, `routes/delivery_notes.py`, `static/js/delivery_notes.js`; nueva tabla de devoluciones | Agregar **"Registrar devolución"** a una nota emitida: líneas y cantidades devueltas (cada una ≤ lo entregado menos lo ya devuelto), motivo obligatorio y número propio (`DEV-000001`). Cada línea genera una **entrada** de inventario enlazada a la nota, y la nota conserva su número. Para el pronóstico, la demanda es lo entregado menos lo devuelto. | Nota de 10 → devolución de 3 → stock +3, la nota muestra "Devuelto 3 de 10" y una segunda devolución de 8 se rechaza. | 2-3 días |
| N-3 | **Plazo máximo para cancelar.** Hoy una nota de cualquier antigüedad se cancela entera. | `cancel_note` en `services/delivery_note_service.py` | Plazo configurable (por ejemplo 30 días). Pasado el plazo, se rechaza la cancelación y se ofrece la devolución (N-2). | Una nota de 31 días responde 409 con "use una devolución". | 0,5 día |
| N-4 | **Dígito verificador del RIF.** Hoy solo se valida el formato; el propio código dice "no se verifica el dígito de control en v1". | `customer_service.py` (`normalize_tax_id`), que pasará al `app/utils/validators.py` de F0-7 | Implementar el cálculo de módulo 11 del SENIAT en `validators.rif()`. **Antes de activarlo**, comprobarlo con RIF reales conocidos de proveedores y clientes. Usarlo en clientes, proveedores y en la configuración de la empresa. | Un RIF real válido se acepta; el mismo RIF con el último dígito cambiado se rechaza. | 0,5 día |
| N-5 | **Lista completa sin paginar en la API.** `GET /api/delivery-notes` sin `page` ni `per_page` devuelve **todas** las notas (`list_notes`). | `controllers/delivery_note_controller.py:25` | Paginar siempre, con valor por defecto de `per_page`, o poner un tope (por ejemplo 500) y avisar si se excede. | La llamada sin parámetros devuelve como máximo una página. | 0,5 día |
| N-6 | **Datos del cliente y de la cancelación.** | Ya listados en la Fase 2 (Datos maestros) | Agregar Dirección al cliente y copiarla a la nota. Copiar el motivo de la cancelación al movimiento que devuelve el stock. | — | Incluido en la Fase 2 |
| N-7 | **Pruebas que faltan.** Hay **8 scripts** que necesitan el servidor encendido: `test_delivery_notes.py`, `test_catalog.py`, `test_chatbot.py`, `test_inventory.py`, `test_images_profile.py`, `test_operational_separation_migration.py` y otros 2 que usan `requests`. | `scripts/` | Pasarlos a pruebas aisladas en el mismo proceso (`IsolatedCase` en `scripts/approval_test_support.py`), sin servidor. Agregar tres pruebas: **(a)** borrar un cliente con cotizaciones responde 409 (el código ya lo impide, pero no hay prueba); **(b)** frontera de las **8:00 p. m.** de Caracas (00:00 UTC): una nota de las 8:30 p. m. debe contar en el día local correcto, en los filtros "desde/hasta" y en los KPI diarios; **(c)** se mantiene la prueba existente de notas simultáneas. | Todas las suites de Notas corren sin servidor y pasan. | 1-1,5 días |

---

## Compras — revisión anterior (verificada, no hay que rehacerla)

Una revisión anterior de Compras señaló 6 fallas y 4 refuerzos. El 31/10/2026 todos se reprodujeron en el laboratorio contra la API real, y **todos están corregidos**:

| Punto de la revisión | Resultado en el laboratorio |
|---|---|
| Una cotización de 1 unidad respaldaba una orden de 10 | Preparar la orden responde **400**: "La cotización debe cubrir los productos y las cantidades exactas de la solicitud". Al aprobar, también se compara cantidad por cantidad (`order_mismatch`). |
| Renombrar el proveedor cambiaba las órdenes antiguas | Después de aprobar el renombre, OC-000004 sigue mostrando el nombre original en la impresión y en la API: se guarda una copia del proveedor al aprobar la orden (`supplier_snapshot`). |
| Las modificaciones de proveedores no salían en "pendientes" | El filtro de pendientes y la bandeja de aprobaciones muestran al proveedor con el cambio propuesto. |
| No había forma autorizada de desactivar un proveedor | Existe la propuesta de cambio de estado (`/status-proposals`): exige motivo y la aprobación de un administrador. |
| Se aceptaban solicitudes de servicios | La solicitud se rechaza desde el inicio: "Los servicios no admiten solicitudes de reposición". |
| Se aceptaba una recepción anterior a la aprobación | Una recepción con fecha anterior a la aprobación o posterior a hoy responde 400. |
| Refuerzo: bandeja única de pendientes | Hecha: `/purchases/approvals` reúne órdenes, cambios de proveedor, cierres y correcciones, con responsable y antigüedad. |
| Refuerzo: correcciones de recepción | Hechas: piden aprobación y generan movimientos compensatorios enlazados (por ejemplo, COR-1 → MOV-3). |
| Refuerzo: importes por etapa | Hechos: "Importes por etapa" separa solicitado, aprobado, recibido y no suministrado. |
| Refuerzo: límites y concurrencia | Hechos: todas las exportaciones tienen tope de 10.000 filas, y hay una prueba de concurrencia de asociaciones producto–proveedor con orden común de bloqueos. |

**Restos menores** (se suman a la Fase 2):

- **Indicador principal de Compras:** el KPI "Valor solicitado en órdenes" todavía suma borradores sin aprobar y el total completo de órdenes cerradas con faltante. Hay dos opciones:
  - renombrarlo "Valor solicitado (incluye borradores)";
  - mostrar arriba el "Aprobado neto" (aprobado menos no suministrado).
- **Fecha de recepción:** el servidor acepta cualquier fecha entre la aprobación y hoy, pero el formulario solo deja hoy (C9). Hay que decidir una de las dos reglas. Si se permiten fechas pasadas, el detalle debe distinguir la "fecha física de llegada" de la "fecha de registro".
- **El "—" por permisos** en los importes que ve el Inventario ya está cubierto en F0-8.

---

## Reportes de ventas — revisión anterior

Una revisión anterior de Reportes de ventas señaló 5 puntos. Verificados en el código el 31/10/2026:

- **3 corregidos:**
  - **"Ticket promedio" con tres significados:** ahora cada pestaña tiene su etiqueta ("Ticket promedio", "Ingreso prom. por producto", "Ticket promedio por venta"), y en Métodos el promedio se pondera por transacciones (`sales_report_service.py:312`).
  - **"Ver detalle" de categoría:** ahora filtra por el id de la categoría (`report_controller.py:307`), ya no por texto libre.
  - **"Ingresos cobrados" sumaba "Por cobrar":** el indicador se llama ahora "Ventas registradas" y ya no dice que lo vendido esté cobrado.

**Lo que falta:**

| # | Pendiente | Dónde está | Qué hacer | Cómo comprobarlo | Esfuerzo (estimado) |
|---|---|---|---|---|---|
| RV-1 | **El aviso de moneda se pierde en el CSV.** El PDF ya incluye "Importes sin moneda de origen registrada…" y quita el "$", pero el CSV no lo trae. Además, el Inicio ("Ventas de hoy $ 0.00") y Panorama ("Ventas ($)", "Semana anterior ($)") muestran "$" para esos mismos importes. | `services/sales_report_export_service.py:394-397` (rama CSV); `static/js/dashboard.js:41` (`fmtMoney`) y `:338` (leyendas del gráfico) | Agregar el aviso como primera fila o encabezado del CSV. En el Inicio y en Panorama, quitar el "$" de los importes de ventas, o usar la moneda real cuando exista (une con F0-5 y N-1). | El CSV abre con el aviso; ninguna cifra de ventas muestra "$" sin moneda registrada. | 0,5 día |
| RV-2 | **La categoría de una venta no es la del momento de vender.** Si un producto cambia de categoría, sus ventas anteriores pasan a contar en la nueva. La pantalla lo avisa, pero el dato no se conserva: ni `sale_lines` ni `delivery_note_items` guardan la categoría. | `models/sale_line.py`, `models/delivery_note_item.py`, `services/sales_report_service.py:131,157` (unen con `Product.category_id`) | Agregar `category_id` y `category_name` a las líneas de nota (y de venta, si se conservan), llenarlos al emitir y completar los registros viejos con la categoría actual, marcándolos como aproximados. Los reportes deben agrupar por la columna guardada. Si no se hace, dejar el aviso y declararlo en el alcance. | Cambiar de categoría un producto vendido no mueve sus ventas anteriores en "Ventas por categoría". | 1 día |

Nota: las líneas de nota guardan la cantidad como `Numeric(12,2)` y el stock es entero. Si se pasa a cantidades decimales (Fase 3), conviene alinear ambos tipos.

---

## Integridad del kardex, costo y documentación

Cuatro puntos más, verificados en el código el 31/10/2026:

- **Lo que ya protege el sistema:**
  - cada movimiento cuadra por dentro, porque la base exige que el stock nuevo sea el anterior ± la cantidad (CHECK `ck_movement_arithmetic`);
  - los movimientos se registran con bloqueo y siempre en el mismo orden (`inventory_service.lock_products`).
- **Lo que no vigila nadie:** la **cadena** completa: que cada movimiento empiece donde terminó el anterior, y que el stock del producto sea el resultado del último movimiento. Hoy un descuadre por un cambio directo en la base o una restauración incompleta pasaría inadvertido.

| # | Pendiente | Dónde está | Qué hacer | Cómo comprobarlo | Esfuerzo (estimado) |
|---|---|---|---|---|---|
| K-1 | **Verificación periódica del kardex.** No existe ninguna tarea que compruebe la cadena de movimientos. | El planificador ya existe (`docker_support/scheduler.py`, servicio `scheduler` en `compose.yaml`); las alertas, en `notification_service.py` | Crear `inventory_integrity_service.check()` con 3 chequeos: **(a)** el stock actual es igual al stock final del último movimiento; **(b)** el stock anterior de cada movimiento es igual al stock final del movimiento previo de ese producto; **(c)** ningún producto tiene stock distinto de 0 sin movimientos. Programarla a diario en el planificador. Si encuentra algo, crear una notificación para los administradores y un registro con el detalle (producto, movimiento, valor esperado y encontrado). Exponer un botón "Verificar integridad" en Movimientos y un comando para correrla después de restaurar un respaldo. | Con datos sanos no hay alertas. Si se cambia en una base de prueba el `current_stock` de un producto, aparecen la alerta y el detalle. En el laboratorio, las 3 consultas dieron **0 descuadres** en 16 movimientos. | 1-1,5 días |
| K-2 | **Costo por movimiento.** `stock_movements` no guarda costo unitario: no hay kardex valorizado ni valor histórico del inventario, y el "Valor del inventario" usa el costo de referencia escrito a mano. | `models/stock_movement.py`; recepciones en `purchase_service.py`; salidas en `inventory_service.py` y `delivery_note_service.py` | Agregar `unit_cost` y `cost_currency` al movimiento (migración nueva). **Entradas por recepción:** costo de la línea de la orden. **Entradas manuales:** costo obligatorio o costo promedio vigente. **Salidas y notas:** costo promedio ponderado del producto en ese momento. Mantener el costo promedio por producto y por moneda (sin convertir hasta que exista la tasa de cambio de la Fase 3). Agregar el reporte "Kardex valorizado" (fecha, movimiento, cantidad, costo, saldo en unidades y en valor). | La recepción de 120 a USD 2,45 deja el movimiento con costo 2,45. El valor del inventario sale del kardex y no del costo de referencia. | 3-4 días |
| K-3 | **Cantidades decimales.** Al cancelar ya **no** se recorta con `int()`: desde `delivery_note_service.py:436` una cantidad no entera se rechaza con un conflicto. Sigue la inconsistencia de tipos: `delivery_note_items.quantity` es `Numeric(12,2)` y el stock, los movimientos y las compras son enteros. | Ver la fila "Unidades y cantidades decimales" de la Fase 3 | Resolverlo junto con esa fila: o todo entero (y la línea de nota pasa a `Integer` con una migración), o todo decimal `Numeric(12,3)`. No dejar tipos mezclados. | El mismo tipo de cantidad en productos, movimientos, notas y compras. | Incluido en la Fase 3 |
| K-4 | **Documentación desactualizada.** `AGENTS.md` (líneas 8 y 112) y `README.md` (líneas 13 y 39) dicen "MySQL 8", pero `compose.yaml` usa **MariaDB 10.11** y los cortes y la conciliación exigen MariaDB (fallan a propósito en MySQL). | `AGENTS.md`, `README.md` y el capítulo de tecnología de la tesis | Cambiar a "MariaDB 10.11" y explicar por qué no MySQL (los cortes dependen de funciones de MariaDB). Revisar que el documento de la tesis diga lo mismo. | Ninguna mención a MySQL 8 como base soportada. | 0,5 hora |

---

## Fase 3 — Capacidad empresarial

Esto es lo que separa un prototipo de tesis de un sistema que una ferretería pueda usar. Si no entra antes de la defensa, se declara en el alcance.

| Mejora | Por qué | Propuesta concreta | Esfuerzo (estimado) | ¿Antes de la defensa? |
|---|---|---|---|---|
| **Tasa de cambio Bs/$** | En Venezuela se cotiza en $ y se cobra en Bs a la tasa del día. Hoy USD y VES nunca se convierten. | Tabla `exchange_rates` (fecha, tasa, fuente BCV/manual, registrada por). La carga el Admin a diario. La nota guarda moneda y la **tasa usada** (foto de ese momento) y muestra "USD 75,00 · Bs X a tasa Y". Los reportes suman en USD con la equivalencia. | 3-4 días | Recomendada: es la pregunta más probable del jurado. |
| **Unidades y cantidades decimales** | No se pueden vender 2,5 m de cable ni 1,5 kg de clavos. Todo es entero. | **Antes de la defensa:** dejar solo unidades contables (Unidad, Caja, Paquete, Rollo, Juego) y quitar Metro y Kilogramo. **Después:** pasar las cantidades a `Numeric(12,3)` en productos, movimientos, notas y órdenes, ajustar los CHECK, los formularios y el pronóstico, y agregar un factor de conversión compra→venta (caja de 100 → unidad). | 1 hora (restringir) / 5-7 días (decimales) | Restringir: sí. Decimales: declarar. |
| **Recepciones parciales múltiples** | Hoy una recepción parcial obliga a cerrar la orden con faltante. Los proveedores suelen enviar por partes. | Permitir varias recepciones mientras quede pendiente. El cierre con faltante pasa a ser una acción aparte ("Cerrar con faltante"). | 2-3 días | Declarar (la regla actual es deliberada, ver `docs/PURCHASE_INTEGRITY.md`). |
| **Ajuste desde conciliación** | Un conteo físico nunca corrige el stock desde Conciliación: no hay ninguna fuente acreditada. | Acreditar la fuente "Conteo físico manual" en `RECONCILIATION_ACCREDITED_SOURCES`. El ajuste sigue requiriendo la aprobación de un segundo administrador, como ya está programado. | 0,5-1 día | Sí, porque cierra el ciclo de inventario. |
| **Acceso del rol Inventario a Conciliación** | El almacén es quien cuenta, pero hoy no ve Cortes y conciliaciones. | Darle permiso para cargar y previsualizar. Confirmar y ajustar quedan para el Admin. | 0,5 día | Sí. |
| **Operación** | Para que alguien lo use cada día. | Procedimiento escrito de respaldo y restauración (ya probado en el laboratorio), despliegue y actualización. Unificar las líneas 5001 y 5011 en una sola. | 1-2 días | Sí, en anexo. |
| **Pruebas** | Que "las pruebas pasan" sea verdad. | Arreglar o retirar las 43 suites obsoletas, actualizar `docker_support/verify.py` y convertir los recorridos de esta auditoría en specs Playwright por rol. | 2-3 días | Sí, al menos las suites. |

---

## Qué declarar en el alcance de la tesis

Texto sugerido para "Alcance y limitaciones". Ajústalo a tu redacción:

> El sistema registra cantidades enteras; no gestiona fracciones de unidad (metros, kilogramos) ni conversión entre unidades de compra y de venta. Los montos se registran en USD o VES según el producto; el sistema no aplica tasa de cambio entre monedas (*si no se implementa la Fase 3*). Cada orden de compra admite una única recepción; una entrega incompleta cierra la orden y el faltante se repone con una nueva solicitud. La nota de entrega es un documento interno no fiscal y no sustituye a la factura; su corrección se hace por cancelación completa (*si no se implementa la devolución parcial N-2*). El valor del inventario se calcula con el costo de referencia de cada producto y no con un kardex valorizado (*si no se implementa K-2*). La programación automática de cortes requiere configuración del servidor y el ajuste por conciliación requiere aprobación de un segundo administrador.

---

## Checklist antes de la defensa

- [ ] Fase 0 completa: los 8 casos de aceptación pasan en el navegador.
- [ ] Fase 1 completa: ningún botón, interruptor ni campo sin función.
- [ ] Fase 2 completa: ningún "—" falso, ninguna fecha técnica ni mensaje con nombre de campo en el recorrido por los 5 roles.
- [ ] Notas de entrega: moneda guardada y visible (N-1), plazo de cancelación (N-3), dígito del RIF (N-4), API paginada (N-5) y pruebas sin servidor (N-7).
- [ ] Devoluciones parciales (N-2) implementadas, o declaradas en el alcance.
- [ ] Reportes de ventas: aviso de moneda en el CSV y sin "$" en el Inicio y Panorama (RV-1); categoría guardada al vender (RV-2), o declarada en el alcance.
- [ ] Verificación diaria del kardex con alerta (K-1) funcionando, y ejecutada después de restaurar el respaldo.
- [ ] Costo por movimiento y kardex valorizado (K-2) implementados, o declarados en el alcance.
- [ ] Un solo tipo de cantidad en todo el sistema (K-3) y la documentación dice MariaDB 10.11 (K-4).
- [ ] Tasa de cambio implementada, o declarada en el alcance.
- [ ] Unidades restringidas a contables, o decimales implementados.
- [ ] Ajuste desde conciliación habilitado con aprobación del segundo administrador.
- [ ] Suites de prueba en verde (SQLite y MariaDB), con los fallos restantes documentados.
- [ ] Instalación desde cero (0001→HEAD) y restauración de respaldo probadas de nuevo.
- [ ] Base de demostración limpia y guion de demo del núcleo: producto → movimiento → solicitud → compra → recepción → nota → pronóstico.
- [ ] Recorrido de auditoría repetido con los 5 roles y capturas guardadas para el anexo.
