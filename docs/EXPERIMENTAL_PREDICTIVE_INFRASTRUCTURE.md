# Infraestructura predictiva experimental

Work units A–E, 2026-10-02. Herramientas para evaluación posterior; sin resultados
científicos finales ni modificaciones del protocolo y artefactos congelados.

## Archivos y arquitectura

| Unidad | Servicio nuevo | Test nuevo | Documento nuevo |
|---|---|---|---|
| A | `app/services/scaled_error_service.py` | `scripts/test_scaled_errors.py` | `docs/EXPERIMENTAL_SCALED_ERRORS.md` |
| B | `app/services/experimental_inventory_service.py` | `scripts/test_experimental_inventory.py` | `docs/EXPERIMENTAL_INVENTORY_SIMULATOR.md` |
| C | `app/services/experimental_adida_service.py` | `scripts/test_experimental_adida.py` | `docs/EXPERIMENTAL_ADIDA.md` |
| D | `app/services/experimental_evaluation_service.py` | `scripts/test_experimental_prediction_integration.py` | Este documento |
| E | `app/services/experimental_history_contract.py` | `scripts/test_experimental_history_contract.py` | `docs/EXPERIMENTAL_HISTORY_INPUT_CONTRACT.md` |

También son nuevos `scripts/experimental_prediction_tools.py` (DEMO/auditoría de
entrada) y `scripts/run_experimental_predictive_regressions.py` (pruebas existentes
inspeccionadas con bloqueo de conexiones y archivos privados). Los 17 archivos
son fuente TRACK. No se modifica ningún archivo preexistente.

Los tres servicios numéricos son puros y usan biblioteca estándar. El DTO D
reutiliza únicamente el cálculo de métricas ya existente y añade escalas de
entrenamiento en `scaled_metrics`, manteniendo WAPE en `legacy_metrics`:

```text
TRAIN explícito + actual/predicted -> legacy_metrics + scaled_metrics
TRAIN diario + bucket explícito -> ADIDA -> DTO experimental + errores técnicos
fold empírico existente + historia/calendario explícitos -> suplemento de escalas
demanda + forecasts por origen + escenario explícito -> simulación/traza/métricas
CSV 2023–2024 / CSV 2026 -> validación separada, sin fitting ni comparación
```

`supplement_empirical_fold(series, row, operational_calendar=..., m=1)` no modifica
el fold original ni vuelve a ejecutar modelos. Solo puntúa folds SUCCESS cuya
identidad de train/test concuerda con la serie/calendario; una indisponibilidad
del modelo permanece indisponible. Rechaza diferencias que crucen 2025. Escalas
se calculan por fold con su propio entrenamiento; no se crea una escala conjunta
mezclando targets o folds. No implementa un ranking ni un ganador.

`evaluate_adida()` recibe bucket requerido y forecast anterior al scoring.
Cambiar targets cambia errores, pero no forecast ni configuración. El DTO marca
`scientific_status=infrastructure_only` y `selection_performed=false`. No existe
integración de estos módulos con rutas, selector LIVE, two-part, replenishment
o PurchaseOrder. La carga offline evita ejecutar `app/__init__.py`.

## Fórmulas y límites

Para entrenamiento de tamaño `n`, lag explícito `m` (por defecto 1), error test
`e_i = actual_i - predicted_i` y horizonte `h`:

```text
d_abs = sum(|train_t - train_(t-m)|)/(n-m)
d_sq  = sum((train_t - train_(t-m))²)/(n-m)
MASE  = [sum(|e_i|)/h] / d_abs
RMSSE = sqrt([sum(e_i²)/h] / d_sq)
```

