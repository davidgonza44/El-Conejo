# Contrato de entrada para historia 2023–2024 y evaluación externa 2026

Estado: infraestructura experimental. No es una auditoría histórica concluida,
un nuevo protocolo congelado ni evidencia científica. No se han recibido ni
procesado aquí los archivos empresariales de 2023–2024. El contrato valida
declaraciones y estructura; no acredita por sí solo que una fuente sea real o
completa. Esa verificación pertenece al siguiente work unit.

## Formato normalizado requerido

CSV UTF-8 (BOM permitido), encabezado obligatorio, separador coma, una fila por
fecha comercial/producto/almacén. La granularidad es diaria. Las cantidades usan
punto decimal en CSV, sin separador de miles. No se agregan productos, almacenes
o unidades diferentes como si fueran homogéneos. El código se conserva como
texto, incluidos sus ceros iniciales.

| Columna | Requisito |
|---|---|
| `date` | Fecha comercial local, ISO estricto `YYYY-MM-DD`. Si la fuente trae hora, convertir y auditar primero con `America/Caracas`; este contrato no acepta timestamps. |
| `product_code` | Identificador estable en texto, conciliado mediante un catálogo/mapeo auditado, sin renumeración silenciosa. |
| `warehouse_code` | Identificador explícito del almacén, estable entre años. No hay almacén por defecto. |
| `unit` | Unidad de la cantidad, estable por producto/almacén. Conversiones de empaque/unidad se auditan antes. |
| `quantity` | Demanda observada finita y no negativa. `0` solo si la cobertura del día/producto está demostrada. Vacío para período no observado. No es stock ni compras. |
| `observation_status` | `observed` o `unobserved`. No inferir observado por ausencia de ventas. |
| `operational_status` | `open`, `closed` o `unknown`, sustentado por calendario/evidencia del almacén. |
| `source_id` | Referencia trazable a la fuente/lote auditado, sin incluir credenciales o rutas privadas. |
| `data_kind` | `real` para futuras entradas empresariales; `synthetic` solo con opt-in técnico explícito y nunca como evidencia. |
| `observation_basis` | Referencia a evidencia de cobertura completa o causa de ausencia/cierre; texto explícito, no una suposición automática. |

Columnas adicionales pueden conservarse para la auditoría. Se rechazan columnas
requeridas ausentes, duplicados por fecha/producto/almacén, cantidades inválidas,
cambios de unidad y calendarios contradictorios de un mismo almacén/día. Los
identificadores no se fusionan ni corrigen automáticamente.

## Días operativos y períodos no observados

Un día con movimientos válidos demuestra actividad, pero no demuestra cobertura
completa de todos los productos ni que todos los días sin movimientos estuvieran
cerrados. La auditoría deberá contrastar calendario de apertura, cierres reales,
documentos comerciales y completitud de cada extracción. No se codifica un
calendario fijo de lunes a sábado ni se infiere apertura de los días con ventas.

Una fila `observed/open` requiere una cantidad explícita y evidencia de cobertura.
Un día `unobserved/open` o `unobserved/unknown` conserva `quantity` vacía y corta el
segmento. Un cierre confirmado se representa `unobserved/closed` con cantidad
vacía: permite enlazar observaciones consecutivas en **tiempo operativo**, sin
añadir un cero ni convertirlo en demanda observada. Un día ausente del CSV corta
el segmento; no se crea una fila automáticamente. No se extiende hacia atrás
una serie antes de su primera fecha con cobertura demostrada.

`experimental_history_contract.validate_partition()` devuelve segmentos de días
operativos observados y las filas explícitas de calendario. `prepare_inputs()`
mantiene dos roles separados:

```text
2023–2024 -> development_history (historia/desarrollo)
2025      -> unobserved_periods, quantity=null; nunca entra como observación
2026      -> external_evaluation (bloque externo separado)
```

Cada partición rechaza años ajenos a su rol. Tampoco acepta filas sintéticas de
2025 o `Existencias_2025` como demanda/evidencia. No concatena el cierre de 2024
con 2026, no interpola el hueco y no calcula diferencias naïve cruzándolo.

La cantidad registrada de ventas no prueba demanda latente cuando hubo faltantes.
La auditoría futura deberá documentar esa limitación, además de anulaciones,
devoluciones, movimientos internos, ventas de servicios y duplicados. Este
contrato espera demanda ya conciliada; no inventa conversiones ni corrige stock.

## Compatibilidad con las herramientas

MASE/RMSSE admiten una secuencia con cadencia común declarada. El adaptador
`supplement_empirical_fold()` exige un calendario operativo explícito y conserva
las métricas del fold original. No se juntan segmentos desconectados ni se
promedian métricas con escalas distintas sin una regla futura predeclarada.

ADIDA y `evaluate_forecast()` admiten por ahora **días calendario consecutivos**.
Un segmento operativo que salta un cierre no se convierte en calendario cambiando
fechas ni rellenando el cierre con cero. Se utiliza únicamente un tramo diario
contiguo con cobertura; si no lo hay, ADIDA queda unavailable. La adaptación de
ADIDA a tiempo operativo requiere una decisión posterior explícita.

El simulador acepta períodos consecutivos de la misma unidad temporal declarada
en el escenario; stock inicial, lead time, revisión, política y nivel objetivo
se suministran por separado. No se extraen de valores empresariales supuestos.
Cada fila de forecasts debe haberse generado antes de la demanda de su origen.

## Uso futuro sin comparación final

`scripts/experimental_prediction_tools.py audit-inputs` solo lee los dos CSV
explícitos, valida el contrato y muestra conteos sin cantidades/identidades
privadas. No importa la aplicación Flask, abre DB, entrena modelos, calcula
resultados externos, elige bucket ni escribe artefactos. El CLI no admite
synthetic; los tests sí lo habilitan expresamente para fixtures.

Ejemplo de invocación futura, con nombres ilustrativos y sin ejecución actual:

```powershell
.\venv312\Scripts\python.exe scripts/experimental_prediction_tools.py audit-inputs --development historia_normalizada.csv --external externo_normalizado.csv
```

Cuando lleguen fuentes reales: auditar 2023–2024, seleccionar/configurar usando
solo ese bloque, congelar reglas y después autorizar validación externa 2026.
Este work unit no ejecuta selección, calibración, bootstrap, significancia ni
conclusiones finales. Los resultados de `prepare_inputs()` declaran
`final_comparison_executed=false` y no se marcan como evidencia científica.
