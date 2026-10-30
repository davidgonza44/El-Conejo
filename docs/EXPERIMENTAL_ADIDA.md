# ADIDA: candidato experimental de agregación temporal

Este módulo es infraestructura técnica para una evaluación posterior. No contiene
resultados científicos, selección de modelo ni una configuración óptima. No se
conecta al selector LIVE, al servicio de reabastecimiento ni a órdenes de compra.
Los seis modelos históricos, Croston/SBA/TSB y two-part conservan su comportamiento.

El esquema agregación → extrapolación → desagregación sigue la propuesta de
[Nikolopoulos, Syntetos, Boylan, Petropoulos y Assimakopoulos (2011)](https://www.tandfonline.com/doi/full/10.1057/jors.2010.32).
Esta implementación inicial fija la extrapolación en el naïve del último bloque
agregado y la desagregación uniforme. Es una variante explícita de ese esquema,
no una reproducción de las configuraciones empíricas del artículo.

## API pura

Archivo: `app/services/experimental_adida_service.py`.

```python
adida(
    history,
    aggregation_size=7,  # DEMO: explícito, no recomendado ni optimizado
    horizon=30,         # días calendario desde el origen
    starts=[...],       # una fecha por cantidad observada
    forecast_origin="2024-01-15",
)
```

`history` es una secuencia de cantidades finitas y no negativas. `starts` exige
fechas ISO `YYYY-MM-DD` o `datetime.date`, ordenadas, únicas y diarias contiguas.
`forecast_origin` es el primer día pronosticado; todas las observaciones deben
ser estrictamente anteriores y la última debe ser del día anterior al origen.
No se realiza ordenamiento implícito. `aggregation_size` y `horizon` son enteros
positivos requeridos: no hay búsqueda de configuraciones ni parámetros elegidos
según errores futuros.

La única granularidad admitida actualmente es el día calendario. No se comprimirá
un calendario a días con ventas. El contrato histórico experimental actual
representa cierres confirmados sin cantidad; ADIDA requiere un tramo diario
contiguo observado y no convierte esos cierres en ceros. Un día desconocido
conserva su condición de no observado. El módulo rechaza gaps,
observaciones ausentes y rezago al origen. No genera esas observaciones. En
particular, no permite concatenar el cierre de 2024 con 2026 ignorando 2025.

## Bloques, horizontes y fórmulas

Para `n` cantidades observadas y tamaño explícito `b`, se descarta el prefijo más
antiguo de `r = n mod b` observaciones. Los bloques completos se anclan al origen
hacia atrás. El prefijo descartado se informa; no se completa con ceros, no se
estima y no influye en el forecast. Si `n < b`, el candidato queda unavailable.
Si falta el último día antes del origen, queda unavailable: no se inventa un
bloque final de entrenamiento. Este anclaje evita bloques recientes parciales.

Cada bloque completo se agrega con `A_j = sum(y_t)` dentro de sus `b` días.
El extrapolador naïve da `A_hat = A_último` para cada bloque futuro. Se distribuye
uniformemente: `y_hat = A_hat / b` por día. Para los `p < b` días del último
bloque futuro, su total es `A_hat * p / b`. No se prolonga el horizonte para
completar ese bloque. Se corrige el residuo de punto flotante en la última
cantidad de cada bloque, conservando su total dentro de precisión numérica.

Con `b=7`, un horizonte de 7 días tiene un bloque; 30 días tienen cuatro bloques
completos y dos días del quinto. El 7 es solo un fixture mecánico. Para otros
tamaños rige la misma fórmula. No se divide un total de 30 días entre 7 ni se
confunde un total agregado con una cantidad diaria.

## DTO y estados

El DTO contiene `model=adida`, `experimental=true`, `available`,
`aggregation_size`, `aggregated_observations`, `horizon`,
`horizon_unit=calendar_day`, `forecast`, `forecast_dates`, `forecast_total`,
`base_method=naive_last_aggregate`, `disaggregation_method=uniform`,
`partial_block_policy=origin_aligned_drop_oldest` y `unavailable_reason`.

`metadata` expone cantidades de entrenamiento, fechas usadas, origen, cantidad
descartada del prefijo, totales históricos agregados y períodos/totales de los
bloques futuros. No hay métricas de selección ni flags que habiliten compras.

En unavailable, `forecast=[]`, `forecast_total=null` y
`unavailable_reason={"code": ..., "message": ...}`. Los motivos incluyen
configuración inválida, observación inválida, fechas inválidas, historia futura,
historia desordenada, gap no observado, último período no observado, historia
insuficiente y desborde numérico/de fechas. Las cantidades inválidas no se
reflejan literalmente en el DTO: es serializable con `allow_nan=False`.
Una serie all-zero efectivamente observada sí produce un forecast cero válido.

## Separación temporal

La API recibe exclusivamente historia antes del origen; rechaza una observación
en el origen o posterior. No recibe demanda futura de test ni objetivos de
optimización. No ejecuta grid search, no compara tamaños de bloque ni modifica
el protocolo congelado. El contrato histórico debe seleccionar un segmento
contiguo observado antes de invocarla; no puede salvar 2025 rellenándolo.

Cuando existan los archivos reales de 2023–2024, ese bloque se utilizará para
historia/desarrollo y una selección documentada de bucket. Las reglas se
congelarán antes de evaluar el bloque externo de 2026. Estas pruebas técnicas
no autorizan tuning retrospectivo sobre 2026, resultados finales o uso de
2025 sintético como evidencia.

## Fixtures DEMO reproducibles

Todos los ejemplos son cantidades inventadas para probar aritmética, sin datos
empresariales ni acceso a DB. Las fechas de 2024 son etiquetas de fixture.

| Historia | b | Horizonte | Resultado esperado |
|---|---:|---:|---|
| `[1,2,3,4,5,6]` | 3 | 3 | Bloques `[6,15]`; forecast `[5,5,5]`; total 15 |
| `[900,800,1,2,3,4,5,6]` | 3 | 3 | Descarta prefijo de 2; mismo resultado |
| 14 días de cantidad 2 | 7 | 7 | Bloques `[14,14]`; 7 días de 2; total 14 |
| 14 días de cantidad 2 | 7 | 30 | 30 días de 2; totales `[14,14,14,14,4]`; total 60 |
| `[0,0,0,8,0,0,0]` dos veces | 7 | 7 | Bloques `[8,8]`; cantidad diaria 8/7; total 8 |
| 14 ceros observados | 7 | 30 | 30 ceros; available |

Ejecución segura, con biblioteca estándar y carga del archivo por `importlib`
sin importar el paquete `app` ni crear una aplicación:

```powershell
.\venv312\Scripts\python.exe scripts/test_experimental_adida.py
```

También admite ejecución selectiva de una fixture:

```powershell
.\venv312\Scripts\python.exe scripts/test_experimental_adida.py ExperimentalAdidaTests.test_30_calendar_days
```

La suite cubre bloques completos/parciales, 7/30 días, all-zero, intermitencia,
conservación de totales, rechazo de leakage y gaps (incluido 2025 no observado),
parámetros/fechas inválidos, JSON finito, desbordes, determinismo e inmutabilidad.
La existencia de estas pruebas no equivale a su ejecución ni a validación
científica. Los resultados ejecutados se reportan en el cierre del work unit.