La escala utiliza solo entrenamiento, conforme a la definición de
[Hyndman sobre errores escalados](https://otext.robjhyndman.com/hyndsight/rolling_mase.html).
`<1` indica menor error que el naïve de entrenamiento en esa escala, `>1` mayor.
No equivale a derrotar al naïve sobre el mismo test. Cero/escala indefinida produce
`available=false`, `value=null` y `reason` estructurado, sin épsilon, NaN o Infinity.

Simulación: `IP=on_hand+pipeline-backorder`; `Q=max(0,S-IP)`. Política fija
`S=target_stock`; política por forecast `S=sum(forecast_t)+target_stock`, con buffer
explícito. Pedidos antes de demanda t, entrega completa en t+L. Las métricas y
definiciones exactas de servicio inmediato/diferido, stock, pipeline y CSL están
en el documento B. No se define ni etiqueta exceso de inventario.

ADIDA usa sumas por bloques b explícitos, naïve del último agregado y distribución
uniforme; descarta el prefijo n mod b, y prorratea la cola futura al horizonte.
7/30 son horizontes técnicos; b=7 aparece solo en fixtures. No hay tuning,
inferencias de estacionalidad ni lectura de 2026 para escoger configuración.
ADIDA requiere historia diaria contigua; no comprime ni rellena 2025.

Los servicios no pueden certificar que una entrada declarada real sea completa,
que un forecast externo se generó ex ante o que lead time/stock sean empresariales.
La procedencia, unidades y observación se auditarán antes de una evaluación real.

## Ejemplos DEMO reproducibles

| Fixture | Configuración explícita | Resultado aritmético |
|---|---|---|
| Escalas | train `[1,2,4,7]`, actual `[8,11]`, pred `[7,13]`, m=1 | MASE 0.75; RMSSE sqrt(15/28)≈0.731925 |
| Inventario | demanda `[2,2,2]`, filas forecast `[[2],[2],[2]]`, stock2, L0, R1, lost_sales, política forecast, buffer0 | Demanda/servicio6; fill_rate1; dos pedidos, total4; stockfinal0; residuos contables0 |
| ADIDA | historia `[1,2,3,4,5,6]`, b3, h3 | Agregados `[6,15]`; forecast `[5,5,5]`; total15 |
| ADIDA h30 | 14 cantidades2, b7, h30 | 30 cantidades2; total60; último bloque parcial de dos días total4 |

Todos son cantidades sintéticas; ninguna sirve como evidencia del trabajo de
grado. El CLI DEMO imprime JSON finito y no escribe resultados:

```powershell
.\venv312\Scripts\python.exe scripts/experimental_prediction_tools.py demo
```

## Validación ejecutada

Las suites nuevas se cargan por importlib sin aplicación ni DB. Los tests
existentes del runner usan únicamente paneles sintéticos y directorios temporales,
con entradas/salidas reales parcheadas. El runner de regresiones prohíbe sockets,
conexiones SQLite y lecturas `.env`/instance/uploads/private_imports; no abre DB.

| Comando con `.\venv312\Scripts\python.exe` | Tests escritos | Ejecutados | Aprobados |
|---|---:|---:|---:|
| `scripts/test_scaled_errors.py` | 23 | 23 | 23 |
| `scripts/test_experimental_inventory.py` | 21 | 21 | 21 |
| `scripts/test_experimental_adida.py` | 27 | 27 | 27 |
| `scripts/test_experimental_history_contract.py` | 11 | 11 | 11 |
| `scripts/test_experimental_prediction_integration.py` | 10 | 10 | 10 |
| `scripts/run_experimental_predictive_regressions.py` | 102 existentes | 102 | 102 |

Regresiones: `test_experimental_demand`, `test_forecast_advanced`,
`test_predictive_operational`, `test_empirical_2026_backtest_runner` y
`test_empirical_2026_final_batch`. Resultado conjunto: `Ran 102 tests`, `OK`.
Statsmodels emitió avisos de parámetros iniciales no estacionarios/invertibles
en fixtures ARIMA; no fueron fallos ni ajustes de modelos. Los archivos empíricos
reales y sus resultados congelados no fueron leídos para estas pruebas.

`git diff --check` y `git diff --cached --check` se ejecutan antes de cada commit
aislado. No se tocó JavaScript, por lo que node --check no aplica. No hubo
instalaciones, migraciones, acceso a MySQL/MariaDB, .env, push ni modificación del
tomo, seis modelos, Croston/SBA/TSB, two-part, protocolo o compras reales.

El siguiente work unit requiere fuentes reales 2023–2024: auditoría, selección
histórica, freeze y evaluación externa 2026, luego cobertura por volumen,
calibración, inventario e inferencia estadística. Nada de eso se da por concluido.
