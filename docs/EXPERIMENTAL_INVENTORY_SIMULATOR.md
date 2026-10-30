# Simulador experimental de control de inventario

Esta infraestructura permite comparar el impacto de secuencias de pronósticos bajo un escenario declarado. No aporta resultados científicos cerrados ni modifica el protocolo congelado. No importa Flask, SQLAlchemy ni servicios de compras; no crea órdenes reales ni alimenta `PurchaseOrder`, el selector LIVE o `replenishment_service`. Usa exclusivamente la biblioteca estándar de Python. Sus fixtures son DEMO sintéticos; no representan valores empresariales ni evidencia de 2025.

## Contrato explícito

`app/services/experimental_inventory_service.py` expone `simulate_inventory(...)`. Todos sus argumentos son obligatorios y se entregan por nombre:

| Argumento | Contrato |
| --- | --- |
| `demand_series` | Secuencia no vacía de cantidades realizadas, finitas y no negativas, un período observado por posición. |
| `forecast_series` | Matriz por origen: fila `t` disponible al comienzo de `t`, antes de observar su demanda. Para la política con pronóstico, cada fila contiene exactamente `L + R` predicciones de demanda de `t` a `t + L + R - 1`. |
| `initial_stock` | Existencia física inicial no negativa, explícita. No hay pedidos en tránsito ni atrasos iniciales. |
| `lead_time_periods` (`L`) | Entero no negativo en la misma unidad temporal de la demanda. |
| `review_period` (`R`) | Entero positivo; se revisa en `t = 0, R, 2R, ...`. |
| `stockout_mode` | `lost_sales` o `backorder`. |
| `policy` | `fixed_order_up_to` o `forecast_order_up_to`, elegida explícitamente. |
| `policy_parameters` | Exactamente `{"target_stock": cantidad}`; no se aceptan parámetros ocultos. |

No hay defaults empresariales. En `fixed_order_up_to`, `target_stock` es el nivel objetivo fijo, y se permite una fila vacía por origen porque esa política no usa pronósticos. En `forecast_order_up_to`, el mismo parámetro es un **buffer explícito en unidades**, adicional al pronóstico acumulado. El valor cero también debe suministrarse. No se deduce stock de un archivo de existencias ni se estima lead time.

El período tiene una duración uniforme, definida por el escenario; pueden ser días operativos consecutivos o días calendario auditados. El servicio no interpreta fechas ni rellena huecos. Un período no observado debe detener o segmentar el escenario antes de invocarlo; no se representa con cero ni se elimina un hueco comprimiendo la continuidad. En particular, 2025 continúa no observado. Las funciones que preparen entradas deben comprobar la continuidad temporal y la procedencia de cada fila del pronóstico.

## Política y orden de eventos

La posición de inventario es `IP_t = existencia física + pedidos en tránsito - atrasos pendientes`. En cada revisión:

- Política fija: `S_t = target_stock`.
- Política con pronóstico: `S_t = sum(forecast_series[t]) + target_stock`.
- Cantidad pedida: `Q_t = max(0, S_t - IP_t)`.

El servicio permite cantidades fraccionarias; no supone múltiplos de empaque, cantidades mínimas, capacidad, costos ni redondeo. Un pedido positivo constituye una orden simulada. La entrega es completa, determinista, en `t + L`.

Cada período sigue este orden:

1. Recibir pedidos cuya fecha simulada sea `t`.
2. Servir atrasos anteriores con esas existencias.
3. Si toca revisión, decidir el pedido con el estado disponible y **solo la fila de pronóstico de origen `t`**.
4. Con `L = 0`, recibir inmediatamente ese pedido y servir los atrasos que permanezcan.
5. Observar la demanda de `t`, servir lo disponible y registrar su faltante inmediato.
6. En `lost_sales`, perder el faltante; en `backorder`, mantenerlo pendiente para períodos posteriores.

La decisión no recibe demanda real de `t` ni posterior. Tampoco suma filas futuras de `forecast_series` para construir el horizonte. El caller sigue siendo responsable de producir cada fila con historia anterior al origen; el simulador no puede detectar un pronóstico que ya venga contaminado. Los tests perturban demanda futura y filas futuras para comprobar la causalidad interna.

El escenario termina tras la última demanda proporcionada. No se simula demanda cero adicional ni se reciben entregas posteriores artificialmente. `ending_orders` conserva cantidades y períodos de llegada fuera del horizonte.

## Métricas y fórmulas

Sea `d_t` la demanda, `f_t` el servicio inmediato, `b_t` el servicio de atrasos y `u_t = d_t - f_t` el faltante inmediato. Las métricas son:

