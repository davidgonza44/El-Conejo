# Métricas escaladas experimentales

Infraestructura adicional de evaluación. No modifica WAPE, los seis modelos
históricos, el protocolo congelado, la selección LIVE ni compras. Los ejemplos
de este documento son fixtures sintéticos, no resultados del trabajo de grado.

`app/services/scaled_error_service.py` contiene un servicio puro, determinista
y de biblioteca estándar: `compute(train, actual, predicted, *, m=1)`. No
importa Flask, modelos, configuración ni conexiones. El denominador utiliza
exclusivamente `train`; `actual` y `predicted` tienen igual longitud y sirven
únicamente para el numerador. Los datos deben estar observados y tener la misma
cadencia. Una ausencia (`None`) es inválida: nunca se transforma en cero. El
llamador conserva las fechas y no concatena segmentos a través de períodos no
observados como si fueran contiguos. En particular, 2025 permanece no observado.

## Fórmulas

Sea `n` el número de observaciones de entrenamiento, `h` el de evaluación y
`e_i = actual_i - predicted_i`:

```text
MASE_scale  = sum(|train_t - train_(t-m)|, t=m..n-1) / (n-m)
RMSSE_scale = sum((train_t - train_(t-m))^2, t=m..n-1) / (n-m)
MASE        = [sum(|e_i|, i=0..h-1) / h] / MASE_scale
RMSSE       = sqrt([sum(e_i^2, i=0..h-1) / h] / RMSSE_scale)
```

`m=1` corresponde al benchmark naïve de un paso no estacional. La API admite
un entero positivo explícito para uso futuro, sin inferir estacionalidad ni
buscar el mejor valor. Cualquier justificación futura de `m` debe proceder del
bloque histórico/de desarrollo 2023–2024; 2026 queda fuera del ajuste.

Un valor menor que 1 indica menor error que el benchmark naïve de entrenamiento
en esa escala; mayor que 1 indica mayor error. Esto no equivale a comparar
contra un forecast naïve evaluado sobre el mismo test.

## Contrato y ausencia de escala

El resultado incluye `m`, metadatos `benchmark` (`method`, `lag`,
`scale_source=training_only`) y objetos `mase`/`rmsse`, cada uno con:

| Campo | Significado |
|---|---|
| `available` | Disponibilidad de la métrica |
| `value` | Número finito o `null` |
| `denominator` | Escala finita calculada o `null` si no pudo calcularse |
| `reason` | `null` al estar disponible; objeto con `code` y `description` si no |

Si la escala es cero, devuelve `available=false`, `value=null`,
`denominator=0.0` y `reason.code=zero_training_scale`, incluso con forecast
perfecto. Nunca introduce un épsilon o sustituye el denominador por el test.
Entrenamiento con `n<=m`, evaluación vacía, longitudes distintas, observaciones
ausentes/inválidas/no finitas o `m` inválido producen razones estructuradas.

Overflow y underflow no representables se informan por separado para cada
métrica (`numeric_overflow` / `numeric_underflow`). MASE puede seguir disponible
aunque el cuadrado usado por RMSSE no sea representable. Se normaliza la suma
para evitar que una media representable falle por una suma intermedia grande.
Todo el resultado admite `json.dumps(result, allow_nan=False)`.

## Ejemplo reproducible y pruebas

Para `train=[1,2,4,7]`, `actual=[8,11]`, `predicted=[7,13]`, `m=1`:
`MASE_scale=2`, `RMSSE_scale=14/3`, `MASE=0.75` y
`RMSSE=sqrt(15/28)` (aproximadamente `0.7319250547`).

Para `train=[0,0,4,0,0,2]`, `actual=[0,3,0]`, `predicted=[1,2,0]`:
`MASE_scale=2`, `RMSSE_scale=36/5`, `MASE=1/3` y
`RMSSE=sqrt(5/54)`.

`scripts/test_scaled_errors.py` carga el archivo directamente con `importlib`,
sin importar `app`, leer `.env` ni abrir bases de datos. Sus casos usan solo
listas sintéticas. Comando de validación:

```powershell
.\venv312\Scripts\python.exe scripts/test_scaled_errors.py
```

La existencia de los tests no constituye evidencia de que fueron ejecutados o
aprobados: el reporte de cada work unit registra su ejecución y resultado.
