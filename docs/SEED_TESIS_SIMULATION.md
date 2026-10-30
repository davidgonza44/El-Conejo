# Simulación aislada del conjunto de tesis — 2026-09-22

100 % sintético; no es evidencia empírica. Generado de nuevo mediante los servicios reales en SQLite temporal desechable, con PyMySQL bloqueado. No se creó la base MySQL ni se ejecutó el seed real.

| Entidad | Registros |
|---|---|
| categories | 10 |
| products | 120 |
| suppliers | 10 |
| customers | 60 |
| users | 3 |
| sales | 1571 |
| sale_lines | 3783 |
| stock_movements | 4817 |
| purchase_orders | 351 |
| purchase_order_lines | 926 |
| purchase_receptions | 331 |
| purchase_reception_lines | 906 |
| quotes | 40 |
| quote_lines | 80 |
| orders | 40 |
| order_lines | 80 |

## Predicción, demanda e inventario

| Indicador | Cantidad |
|---|---|
| Productos | 120 |
| Historia suficiente (simple/avanzada) | 82 |
| Forecast por modelo esperable | 18 |
| Estimación descriptiva esperable | 65 |
| Sin forecast/estimación | 37 |
| Agotados | 5 |
| Positivo inferior al mínimo | 1 |
| Exactamente en el mínimo | 15 |
| Reabastecimiento aproximado necesario | 42 |

| Suficiencia | Productos |
|---|---|
| ADVANCED_READY | 74 |
| INSUFFICIENT | 9 |
| LIMITED | 12 |
| NO_HISTORY | 17 |
| SIMPLE_READY | 8 |

| Elegibilidad | Productos |
|---|---|
| ELIGIBLE | 18 |
| INACTIVE | 12 |
| INSUFFICIENT_HISTORY | 13 |
| INTERMITTENT | 60 |
| NO_DEMAND | 17 |

| Patrón de demanda | Productos |
|---|---|
| Continua | 4 |
| Dispersa | 81 |
| Intermitente | 18 |
| Sin historial | 17 |

| Riesgo | Operativo | Aproximación con media |
|---|---|---|
| Crítico | 5 | 5 |
| Alto | 0 | 2 |
| Medio | 16 | 25 |
| Bajo | 99 | 88 |

Sin entrenamiento ni selección de modelos. La aproximación usa demanda media por día calendario, las reglas de cobertura existentes y un plazo supuesto de 7 días; el reabastecimiento descuenta compras pendientes. Las cantidades finales pueden cambiar al ejecutar forecasting. Inactivo describe la demanda, no el estado del catálogo.

## Categorías y ventas

| Categoría | Ventas con categoría | Ventas solo de categoría | Renglones | Elegibles modelo | % importe |
|---|---|---|---|---|---|
| Cementos y morteros | 294 | 183 | 535 | 2 | 39.27 |
| Pinturas | 240 | 140 | 472 | 2 | 22.92 |
| Tuberías PVC | 288 | 143 | 520 | 2 | 6.95 |
| Conexiones PVC | 246 | 113 | 430 | 2 | 2.36 |
| Tornillería | 267 | 99 | 429 | 2 | 3.97 |
| Herramientas manuales | 274 | 91 | 425 | 2 | 7.26 |
| Cables eléctricos | 135 | 84 | 273 | 2 | 9.96 |
| Pegamentos y selladores | 248 | 74 | 382 | 2 | 3.90 |
| Discos y abrasivos | 160 | 37 | 227 | 2 | 1.18 |
| Cerraduras | 43 | 26 | 90 | 0 | 2.21 |

Una venta puede incluir varias categorías; la columna de ventas con categoría no es sumable.

| Método de pago | Ventas |
|---|---|
| cash | 505 |
| credit_card | 62 |
| debit_card | 311 |
| transfer | 693 |

## Elegibilidad por producto (120)