| Campo | Definición |
| --- | --- |
| `total_demand` | `sum(d_t)`. |
| `fulfilled_units` | `sum(f_t + b_t)`; todo servicio materializado dentro del horizonte. |
| `unmet_units` | Pérdida definitiva en `lost_sales`; atraso pendiente al final en `backorder`. No cuenta varias veces un atraso. |
| `immediately_fulfilled_units` / `immediate_unmet_units` | `sum(f_t)` / `sum(u_t)`. |
| `backorder_fulfilled_units` / `lost_units` | Servicio diferido acumulado / pérdidas acumuladas. |
| `fill_rate` | `sum(f_t) / sum(d_t)`: fracción servida **inmediatamente**, incluso con atrasos servidos después. |
| `stockout_periods` | Número de períodos con `u_t > 0`; no cuenta un período sin nueva demanda solo porque exista backlog. |
| `average_on_hand` | Promedio de existencia física **al final** de cada período. |
| `max_on_hand` | Mayor existencia física observada: inicial y picos tras recepciones, antes del servicio. |
| `ending_stock` | Existencia física al final del horizonte. |
| `order_count` / `total_ordered_units` | Órdenes positivas / suma de sus cantidades. |
| `received_units` / `ending_pipeline_units` | Recepciones dentro del horizonte / órdenes aún en tránsito al final. |
| `ending_backorder` | Demanda pendiente al final; cero para pérdidas. |
| `average_backorder` / `max_backorder` | Promedio / máximo de atrasos al final de cada período, solo para `backorder`. |

`cycle_service_level` es una proporción empírica de **ciclos completos de revisión** `[kR, (k+1)R)`, no una probabilidad teórica de servicio ni un estimador por ciclo de llegada de pedidos. Un ciclo exitoso comienza sin atraso pendiente y no presenta faltante inmediato. La fórmula es `successful_review_cycles / complete_review_cycles`; el tramo terminal incompleto se excluye y su longitud queda registrada en `excluded_partial_cycle_periods`.

Con demanda total cero, `fill_rate = null` y `metric_availability.fill_rate` contiene `available=false` y `reason.code=zero_total_demand`. Sin ciclos completos, se usa el mismo contrato para CSL con `no_complete_review_cycle`. Nunca se produce NaN/Infinity; valores no finitos o desbordamientos se rechazan con `ValueError`. No existe una métrica denominada exceso de inventario porque no se declaró un umbral para definirlo.

`accounting` expone el residuo numérico de tres identidades:

- `initial_stock + received_units - fulfilled_units = ending_stock`.
- `total_demand = fulfilled_units + lost_units + ending_backorder`.
- `total_ordered_units = received_units + ending_pipeline_units`.

Para cantidades decimales, la comparación contable utiliza tolerancia de punto flotante. La traza por período expone entradas, recepciones, servicio actual y diferido, posición antes del pedido, objetivo, orden, existencia, backlog y pipeline para auditoría.

## Ejemplos DEMO calculables a mano

```python
import importlib.util
from pathlib import Path

# Ejecutar desde la raíz del repositorio sin importar app/__init__.py.
path = Path("app/services/experimental_inventory_service.py")
spec = importlib.util.spec_from_file_location("inventory_demo", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
simulate_inventory = module.simulate_inventory

demo = simulate_inventory(
    demand_series=[2, 2, 2],
    forecast_series=[[2, 2], [2, 2], [2, 2]],
    initial_stock=2,
    lead_time_periods=1,
    review_period=1,
    stockout_mode="lost_sales",
    policy="forecast_order_up_to",
    policy_parameters={"target_stock": 0},
)
```

El ejemplo representa un fixture, no un parámetro comercial: demanda y servicio = 6; fill rate = 1; tres órdenes de 2 unidades; recepciones = 4; existencia final = 0; dos unidades siguen en tránsito y llegarían en `t = 3`. La conservación es `2 + 4 - 6 = 0`.

Con demanda `[3, 3, 3]`, stock inicial 0 y filas `[3, 3]`:

- `lost_sales`: se sirven 6, se pierden 3, fill rate = `2/3`, se ordenan 9 unidades y quedan 3 en tránsito.
- `backorder`: se sirven 9 dentro del horizonte, 3 con atraso; fill rate sigue en `2/3`, atraso final = 0, se ordenan 12 y quedan 3 en tránsito.

Esto distingue la atención inmediata del servicio eventual. No permite concluir que una política sea superior en la empresa.

## Validación segura

`scripts/test_experimental_inventory.py` carga el archivo por `importlib` directo, sin ejecutar `app/__init__.py`. Solo emplea fixtures pequeños en memoria, sin base de datos ni ficheros empresariales:

```powershell
.\venv312\Scripts\python.exe scripts/test_experimental_inventory.py
```

Cubre demanda cero/constante, stock suficiente, pérdidas, atrasos, lead time cero/positivo, revisión, ciclos incompletos, efecto de los forecasts, causalidad, conservación por período y total, reproducibilidad, entradas sin defaults, parámetros inválidos y JSON estricto. El hecho de que exista una prueba no implica que haya sido ejecutada: el resultado efectivo se registra al cerrar el work unit.
