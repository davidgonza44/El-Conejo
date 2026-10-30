# Diagnóstico de suficiencia histórica (prediction readiness)

Incremento cerrado: infraestructura de diagnóstico predictivo / readiness.

El módulo LIVE de Análisis predictivo (`/predictive/*`) ya consume esta
misma fuente canónica. Esta pantalla (`/predictions`) sigue siendo solo
diagnóstico: no elige modelo ni crea órdenes. El procedimiento para activar
resultados cuando lleguen los CSV reales está en
`docs/PREDICTIVE_HISTORICAL_HANDOFF.md`.

## Qué representa la demanda

La demanda comercial de un producto es la cantidad neta observada en el
tiempo, lista para un análisis futuro. En esta fase solo se **diagnostica**
si esa historia alcanza para un modelo. No se predice la demanda futura ni
se sugiere cuánto comprar.

## Fuentes usadas

1. **Histórica:** tabla `historical_demand_records`.
   Solo entran filas que:
   - pertenecen a un lote `historical_imports.status = confirmed`
     (los lotes revertidos quedan fuera);
   - `include_in_demand = true`;
   - están vigentes (`effective_status` en `issued` o `active`);
   - tienen `product_id` válido.
2. **Operativa:** `delivery_note_items` unido a `delivery_notes`.
   Solo entran renglones de notas con `status = issued`.
   Fecha: `delivery_notes.created_at` (día calendario).
   Cantidad: `delivery_note_items.quantity`.

Las devoluciones y anulaciones históricas se restan únicamente si tienen un
vínculo válido (`related_record_id` hacia una venta o corrección del mismo
producto). Si el vínculo falta o el neto diario queda negativo, se reporta
una inconsistencia; **no** se reemplaza el neto por cero en silencio.

## Por qué no se usa `stock_movements`

Cada nota de entrega emitida ya genera un movimiento de salida. Sumar
`stock_movements` con las notas **duplicaría** la misma operación. Las
salidas manuales, entradas y ajustes no son demanda comercial de mostrador.

## Cómo se evita el doble conteo

La combinación vive en un solo servicio: `app/services/demand_data_service.py`.

- El corte temporal se deriva de `period_start` y `period_end` de los lotes
  **confirmados** (no se inventan fechas).
- La historia importada cubre ese período confirmado (en la práctica, 2025).
- Las notas operativas solo entran **después** de `period_end`
  (`operational_starts_on = period_end + 1 día`).
- Si no hay importación confirmada, no hay corte: las notas emitidas pueden
  usarse solas. En el estado actual del proyecto eso suele clasificar casi
  todo como `NO_HISTORY` o `INSUFFICIENT`, y ese resultado es válido.

Una misma venta no puede contarse como historia CSV y como nota, ni como
nota y como movimiento de inventario.

## Granularidad diaria y ceros

La serie canónica es **diaria**.

1. Se toma la fecha mínima y máxima con demanda aplicada del producto.
2. Se materializa **cada** día del intervalo.
3. Los días sin eventos quedan en **0** (no se omiten).
4. Varias ventas del mismo día se agregan.
5. Las devoluciones vinculadas se restan en su `event_date`.

Ejemplo: `2025-01-01 → 3`, `2025-01-02 → 0`, `2025-01-03 → 7`.

## Clasificación de suficiencia

Los umbrales son constantes internas (no configurables en el frontend).
Se aplica de mayor a menor. Un **período** es un día calendario de la serie
rellena, no el número de eventos. Muchos eventos concentrados en uno o dos
días no alcanzan `SIMPLE_READY`.

| Clase | Condición |
|---|---|
| `ADVANCED_READY` | ≥ 60 períodos **y** ≥ 12 períodos positivos |
| `SIMPLE_READY` | ≥ 30 períodos **y** ≥ 8 períodos positivos |
| `LIMITED` | ≥ 8 períodos **y** ≥ 4 períodos positivos |
| `INSUFFICIENT` | hay demanda positiva pero no llega a `LIMITED` |
| `NO_HISTORY` | 0 períodos positivos |

## Patrones descriptivos

No se elige Croston ni ningún modelo. Solo se describe lo observado:

| Patrón | Regla |
|---|---|
| `no_history` | 0 días con demanda positiva |
| `sparse` | proporción de ceros ≥ 0,80 |
| `intermittent` | ceros ≥ 0,50 **o** intervalo medio entre positivos > 1,32 |
| `continuous` | el resto, con demanda positiva |

También se calculan `zero_ratio`, intervalo medio entre demandas positivas y
coeficiente de variación cuando la media es > 0. Las métricas de error
(MAE, RMSE, WAPE, MAPE) se calculan en el módulo LIVE a partir del
backtest fuera de muestra, no en esta pantalla de diagnóstico.

## Productos inactivos

Pueden aparecer en la auditoría si tienen datos asociados. Se identifican
como inactivos, `readiness_for_replenishment = false` y **no** son
candidatos de reabastecimiento.

## Relación con el módulo LIVE

`/predictions` y `/api/predictions/readiness` siguen siendo diagnóstico.
Los pronósticos, backtest, tendencias, criticidad y reabastecimiento viven
en `/predictive/*` (servicios en `app/services/*forecast*`,
`backtest_service`, `trend_service`, `critical_product_service`,
`replenishment_service`). Sin CSV confirmado, LIVE muestra estados
honestos; no hay fallback a fixtures.

## Qué pasará cuando se cargue el CSV real

Cuando un administrador confirme el CSV 2025 en Importación histórica:

1. Esos renglones (confirmados, vigentes, `include_in_demand`) pasarán a la
   serie diaria.
2. Las notas emitidas y ventas completadas seguirán contando solo
   **después** del período importado.
3. Esta pantalla recalculará suficiencia y patrones.
4. `/predictive/*` recalculará elegibilidad, baseline, backtest y
   recomendaciones. El procedimiento detallado está en
   `docs/PREDICTIVE_HISTORICAL_HANDOFF.md`.

## Permisos y rutas

- Permiso: `predictions:read` (admin e inventario; vendedor no).
- Web: `GET /predictions` (Análisis predictivo).
- API de solo lectura:
  - `GET /api/predictions/readiness`
  - `GET /api/predictions/products`
  - `GET /api/predictions/products/<id>`

Sin sesión → 401 JSON en API y redirección a `/login` en la página.
Sin permiso → 403 JSON / `/access-denied`. Producto inexistente → 404.

La API no expone `purchase_price`, `raw_row_json`, rutas físicas ni errores
internos.

## Verificación

`python scripts/test_prediction_readiness.py`