| Código | Nombre | Categoría | Suficiencia | Demanda | Elegibilidad | Stock/mínimo | Riesgo aprox. |
|---|---|---|---|---|---|---|---|
| CEM-001 | Cemento gris 25 kg | Cementos y morteros | ADVANCED_READY | Intermitente | ELIGIBLE | 0/12 | Crítico |
| CEM-002 | Cemento gris 42,5 kg | Cementos y morteros | ADVANCED_READY | Dispersa | INTERMITTENT | 17/12 | Bajo |
| CEM-003 | Cemento gris 50 kg | Cementos y morteros | ADVANCED_READY | Intermitente | ELIGIBLE | 14/12 | Alto |
| CEM-004 | Cemento blanco 25 kg | Cementos y morteros | ADVANCED_READY | Dispersa | INTERMITTENT | 16/12 | Bajo |
| CEM-005 | Cemento blanco 42,5 kg | Cementos y morteros | ADVANCED_READY | Dispersa | INTERMITTENT | 14/12 | Bajo |
| CEM-006 | Cemento blanco 50 kg | Cementos y morteros | ADVANCED_READY | Dispersa | INTERMITTENT | 23/12 | Bajo |
| CEM-007 | Mortero de pega 25 kg | Cementos y morteros | ADVANCED_READY | Dispersa | INTERMITTENT | 17/12 | Bajo |
| CEM-008 | Mortero de pega 42,5 kg | Cementos y morteros | ADVANCED_READY | Dispersa | INTERMITTENT | 17/12 | Bajo |
| CEM-009 | Mortero de pega 50 kg | Cementos y morteros | ADVANCED_READY | Dispersa | INTERMITTENT | 16/12 | Bajo |
| CEM-010 | Friso premezclado 25 kg | Cementos y morteros | SIMPLE_READY | Dispersa | INACTIVE | 16/12 | Bajo |
| CEM-011 | Friso premezclado 42,5 kg | Cementos y morteros | NO_HISTORY | Sin historial | NO_DEMAND | 36/12 | Bajo |
| CEM-012 | Friso premezclado 50 kg | Cementos y morteros | NO_HISTORY | Sin historial | NO_DEMAND | 36/12 | Bajo |
| PIN-001 | Pintura caucho blanca 1/4 galón | Pinturas | ADVANCED_READY | Dispersa | INTERMITTENT | 16/12 | Bajo |
| PIN-002 | Pintura caucho blanca 1 galón | Pinturas | ADVANCED_READY | Intermitente | ELIGIBLE | 17/12 | Medio |
| PIN-003 | Pintura caucho blanca 4 galones | Pinturas | ADVANCED_READY | Dispersa | INTERMITTENT | 17/12 | Bajo |
| PIN-004 | Pintura satinada marfil 1/4 galón | Pinturas | ADVANCED_READY | Dispersa | INTERMITTENT | 13/12 | Bajo |
| PIN-005 | Pintura satinada marfil 1 galón | Pinturas | ADVANCED_READY | Intermitente | ELIGIBLE | 16/12 | Medio |
| PIN-006 | Pintura satinada marfil 4 galones | Pinturas | ADVANCED_READY | Dispersa | INTERMITTENT | 20/12 | Bajo |
| PIN-007 | Esmalte azul 1/4 galón | Pinturas | ADVANCED_READY | Dispersa | INTERMITTENT | 14/12 | Bajo |
| PIN-008 | Esmalte azul 1 galón | Pinturas | ADVANCED_READY | Dispersa | INTERMITTENT | 12/12 | Medio |
| PIN-009 | Esmalte azul 4 galones | Pinturas | SIMPLE_READY | Dispersa | INTERMITTENT | 13/12 | Bajo |
| PIN-010 | Fondo anticorrosivo gris 1/4 galón | Pinturas | LIMITED | Dispersa | INACTIVE | 21/12 | Bajo |
| PIN-011 | Fondo anticorrosivo gris 1 galón | Pinturas | NO_HISTORY | Sin historial | NO_DEMAND | 36/12 | Bajo |
| PIN-012 | Fondo anticorrosivo gris 4 galones | Pinturas | NO_HISTORY | Sin historial | NO_DEMAND | 36/12 | Bajo |
| PVC-001 | Tubo PVC presión 1/2 pulgada x 3 m | Tuberías PVC | ADVANCED_READY | Intermitente | ELIGIBLE | 14/12 | Medio |
| PVC-002 | Tubo PVC presión 3/4 pulgada x 3 m | Tuberías PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 15/12 | Bajo |
| PVC-003 | Tubo PVC presión 1 pulgada x 3 m | Tuberías PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 13/12 | Bajo |
| PVC-004 | Tubo PVC sanitario 2 pulgadas x 3 m | Tuberías PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 16/12 | Bajo |
| PVC-005 | Tubo PVC sanitario 3 pulgadas x 3 m | Tuberías PVC | ADVANCED_READY | Intermitente | ELIGIBLE | 14/12 | Medio |
| PVC-006 | Tubo PVC sanitario 4 pulgadas x 3 m | Tuberías PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 20/12 | Bajo |
| PVC-007 | Tubo PVC conduit 1/2 pulgada x 3 m | Tuberías PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 16/12 | Bajo |
| PVC-008 | Tubo PVC conduit 3/4 pulgada x 3 m | Tuberías PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 12/12 | Medio |
| PVC-009 | Tubo PVC conduit 1 pulgada x 3 m | Tuberías PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 14/12 | Bajo |
| PVC-010 | Tubo PVC ventilación 2 pulgadas x 3 m | Tuberías PVC | LIMITED | Dispersa | INACTIVE | 26/12 | Bajo |
| PVC-011 | Tubo PVC ventilación 3 pulgadas x 3 m | Tuberías PVC | NO_HISTORY | Sin historial | NO_DEMAND | 36/12 | Bajo |
| PVC-012 | Tubo PVC ventilación 4 pulgadas x 3 m | Tuberías PVC | NO_HISTORY | Sin historial | NO_DEMAND | 36/12 | Bajo |
| CON-001 | Codo PVC 90 grados 1/2 pulgada | Conexiones PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 14/12 | Bajo |
| CON-002 | Codo PVC 90 grados 3/4 pulgada | Conexiones PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 14/12 | Bajo |
| CON-003 | Codo PVC 90 grados 1 pulgada | Conexiones PVC | ADVANCED_READY | Intermitente | ELIGIBLE | 15/12 | Medio |
| CON-004 | Tee PVC 1/2 pulgada | Conexiones PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 15/12 | Bajo |
| CON-005 | Tee PVC 3/4 pulgada | Conexiones PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 15/12 | Bajo |
| CON-006 | Tee PVC 1 pulgada | Conexiones PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 20/12 | Bajo |
| CON-007 | Unión PVC 1/2 pulgada | Conexiones PVC | ADVANCED_READY | Intermitente | ELIGIBLE | 12/12 | Alto |
| CON-008 | Unión PVC 3/4 pulgada | Conexiones PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 12/12 | Medio |
| CON-009 | Unión PVC 1 pulgada | Conexiones PVC | ADVANCED_READY | Dispersa | INTERMITTENT | 0/12 | Crítico |
| CON-010 | Adaptador PVC roscado 1/2 pulgada | Conexiones PVC | SIMPLE_READY | Dispersa | INACTIVE | 12/12 | Medio |
| CON-011 | Adaptador PVC roscado 3/4 pulgada | Conexiones PVC | INSUFFICIENT | Continua | INSUFFICIENT_HISTORY | 33/12 | Bajo |
| CON-012 | Adaptador PVC roscado 1 pulgada | Conexiones PVC | NO_HISTORY | Sin historial | NO_DEMAND | 36/12 | Bajo |
| TOR-001 | Tornillo autorroscante 1 pulgada, 100 uds. | Tornillería | ADVANCED_READY | Dispersa | INTERMITTENT | 17/12 | Bajo |
| TOR-002 | Tornillo autorroscante 1 1/2 pulgadas, 100 uds. | Tornillería | ADVANCED_READY | Dispersa | INTERMITTENT | 12/12 | Medio |
| TOR-003 | Tornillo autorroscante 2 pulgadas, 100 uds. | Tornillería | ADVANCED_READY | Dispersa | INTERMITTENT | 15/12 | Bajo |
| TOR-004 | Tornillo drywall 1 pulgada, 100 uds. | Tornillería | ADVANCED_READY | Intermitente | ELIGIBLE | 15/12 | Medio |
| TOR-005 | Tornillo drywall 1 1/2 pulgadas, 100 uds. | Tornillería | ADVANCED_READY | Dispersa | INTERMITTENT | 13/12 | Bajo |
| TOR-006 | Tornillo drywall 2 pulgadas, 100 uds. | Tornillería | ADVANCED_READY | Dispersa | INTERMITTENT | 18/12 | Bajo |
| TOR-007 | Tornillo para madera 1 pulgada, 100 uds. | Tornillería | ADVANCED_READY | Intermitente | ELIGIBLE | 17/12 | Medio |
| TOR-008 | Tornillo para madera 1 1/2 pulgadas, 100 uds. | Tornillería | ADVANCED_READY | Dispersa | INTERMITTENT | 0/12 | Crítico |
| TOR-009 | Tornillo para madera 2 pulgadas, 100 uds. | Tornillería | SIMPLE_READY | Dispersa | INACTIVE | 15/12 | Bajo |
| TOR-010 | Perno hexagonal 1 pulgada, 100 uds. | Tornillería | LIMITED | Dispersa | INACTIVE | 20/12 | Bajo |
| TOR-011 | Perno hexagonal 1 1/2 pulgadas, 100 uds. | Tornillería | INSUFFICIENT | Continua | INSUFFICIENT_HISTORY | 34/12 | Bajo |
| TOR-012 | Perno hexagonal 2 pulgadas, 100 uds. | Tornillería | NO_HISTORY | Sin historial | NO_DEMAND | 36/12 | Bajo |
| HER-001 | Alicate universal 6 pulgadas | Herramientas manuales | ADVANCED_READY | Dispersa | INTERMITTENT | 6/6 | Medio |
| HER-002 | Alicate universal 8 pulgadas | Herramientas manuales | ADVANCED_READY | Intermitente | ELIGIBLE | 11/6 | Bajo |
| HER-003 | Alicate universal 10 pulgadas | Herramientas manuales | ADVANCED_READY | Dispersa | INTERMITTENT | 6/6 | Medio |
| HER-004 | Llave ajustable 6 pulgadas | Herramientas manuales | ADVANCED_READY | Dispersa | INTERMITTENT | 7/6 | Bajo |
| HER-005 | Llave ajustable 8 pulgadas | Herramientas manuales | ADVANCED_READY | Intermitente | ELIGIBLE | 8/6 | Medio |
| HER-006 | Llave ajustable 10 pulgadas | Herramientas manuales | ADVANCED_READY | Dispersa | INTERMITTENT | 15/6 | Bajo |
| HER-007 | Pinza de presión 6 pulgadas | Herramientas manuales | ADVANCED_READY | Dispersa | INTERMITTENT | 0/6 | Crítico |
| HER-008 | Pinza de presión 8 pulgadas | Herramientas manuales | ADVANCED_READY | Dispersa | INTERMITTENT | 7/6 | Bajo |
| HER-009 | Pinza de presión 10 pulgadas | Herramientas manuales | ADVANCED_READY | Dispersa | INTERMITTENT | 10/6 | Bajo |
| HER-010 | Alicate de corte 6 pulgadas | Herramientas manuales | LIMITED | Dispersa | INACTIVE | 11/6 | Bajo |
| HER-011 | Alicate de corte 8 pulgadas | Herramientas manuales | INSUFFICIENT | Continua | INSUFFICIENT_HISTORY | 16/6 | Bajo |
| HER-012 | Alicate de corte 10 pulgadas | Herramientas manuales | NO_HISTORY | Sin historial | NO_DEMAND | 18/6 | Bajo |
| CAB-001 | Cable THHN cobre rojo 14 AWG x 20 m | Cables eléctricos | ADVANCED_READY | Dispersa | INTERMITTENT | 8/6 | Bajo |
| CAB-002 | Cable THHN cobre rojo 12 AWG x 20 m | Cables eléctricos | ADVANCED_READY | Intermitente | ELIGIBLE | 11/6 | Bajo |
| CAB-003 | Cable THHN cobre rojo 10 AWG x 20 m | Cables eléctricos | ADVANCED_READY | Dispersa | INTERMITTENT | 6/6 | Medio |
| CAB-004 | Cable THHN cobre negro 14 AWG x 20 m | Cables eléctricos | ADVANCED_READY | Intermitente | ELIGIBLE | 9/6 | Bajo |
| CAB-005 | Cable THHN cobre negro 12 AWG x 20 m | Cables eléctricos | ADVANCED_READY | Dispersa | INTERMITTENT | 9/6 | Bajo |
| CAB-006 | Cable THHN cobre negro 10 AWG x 20 m | Cables eléctricos | SIMPLE_READY | Dispersa | INACTIVE | 6/6 | Medio |
| CAB-007 | Cable THHN cobre azul 14 AWG x 20 m | Cables eléctricos | ADVANCED_READY | Dispersa | INTERMITTENT | 9/6 | Bajo |
| CAB-008 | Cable THHN cobre azul 12 AWG x 20 m | Cables eléctricos | LIMITED | Dispersa | INSUFFICIENT_HISTORY | 8/6 | Bajo |
| CAB-009 | Cable THHN cobre azul 10 AWG x 20 m | Cables eléctricos | LIMITED | Dispersa | INSUFFICIENT_HISTORY | 11/6 | Bajo |
| CAB-010 | Cable THHN cobre verde 14 AWG x 20 m | Cables eléctricos | LIMITED | Dispersa | INACTIVE | 13/6 | Bajo |
| CAB-011 | Cable THHN cobre verde 12 AWG x 20 m | Cables eléctricos | NO_HISTORY | Sin historial | NO_DEMAND | 18/6 | Bajo |
| CAB-012 | Cable THHN cobre verde 10 AWG x 20 m | Cables eléctricos | NO_HISTORY | Sin historial | NO_DEMAND | 18/6 | Bajo |
| PEG-001 | Pegamento PVC 250 ml | Pegamentos y selladores | ADVANCED_READY | Intermitente | ELIGIBLE | 15/12 | Medio |
| PEG-002 | Pegamento PVC 500 ml | Pegamentos y selladores | ADVANCED_READY | Dispersa | INTERMITTENT | 17/12 | Bajo |
| PEG-003 | Pegamento PVC 1 litro | Pegamentos y selladores | ADVANCED_READY | Dispersa | INTERMITTENT | 15/12 | Bajo |
| PEG-004 | Adhesivo de contacto 250 ml | Pegamentos y selladores | ADVANCED_READY | Dispersa | INTERMITTENT | 12/12 | Medio |
| PEG-005 | Adhesivo de contacto 500 ml | Pegamentos y selladores | ADVANCED_READY | Dispersa | INTERMITTENT | 0/12 | Crítico |
| PEG-006 | Adhesivo de contacto 1 litro | Pegamentos y selladores | ADVANCED_READY | Dispersa | INTERMITTENT | 18/12 | Bajo |
| PEG-007 | Sellador acrílico blanco 250 ml | Pegamentos y selladores | ADVANCED_READY | Dispersa | INTERMITTENT | 14/12 | Bajo |
| PEG-008 | Sellador acrílico blanco 500 ml | Pegamentos y selladores | ADVANCED_READY | Intermitente | ELIGIBLE | 17/12 | Medio |
| PEG-009 | Sellador acrílico blanco 1 litro | Pegamentos y selladores | SIMPLE_READY | Dispersa | INTERMITTENT | 12/12 | Medio |
| PEG-010 | Sellador acrílico gris 250 ml | Pegamentos y selladores | LIMITED | Dispersa | INACTIVE | 19/12 | Bajo |
| PEG-011 | Sellador acrílico gris 500 ml | Pegamentos y selladores | NO_HISTORY | Sin historial | NO_DEMAND | 36/12 | Bajo |
| PEG-012 | Sellador acrílico gris 1 litro | Pegamentos y selladores | NO_HISTORY | Sin historial | NO_DEMAND | 36/12 | Bajo |
| DIS-001 | Disco de corte metal 4 1/2 pulgadas | Discos y abrasivos | ADVANCED_READY | Intermitente | ELIGIBLE | 12/12 | Medio |
| DIS-002 | Disco de corte metal 7 pulgadas | Discos y abrasivos | ADVANCED_READY | Dispersa | INTERMITTENT | 16/12 | Bajo |
| DIS-003 | Disco de corte metal 9 pulgadas | Discos y abrasivos | ADVANCED_READY | Dispersa | INTERMITTENT | 17/12 | Bajo |
| DIS-004 | Disco diamantado 4 1/2 pulgadas | Discos y abrasivos | ADVANCED_READY | Dispersa | INTERMITTENT | 10/12 | Medio |
| DIS-005 | Disco diamantado 7 pulgadas | Discos y abrasivos | ADVANCED_READY | Intermitente | ELIGIBLE | 13/12 | Bajo |
| DIS-006 | Disco diamantado 9 pulgadas | Discos y abrasivos | ADVANCED_READY | Dispersa | INTERMITTENT | 20/12 | Bajo |
| DIS-007 | Disco de desbaste 4 1/2 pulgadas | Discos y abrasivos | LIMITED | Dispersa | INSUFFICIENT_HISTORY | 24/12 | Bajo |
| DIS-008 | Disco de desbaste 7 pulgadas | Discos y abrasivos | SIMPLE_READY | Dispersa | INTERMITTENT | 18/12 | Bajo |
| DIS-009 | Disco de desbaste 9 pulgadas | Discos y abrasivos | INSUFFICIENT | Dispersa | INSUFFICIENT_HISTORY | 32/12 | Bajo |
| DIS-010 | Disco flap 4 1/2 pulgadas | Discos y abrasivos | LIMITED | Dispersa | INACTIVE | 26/12 | Bajo |
| DIS-011 | Disco flap 7 pulgadas | Discos y abrasivos | NO_HISTORY | Sin historial | NO_DEMAND | 36/12 | Bajo |
| DIS-012 | Disco flap 9 pulgadas | Discos y abrasivos | NO_HISTORY | Sin historial | NO_DEMAND | 36/12 | Bajo |
| CER-001 | Cerradura de pomo acabado latón | Cerraduras | ADVANCED_READY | Dispersa | INTERMITTENT | 8/6 | Bajo |
| CER-002 | Cerradura de pomo acabado níquel | Cerraduras | ADVANCED_READY | Dispersa | INTERMITTENT | 11/6 | Bajo |
| CER-003 | Cerradura de pomo acabado negro | Cerraduras | LIMITED | Dispersa | INSUFFICIENT_HISTORY | 8/6 | Bajo |
| CER-004 | Cerradura de sobreponer acabado latón | Cerraduras | LIMITED | Dispersa | INSUFFICIENT_HISTORY | 6/6 | Medio |
| CER-005 | Cerradura de sobreponer acabado níquel | Cerraduras | SIMPLE_READY | Dispersa | INTERMITTENT | 6/6 | Medio |
| CER-006 | Cerradura de sobreponer acabado negro | Cerraduras | INSUFFICIENT | Dispersa | INSUFFICIENT_HISTORY | 20/6 | Bajo |
| CER-007 | Cerrojo de seguridad acabado latón | Cerraduras | INSUFFICIENT | Dispersa | INSUFFICIENT_HISTORY | 14/6 | Bajo |
| CER-008 | Cerrojo de seguridad acabado níquel | Cerraduras | INSUFFICIENT | Dispersa | INSUFFICIENT_HISTORY | 15/6 | Bajo |
| CER-009 | Cerrojo de seguridad acabado negro | Cerraduras | INSUFFICIENT | Dispersa | INSUFFICIENT_HISTORY | 14/6 | Bajo |
| CER-010 | Cerradura de embutir acabado latón | Cerraduras | INSUFFICIENT | Continua | INACTIVE | 16/6 | Bajo |
| CER-011 | Cerradura de embutir acabado níquel | Cerraduras | NO_HISTORY | Sin historial | NO_DEMAND | 18/6 | Bajo |
| CER-012 | Cerradura de embutir acabado negro | Cerraduras | NO_HISTORY | Sin historial | NO_DEMAND | 18/6 | Bajo |

