# Carga local de tesis — 22/09/2026

Se creó `ferreteria_conejo_tesis` en 127.0.0.1:3306, servidor XAMPP
MariaDB 10.4.32, con `utf8mb4_unicode_ci`. Se generaron las 40 tablas de
modelos mediante SQLAlchemy `metadata.create_all()`, sin semillas genéricas ni
copias de otra base. `prepare_seed_tesis.py` preparó la tabla 41, `seed_manifests`.
La consulta de metadatos confirmó que el manifiesto existe en la base de tesis
y no en `ferreteria_conejo`.

`seed_tesis.py` se ejecutó **una sola vez**, salida 0, `SEED COMPLETADO`.
No se ejecutó reset. Los datos comerciales y las reglas predictivas aprobadas
no se modificaron. Todas las escrituras de esta operación se dirigieron a la
base de tesis; no se seleccionó ni se escribió la base técnica.

## Conteos persistidos

| Entidad | Registros |
|---|---:|
| Categorías | 10 |
| Productos | 120 |
| Proveedores | 10 |
| Clientes | 60 |
| Usuarios | 3 |
| Ventas / líneas | 1.571 / 3.783 |
| Cotizaciones / líneas | 40 / 80 |
| Pedidos / líneas | 40 / 80 |
| Órdenes de compra / líneas | 351 / 926 |
| Recepciones / líneas | 331 / 906 |
| Movimientos | 4.817 |
| Manifiestos internos | 1 |

El stock surge de 120 entradas iniciales, 906 entradas de recepción,
3.783 salidas de venta y ocho ajustes de una unidad.

## Validación de solo lectura

La utilidad abre conexiones con `SET SESSION TRANSACTION READ ONLY` y rechaza
SQL distinto de SELECT/SHOW/DESCRIBE. Verifica base efectiva, huellas del
manifiesto, conteos, claves foráneas de todos los modelos, saldos cronológicos,
stock no negativo, totales y relaciones de documentos, fechas, actores y pagos.
Reutiliza ocho comprobaciones de integración de la suite aprobada.

- 18 elegibles, 60 intermitentes, 13 insuficientes, 12 inactivos por demanda,
  17 sin demanda: coincide con la simulación.
- 82 productos con historia suficiente; 65 admiten estimación descriptiva.
- **5 agotados**, uno positivo inferior al mínimo y 15 exactamente en el mínimo.
  La última solicitud mencionaba tres agotados, pero la simulación final aprobada
  tenía cinco. Se conservó esa versión sin corregir artificialmente el stock.
- 42 candidatos a reabastecimiento usando la misma aproximación de demanda media,
  plazo de siete días y compras pendientes. No equivale a un forecast entrenado.
- Categorías, tickets por categoría y pagos coinciden con
  [la simulación final](SEED_TESIS_SIMULATION.md).
- Pagos: efectivo 505, transferencia 693, débito 311, crédito 62.
- Compras: 321 completas, diez parciales, diez pendientes y diez canceladas.
- Cero etiquetas prohibidas en 13.128 registros y 139.692 campos de 16 tablas;
  solo se excluye el manifiesto. La búsqueda ignora mayúsculas y acentos e
  incluye las raíces que cubren «Operador sintético» y las variantes de DEMO.
- Sin registros de importación ni demanda histórica científica.

## Páginas

Prueba con cliente HTTP de Flask y datos reales de la base de tesis. Principal
administrativo transitorio exclusivamente en el proceso de verificación, sin
crear credenciales ni sesiones persistidas. No verifica un login humano.

| Ruta | Resultado |
|---|---|
| /dashboard | 200 |
| /products | 200 |
| /inventory | 200 |
| /sales | 200 |
| /sales/quotes | 200 |
| /sales/orders | 200 |
| /reports | 302 a /dashboard, luego 200: comportamiento real existente |
| /predictive/demand | 200 |
| /predictive/replenishment | 200 |
| /predictive/critical | 200 |
| /predictive/trends | 200 |
| /predictive/accuracy | 200 |
| /api/dashboard/summary | 200 |
| /reports/sales | 200 |

