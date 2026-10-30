# Integridad de Compras

Correcciones del 30 de octubre de 2026. La migración `0011_purchase_integrity`
sigue a `0010_inventory_integrity`, que pertenece al trabajo de Inventario.
Este cambio prepara código y migración; no aplica cambios a bases operativas.

## Reglas del flujo

- Una cotización respalda la cantidad exacta de cada producto incluido en una
  orden. Se verifica al preparar, renovar y aprobar. Puede utilizarse una
  cotización vigente en más de una solicitud; no constituye una reserva global
  ni un contrato de suministro acumulativo. Cada orden respeta sus cantidades.
- Las solicitudes y las cotizaciones rechazan productos cuya designación
  confiable `is_service` sea verdadera. No se infiere esa designación del nombre.
- La aprobación conserva nombre, RIF y contacto del proveedor en la orden.
  Las lecturas, impresiones y recepciones usan esa copia. Las órdenes antiguas
  permanecen sin copia; los documentos advierten que muestran datos actuales.
  No se inventan ni reconstruyen datos históricos durante la migración.
- La fecha física de recepción debe estar entre el día de aprobación en
  America/Caracas y hoy. La fecha UTC de registro del movimiento se conserva
  aparte; no se antedatan movimientos ni se recalculan cierres de inventario.
- Los cambios de estado de proveedores son propuestas del Asistente, con
  motivo, que decide el Administrador. El proveedor conserva su estado vigente
  hasta la aprobación. Las órdenes ya aprobadas no se anulan por desactivarlo.
- La bandeja `/purchases/approvals` reúne órdenes, altas y cambios de proveedores,
  cierres comerciales y correcciones de recepción. Está paginada por antigüedad
  y muestra al solicitante. Cada tipo respeta su permiso de aprobación.

## Corrección de recepciones

Inventario solicita la cantidad total que debió recibirse de cada producto,
con motivo. Administración aprueba o rechaza; el solicitante no decide.
La recepción original y sus movimientos no se editan ni eliminan.

Al aprobar, cada diferencia genera una entrada o salida compensatoria mediante
`inventory_service`, en la misma transacción que la decisión y los saldos
recibidos de la orden. Se enlazan solicitud, renglón, producto y movimiento.
El comprobante muestra detalle original, total neto e historial de correcciones.
La orden muestra las correcciones aprobadas y conserva el motivo original de cierre.

Controles:

- Solo órdenes cerradas completas o parciales del flujo de una recepción.
- Cantidad corregida entre cero y lo pedido; al menos un cambio real.
- Una sola corrección pendiente por recepción, serializada por bloqueo de la OC.
- Si cambió el stock o su último movimiento desde la solicitud, se rechaza la
  aprobación y debe presentarse otra propuesta. También se comprueba que las
  cantidades recibidas sigan siendo las que se revisaron.
- Las salidas compensatorias requieren existencias suficientes; toda la
  operación se revierte si falla cualquier renglón.
- Una reposición de faltantes en trámite bloquea la corrección. Una corrección
  pendiente bloquea nuevas reposiciones del mismo faltante.
- Los productos inactivos conservan las restricciones del servicio de Inventario.
- Los reintentos de una aprobación decidida devuelven conflicto sin duplicar stock.
- Las notificaciones ocurren después del commit y sus fallos no repiten la operación.

## Indicadores y exportaciones

Los valores solicitado, aprobado, recibido neto y no suministrado se presentan
por separado, en USD y VES, sin conversiones. La ventana corresponde a la fecha
de las órdenes seleccionadas y los saldos reflejan sus correcciones aprobadas.
El valor solicitado excluye órdenes rechazadas o anuladas; el aprobado exige
una aprobación registrada. Los registros antiguos no se aprueban por inferencia.
Inventario conserva las cantidades sin ver importes.

Las tres exportaciones (órdenes, proveedores y recepciones) consultan como máximo
10.001 registros para aceptar hasta 10.000. Al superar ese límite se pide reducir
los filtros antes de generar el archivo. Las órdenes precargan relaciones usadas
por el listado y el CSV para evitar una consulta de cierre por documento.

## Migración y validación

Aplicar las migraciones mediante el procedimiento de despliegue existente,
primero Inventario `0010` y después Compras `0011`, con respaldo y validación en
una copia desechable. No ejecutar scripts de inicialización sobre datos valiosos.
La migración 0011 es aditiva y no altera cantidades, movimientos ni proveedores.
Su reversión automática se bloquea para conservar la evidencia de correcciones.

Pruebas fuente:

- `scripts/test_purchase_integrity.py`: SQLite en memoria, con conexiones MySQL
  y entregas externas bloqueadas. Valida controles, permisos, documentos,
  compensaciones, importes y límites de exportación.
- `scripts/test_purchase_integrity_concurrency.py`: exige una base desechable
  `conejo_tutor_test_<12 hex>` en 127.0.0.1. Crea y elimina datos sintéticos.
  Hereda los casos del tutor y agrega edición de asociaciones, aprobación doble
  de correcciones, movimientos simultáneos, reposición concurrente y migración.

La suite de concurrencia usa `DB_PORT`, `DB_USER`, `DB_PASSWORD` y
`TUTOR_TEST_DATABASE` definidos explícitamente; nunca carga `.env`.
La comprobación visual requiere un navegador disponible. No se certifica paridad
visual con las referencias mediante pruebas de API o sintaxis.