## Reabastecimiento aproximado por producto

| Código | Nombre | Cantidad | Prioridad | Pendiente descontado |
|---|---|---|---|---|
| CEM-001 | Cemento gris 25 kg | 25 | Alta | 0 |
| CEM-002 | Cemento gris 42,5 kg | 2 | Baja | 0 |
| CEM-003 | Cemento gris 50 kg | 26 | Alta | 0 |
| CEM-004 | Cemento blanco 25 kg | 3 | Baja | 0 |
| CEM-005 | Cemento blanco 42,5 kg | 4 | Baja | 0 |
| PIN-002 | Pintura caucho blanca 1 galón | 5 | Media | 0 |
| PIN-004 | Pintura satinada marfil 1/4 galón | 1 | Baja | 0 |
| PIN-005 | Pintura satinada marfil 1 galón | 6 | Media | 0 |
| PIN-008 | Esmalte azul 1 galón | 2 | Baja | 0 |
| PVC-001 | Tubo PVC presión 1/2 pulgada x 3 m | 9 | Media | 0 |
| PVC-002 | Tubo PVC presión 3/4 pulgada x 3 m | 1 | Baja | 0 |
| PVC-003 | Tubo PVC presión 1 pulgada x 3 m | 2 | Baja | 0 |
| PVC-005 | Tubo PVC sanitario 3 pulgadas x 3 m | 9 | Media | 0 |
| PVC-008 | Tubo PVC conduit 3/4 pulgada x 3 m | 2 | Baja | 0 |
| CON-001 | Codo PVC 90 grados 1/2 pulgada | 1 | Baja | 0 |
| CON-003 | Codo PVC 90 grados 1 pulgada | 11 | Media | 0 |
| CON-005 | Tee PVC 3/4 pulgada | 1 | Baja | 0 |
| CON-007 | Unión PVC 1/2 pulgada | 13 | Alta | 0 |
| CON-008 | Unión PVC 3/4 pulgada | 2 | Baja | 0 |
| CON-009 | Unión PVC 1 pulgada | 14 | Alta | 0 |
| TOR-002 | Tornillo autorroscante 1 1/2 pulgadas, 100 uds. | 2 | Baja | 0 |
| TOR-004 | Tornillo drywall 1 pulgada, 100 uds. | 6 | Media | 0 |
| TOR-005 | Tornillo drywall 1 1/2 pulgadas, 100 uds. | 1 | Baja | 0 |
| TOR-007 | Tornillo para madera 1 pulgada, 100 uds. | 5 | Media | 0 |
| TOR-008 | Tornillo para madera 1 1/2 pulgadas, 100 uds. | 13 | Alta | 0 |
| HER-001 | Alicate universal 6 pulgadas | 1 | Baja | 0 |
| HER-002 | Alicate universal 8 pulgadas | 1 | Baja | 0 |
| HER-003 | Alicate universal 10 pulgadas | 2 | Baja | 0 |
| HER-005 | Llave ajustable 8 pulgadas | 4 | Media | 0 |
| HER-007 | Pinza de presión 6 pulgadas | 7 | Alta | 0 |
| CAB-003 | Cable THHN cobre rojo 10 AWG x 20 m | 1 | Baja | 0 |
| CAB-004 | Cable THHN cobre negro 14 AWG x 20 m | 1 | Baja | 0 |
| PEG-001 | Pegamento PVC 250 ml | 5 | Media | 0 |
| PEG-004 | Adhesivo de contacto 250 ml | 2 | Baja | 0 |
| PEG-005 | Adhesivo de contacto 500 ml | 14 | Alta | 0 |
| PEG-008 | Sellador acrílico blanco 500 ml | 4 | Media | 0 |
| PEG-009 | Sellador acrílico blanco 1 litro | 1 | Baja | 0 |
| DIS-001 | Disco de corte metal 4 1/2 pulgadas | 4 | Baja | 0 |
| DIS-004 | Disco diamantado 4 1/2 pulgadas | 3 | Baja | 0 |
| DIS-005 | Disco diamantado 7 pulgadas | 4 | Baja | 0 |
| CER-004 | Cerradura de sobreponer acabado latón | 1 | Baja | 0 |
| CER-005 | Cerradura de sobreponer acabado níquel | 1 | Baja | 0 |

