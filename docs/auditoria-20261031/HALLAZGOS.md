# Hallazgos de la auditoría en navegador (lab 5099)

## Productos
- P1 ALTA: precio 99999999999999 → HTTP 500 (DataError 1264); UI dice "No se pudieron cargar los productos". [04_asistente_np_precio_enorme]
- P2 ALTA: nombre 300 caracteres → HTTP 500; el input no tiene maxlength. [05_asistente_np_nombre_300_chars]
- P3 MEDIA: precio 1.999 se guarda como 2.00 sin aviso (redondeo silencioso).
- P4 MEDIA: mensajes con nombres técnicos: 'sale_price_currency', 'sale_price', 'current_stock', 'minimum_stock'.
- P5 MEDIA: validación del cliente solo cubre 4 campos; moneda (marcada *) y números negativos/decimales solo los rechaza el servidor.
- P6 MEDIA: servicio (E2E FLETE) se muestra "Agotado" en lista, "Sin existencia · 0 unidad" en catálogo y cuenta en KPI "Sin stock".
- P7 MEDIA: catálogo muestra precio "3,75" sin moneda y con coma; la lista muestra "$ 3.75". Formato incoherente.
- P8 BAJA: unidad "Metro"/"Kilogramo" ofrecida pero cantidades solo enteras; mayúsculas inconsistentes ("Metro" vs "unidad").
- P9 BAJA: casilla "Es un servicio" se dibuja como un cuadro blanco grande separado de su texto.
- P10 BAJA: campo "Estado" parece editable (select) pero está deshabilitado y se deriva del stock.
- P11 BAJA: en la revisión, la fila Imagen muestra la ruta "/media/products/…png" en vez de miniatura; "Sin alta aprobada" repetido 15 veces.
- P12 BAJA: proveedor del formulario de alta: si falta el costo, el proveedor no se agrega y el producto se guarda sin él.

## Proveedores
- S1 MEDIA: "Fecha de decisión" en detalle de proveedor sale cruda en ISO UTC "2026-10-31T04:37:35".

## Movimientos
- M1 MEDIA: detalle muestra "Subcategoría: no disponible" y "Código de barras: no disponible": campos que no existen en el modelo (restos del diseño de referencia).
- M2 MEDIA: detalle llama "Stock actual" al stock resultante de ese movimiento (175) aunque el stock real hoy es 169. Debe decir "Stock resultante".
- M3 MEDIA: una Salida se registra sin motivo (campo opcional). Una pérdida de stock sin justificación.
- M4 BAJA: el selector de producto ofrece servicios (E2E FLETE) para entradas/salidas; luego el servidor rechaza.
- M5 BAJA: "Fecha *" marcada obligatoria pero no editable ("Asignada al guardar"); "Responsable *" select de una sola opción deshabilitado.
- M6 BAJA: texto "06:07 a. m.." con doble punto en el historial.
- M7 BAJA: el motivo escrito al anular una nota no se copia al movimiento de reversa (queda "Cancelación de nota de entrega #NE-000002").
- OK: entradas/salidas/ajuste, stock insuficiente, servicio rechazado con mensaje claro, trazabilidad correcta en lista y detalle.

## Clientes y Notas de entrega
- N1 ALTA: ningún monto de Notas indica moneda: lista, detalle, KPIs y PDF ("Total general: 75,00"). Solo la confirmación previa dice "Total (USD)".
- N2 MEDIA: KPIs "Monto total emitido" y "Promedio por nota" muestran "—" cuando no hay notas emitidas (debería ser 0,00 USD).
- N3 MEDIA: el maestro de clientes no tiene Dirección, pero la nota la pide; nunca se copia y hay que escribirla cada vez.
- N4 BAJA: PDF corta el código "AUD-CAB-VI6 / 0" en dos líneas; no muestra unidad (10 ¿metros?).
- N5 BAJA: errores del modal de cliente en gris (parece ayuda, no error); asterisco de Nombre en línea aparte.
- OK: RIF, correo, teléfono, duplicado por RIF, stock insuficiente, línea duplicada, motivo de anulación (≥5), devolución de stock, PDF generado, vendedor no ve "Cancelar".