Sin errores SQL/QueuePool en las respuestas. Se verificó la respuesta inicial
del módulo predictivo; no se esperó su cálculo operativo pesado. Su caché usa
un directorio temporal propio. No se ejecutó backtesting científico ni se
leyeron o modificaron resultados empíricos. No se tomaron capturas.

## Cambios e incidencias

- `seed_tesis_safety.py`: contraseña vacía requiere autorización explícita;
  root/3306 obligatorios; JSON de MariaDB exige LONGTEXT con json_valid(payload).
- `test_seed_tesis.py`: casos de contraseña y compatibilidad JSON. Las 24 pruebas
  pasaron en 123,896 s, salida 0 (las 22 anteriores más dos nuevas).
- `prepare_seed_tesis_database.py`: creación explícita y restringida de base y
  esquema vacío. Rechaza esquemas preexistentes para evitar cambios implícitos.
- `verify_seed_tesis_mysql.py`: auditoría persistida y páginas en modo solo lectura.
- `serve_seed_tesis.py`: inicio de la aplicación con destino comprobado, sin dotenv.
- `SEED_TESIS.md` y este informe: instrucciones y resultados.

La preparación inicial detectó una conexión que seleccionaba una base aún
inexistente; se corrigió antes de crearla. MariaDB refleja JSON como LONGTEXT;
se añadió la comprobación estricta de su restricción sin alterar la tabla.
La consulta de auditoría requirió alias para relaciones autorreferenciadas.
El primer cierre de la prueba HTTP encontró un archivo temporal aún abierto
por el trabajador predictivo: se acotó su espera y se tolera limpieza diferida
del temporal al salir. Ninguna incidencia requirió reejecutar el seed.

El entorno `.venv` sirve para el seed y sus pruebas, pero carece de sklearn.
Se usó `venv312`, ya instalado con las dependencias predictivas, para validar
la aplicación. No se instalaron paquetes ni se modificó código funcional.
Persisten avisos de deprecación de datetime.utcnow() en código existente.

`seed_large_demo.py` conserva SHA-256
`13062DD0825DF784188A81E5B61F4617EEC421E5600E57CB573615F0827B274A`.
No se leyó `.env`, no hubo commit/push ni operaciones Git destructivas.

## Comandos exactos

Desde `<directorio-del-proyecto>`, las variables de esta sesión son:

```powershell
$env:DB_HOST = 'localhost'
$env:DB_PORT = '3306'
$env:DB_NAME = 'ferreteria_conejo_tesis'
$env:DB_USER = 'root'
$env:DB_ALLOW_EMPTY_PASSWORD = '1'
```

Preparación ejecutada: `.\.venv\Scripts\python.exe -B scripts/prepare_seed_tesis_database.py`.
Manifiesto: `.\.venv\Scripts\python.exe -B scripts/prepare_seed_tesis.py`.
Carga única ejecutada: `.\.venv\Scripts\python.exe -B scripts/seed_tesis.py`.
Suite: `.\.venv\Scripts\python.exe -B scripts/test_seed_tesis.py`.
Verificación: `.\venv312\Scripts\python.exe -u -B scripts/verify_seed_tesis_mysql.py`.

Para iniciar Flask después, con las cinco variables anteriores:

```powershell
.\venv312\Scripts\python.exe -B scripts/serve_seed_tesis.py
```

Dirección: http://127.0.0.1:5000. Sin debug/reloader y sin lectura de `.env`.
Las URL alternativas DATABASE_URL/SQLALCHEMY_DATABASE_URI deben estar ausentes
o vacías; si existen, la herramienta aborta. Los tres actores siguen sin
contraseña utilizable: no se creó un acceso humano durante esta operación.