## Diez movimientos visibles

| Fecha | Tipo | Producto | Cantidad | Anterior | Actual | Motivo | Responsable | Activo hoy |
|---|---|---|---|---|---|---|---|---|
| 2026-01-01 | entrada | CEM-001 | 36 | 0 | 36 | Existencia inicial según conteo de almacén | Rafael Villalba | False |
| 2026-01-01 | entrada | HER-001 | 18 | 0 | 18 | Existencia inicial según conteo de almacén | Rafael Villalba | False |
| 2026-09-19 | entrada | CEM-006 | 6 | 17 | 23 | Recepción 316 · OC-000316 | Mariana Duarte | True |
| 2026-09-20 | entrada | CEM-006 | 6 | 17 | 23 | Recepción 322 · OC-000323 | Mariana Duarte | True |
| 2026-09-20 | entrada | CER-006 | 6 | 14 | 20 | Recepción 331 · OC-000350 | Mariana Duarte | True |
| 2026-09-18 | salida | CER-001 | 2 | 10 | 8 | Venta VTA-001558 | Daniel Acosta | True |
| 2026-09-20 | salida | CEM-007 | 3 | 20 | 17 | Venta VTA-001571 | Daniel Acosta | True |
| 2026-09-19 | salida | CEM-001 | 6 | 6 | 0 | Venta VTA-001560 | Daniel Acosta | True |
| 2026-07-21 | ajuste | HER-010 | 1 | 12 | 11 | Unidad deteriorada durante revisión de almacén | Mariana Duarte | True |
| 2026-08-21 | ajuste | HER-005 | 1 | 6 | 5 | Unidad deteriorada durante revisión de almacén | Mariana Duarte | True |