## Compras
- C1 ALTA: no hay forma en la interfaz de crear una solicitud de reposición si no hay pronóstico (botón "Generar solicitud" deshabilitado; Solicitudes no tiene "Nueva"). En un sistema recién instalado o con productos nuevos el ciclo de compras no puede iniciarse. (Se creó SR-000004 por API para continuar.)
- C2 ALTA: Recepción: al escribir 250 (más que lo pedido, 200) el formulario dice "Parcial: cierre definitivo con faltante / Llega menos de lo pedido" y deshabilita Guardar sin explicar el motivo.
- C3 MEDIA: regla de recepción única: cualquier recepción parcial exige cerrar la orden con faltante; no se puede recibir el resto después. Es deliberada (docs/PURCHASE_INTEGRITY.md) pero la UI muestra columna "Pendiente" y permite escribir "el proveedor envía el resto la próxima semana". Debe declararse en el alcance.
- C4 MEDIA: Rol Inventario ve "Monto recibido —", "Valor total recibido —", costo unitario y subtotal vacíos: el dato existe pero está oculto por permiso y se presenta igual que "sin dato".
- C5 MEDIA: detalle de recepción con datos crudos: "Creada: 2026-10-31T07:04:01-04:00", "2026-10-31T07:11:04 UTC", "Fecha recepción 2026-10-31", estados "COR-2 · pending", "COR-1 · approved" en inglés, tipo "entrada/salida" en minúscula, referencias "MOV-16" vs "MOV-000016" en el resto del sistema.
- C6 MEDIA: "Agregar proveedor" dentro de "Proponer modificación" se guarda al instante sin aprobación; luego "Enviar propuesta" responde 400 "La propuesta no contiene cambios".
- C7 MEDIA: cotización: etiqueta "Precio de Cable THW 12 AWG VI60 ( / Bs. / )" partida en 3 líneas; el formulario se muestra aunque no hay proveedor válido (select vacío).
- C8 BAJA: OC muestra "200 unidades" aunque el producto es en Metro; "Fecha de orden 2026-10-31" en ISO; "Entrega estimada: Sin fecha" aunque el proveedor tiene lead time 5 días.
- C9 BAJA: fecha de recepción editable pero solo acepta hoy ("Value must be 10/31/2026").
- C10 BAJA: numeraciones con distinto ancho: REC-00001 (5) vs OC-000001, SR-000004, NE-000002, MOV-000008 (6).
- C11 BAJA: bandeja de Solicitudes del Inventario vacía sin indicar dónde se crean las solicitudes.
- C12 BAJA: imagen de producto: alta acepta 5 MB, modificación 2 MB; el formulario de modificación no tiene "Es un servicio"; unidad duplicada "Unidad"/"unidad".
- OK: cotización con validación nativa (fecha pasada, precio 0), aprobación admin desde la bandeja abre el detalle correcto, recepción 120 → stock 169→289, OC "Cerrada parcial · No suministrado 80 · $196.00", montos por moneda separados para admin.

## Proveedores (cont.)
- S2 ALTA: proveedor acepta RIF "12345" y teléfono "no tiene" (Clientes sí valida ambos). Quedaron guardados (ids 3 y 5).
- S3 BAJA: error de nombre vacío dice "name: texto requerido, máximo 150 caracteres." (nombre técnico).
- S4 BAJA: pestaña Historial muestra "2026-10-31 11:46:34 UTC" (UTC crudo).
- OK: detalle del proveedor muestra los 11 campos introducidos; aprobación; pestañas Compras/Contactos/Historial.

## Categorías
- OK: alta con nombre, descripción, color e ícono; se guardan (green/droplet) y se revisan.

## Usuarios / Perfil
- U1 MEDIA: Perfil acepta teléfono "abc" ("Perfil actualizado correctamente"). Tres reglas de teléfono distintas: Clientes valida, Proveedores no, Perfil no.
- U2 BAJA: contraseña mínima 6 caracteres; mensajes técnicos "El campo 'email'", "username".
- OK: alta de usuario, correo inválido, clave corta, usuario duplicado, login del nuevo usuario.

