# Plan de implementación: importación histórica 2025

## Alcance aprobado

Completar y verificar la primera versión del módulo de importación histórica para archivos CSV UTF-8 con delimitador `;`, limitada al período 2025 y aislada de las tablas y métricas operativas. No se implementarán predicción, pronósticos ni recomendaciones de reabastecimiento.

## Fases de trabajo

1. Seguridad previa
   - Auditar `.env.example` sin exponer valores y reemplazar únicamente valores no seguros por marcadores.
   - Confirmar que `.env`, almacenamiento privado, temporales, reportes y cachés están ignorados.
   - Buscar posibles credenciales en archivos versionados y producir un informe redactado por archivo y variable.
2. Línea base e invariantes
   - Registrar conteos y huellas de tablas operativas antes de pruebas con base de datos.
   - Confirmar que las pruebas usan transacciones, rollback y almacenamiento temporal.
3. Persistencia histórica
   - Verificar modelos, relaciones, restricciones e índices de las tres tablas históricas.
   - Verificar que la migración manual sea idempotente, no destructiva y no modifique tablas operativas.
4. Backend y seguridad
   - Completar carga privada, límites, parser CSV, normalización, validación, matching y deduplicación.
   - Completar preview, revisión, dry run, token de confirmación, confirmación transaccional, reversión lógica y exportación segura.
   - Verificar autenticación, permisos granulares, errores seguros y ausencia de rutas públicas al archivo.
5. Interfaz
   - Verificar página Tabler, mapping, tablas paginadas, pestañas, acciones y aviso de no modificación del inventario.
6. Pruebas y cierre
   - Ejecutar pruebas estáticas y unitarias seguras primero.
   - Ejecutar la suite integral únicamente con aislamiento y snapshots operativos.
   - Corregir todos los fallos, repetir hasta obtener resultado completo y comprobar limpieza final.

## Criterios de aceptación

- Todos los límites, formatos y reglas de negocio aprobados están cubiertos.
- Cualquier error bloqueante impide toda confirmación.
- Los registros confirmados son inmutables y una reversión es lógica.
- `products.current_stock`, `minimum_stock`, movimientos, notas, usuarios, categorías y KPIs operativos permanecen idénticos.
- No quedan datos ni archivos de prueba.
- `.env` nunca se modifica ni se versiona.
- No se añaden dependencias para XLSX y no existe código predictivo.
- La suite completa pasa antes de declarar el módulo terminado.