| Responsable | Rol | Activo | Movimientos |
|---|---|---|---|
| Rafael Villalba | admin | False | 120 |
| Mariana Duarte | inventario | True | 914 |
| Daniel Acosta | vendedor | True | 3783 |

## Proveedores y clientes: cinco ejemplos de cada uno

| Proveedor | Cliente |
|---|---|
| Materiales La Ceiba C.A. | Ana Salcedo |
| Pinturas Los Cedros C.A. | Luis Figueroa |
| Tuberías El Roble C.A. | María Mendoza |
| Conexiones La Colina C.A. | Carlos Paredes |
| Fijaciones El Samán C.A. | Elena Rivas |

## Cinco cotizaciones

| Número | Cliente | Estado | Validez | Total |
|---|---|---|---|---|
| COT-000001 | Ana Salcedo | pending | 2026-10-05 | 42.52 |
| COT-000002 | Pedro Figueroa | sent | 2026-10-04 | 99.24 |
| COT-000003 | Elena Paredes | accepted | 2026-10-03 | 37.80 |
| COT-000004 | Luis Cordero | rejected | 2026-10-02 | 4.72 |
| COT-000005 | Valeria Salcedo | expired | 2026-08-21 | 21.27 |

## Cinco pedidos

| Número | Cliente | Estado | Pago | Total |
|---|---|---|---|---|
| PED-000001 | Ana Salcedo | pending | pending | 42.52 |
| PED-000002 | Pedro Figueroa | processing | paid | 99.24 |
| PED-000003 | Elena Paredes | completed | paid | 37.80 |
| PED-000004 | Luis Cordero | cancelled | pending | 4.72 |
| PED-000005 | Valeria Salcedo | pending | pending | 21.27 |

## Cinco órdenes de compra

| Número | Proveedor | Estado | Ordenado | Recibido | Total |
|---|---|---|---|---|---|
| OC-000001 | Materiales La Ceiba C.A. | completed | 6 | 6 | 86.40 |
| OC-000003 | Tuberías El Roble C.A. | completed | 24 | 24 | 72.00 |
| OC-000322 | Materiales La Ceiba C.A. | pending | 12 | 0 | 172.80 |
| OC-000323 | Materiales La Ceiba C.A. | partial | 12 | 6 | 172.80 |
| OC-000324 | Materiales La Ceiba C.A. | cancelled | 12 | 0 | 172.80 |

Importes almacenados, sin atribuirles una moneda. Todos los nombres y operaciones son ficticios.

## Búsqueda completa de etiquetas

16 tablas, 13128 registros, 139692 campos. Solo se excluye seed_manifests. Búsqueda sin distinguir mayúsculas ni acentos.

| Expresión | Coincidencias |
|---|---|
| DEMO | 0 |
| DEMO-LARGE | 0 |
| Serie demo | 0 |
| [DEMO | 0 |
| sintético | 0 |
| synthetic | 0 |
| seed | 0 |
| manifest | 0 |
| 20260921 | 0 |