## Configuración
- G1 MEDIA: pestaña General muestra funciones inexistentes: "Unidades y conversiones" (todo "No disponible", "Gestionar unidades" inerte), 4 interruptores "Ajuste global no implementado en esta versión", "Actualización automática de modelos: No disponible", "Actividad reciente: esta versión no registra historial" + "Ver historial completo" inerte.
- G2 BAJA: "Motor de base de datos: mysql 10.11.14" (es MariaDB). Ubicación/Moneda/Idioma/Formato parecen campos editables pero son de solo lectura.
- G3 BAJA: Integraciones lista nombres de variables de entorno (MAIL_PASSWORD, …) a un usuario de negocio.

## Conciliación
- R1 MEDIA: "No hay cortes… En DEMO, use «Simular corte»", pero no existe ese botón; la programación muestra 08:00/12:00/16:00/18:30 aunque está "Automática desactivada".
- R2 MEDIA: el ajuste desde conciliación está siempre bloqueado ("fuente no acreditada", ninguna configurada): un conteo físico nunca corrige el stock desde esta pantalla.
- R3 BAJA: el rol Inventario (almacén) no tiene acceso a Cortes y conciliaciones.
- R4 BAJA: fechas "31/10/26, 12:00:00 p. m." (año 2 dígitos, segundos) distintas al resto.
- OK: CSV con faltante, coincidencia, código inexistente y fila inválida clasificados correctamente; confirmar; modal Resolver.

## Inicio / Reportes / Predictivo / Notificaciones / Chatbot
- D1 ALTA: el servicio E2E FLETE aparece como "Agotado" en alertas de Inicio, "Crítico · Se recomienda generar una orden de compra" en Productos críticos, 2 notificaciones "Producto agotado", chatbot "Sin existencia". Contradice la regla de servicios.
- D2 MEDIA: Inicio admin: "Registrar venta" y "Registrar compra" son accesos rápidos deshabilitados sin explicación; "Nuevo producto" lleva a /products donde el admin no tiene ese botón.
- D3 MEDIA: Panorama dice "4 productos activos" y en la misma página "3 Productos totales".
- D4 MEDIA: Reportes de ventas muestra "Ventas realizadas", "Métodos de pago", "Ticket promedio" aunque el módulo Ventas está retirado; "Importes sin moneda de origen registrada: se presentan como unidades monetarias".
- D5 BAJA: chatbot no encuentra "Cable THW 12 AWG" (exige el nombre completo "… VI60"); precios sin moneda.
- OK: Valor del inventario Bs. 750,90 = 8×8 + 10×8 + 289×2,10 (cuadra con la base); movimientos recientes del Inicio coinciden.
- H1 BAJA: Importación histórica: texto técnico "Opcional en el contrato; el servidor usa historical_demand por defecto", "Tipo documental del lote" libre, año fijo "Solo 2025". Validación de BOM correcta.
- D6 MEDIA: Diagnóstico de historial (/predictions) dice "Aún no se generan pronósticos ni recomendaciones" (falso: existen Pronóstico y Reabastecimiento); "4 productos activos" pero la tabla lista 6 incluyendo una propuesta rechazada (AUD-V-004S-4) y una pendiente; KPIs truncados ("Productos con hi…"), tabla cortada a la derecha ("DEMA").
- V1 BAJA: en páginas largas el fondo gris del menú lateral termina a 900 px y debajo queda blanco (catálogo, conciliación, diagnóstico).

## Patrón transversal "—" por permisos
- X1 ALTA: cuando un rol no tiene permiso para ver un dato, la API lo omite y la pantalla pinta "—", igual que si el dato no existiera. Casos verificados: Inventario ve "Precio —" en todos los productos (lista y detalle) aunque todos tienen precio; "Monto recibido —", "Valor total recibido —", costo unitario/subtotal vacíos en Recepciones; KPIs de Notas "—"; en Órdenes de compra el Inventario ve "Valor solicitado", "Monto total", "Pendiente", y en cada OC "Costo", "Importe" y "Total" como "—". El Catálogo sí oculta la columna (coherente). Corrección: ocultar la columna/campo o mostrar "Restringido".

## Barrido por roles (admin, asistente, inventario, vendedor, soporte_ti)
- 0 errores JavaScript y 0 respuestas HTTP fallidas al abrir las ~90 pantallas y ~150 diálogos (solo los 500 provocados con datos extremos en Productos).
