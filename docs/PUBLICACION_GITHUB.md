# Publicacion del software

Esta rama contiene una instantanea del codigo actual para su publicacion en GitHub.
El historial y el trabajo pendiente de la carpeta original se conservan localmente.

Incluye la aplicacion Flask/Jinja, recursos estaticos, migraciones, Docker,
pruebas y documentacion tecnica revisada. No incluye credenciales, bases de
 datos, archivos subidos por usuarios, entornos virtuales, temporales,
reportes locales, manuscritos ni tesis de terceros. Algunos scripts antiguos
con credenciales incrustadas se conservan localmente pendientes de revision.
Las rutas absolutas locales de la documentacion se sustituyeron por marcadores.

## Comprobaciones realizadas

- CodeGraph doctor y OpenSpec doctor: correctos.
- Revision de sintaxis Python: correcta.
- Suite aislada PDF: 41 comprobaciones correctas.
- Suite aislada importaciones historicas: 102 comprobaciones correctas y dos
  fallos derivados de un ConnectionError; la ejecucion no completo todas las fases.
- Suite de preparacion predictiva: fallo al crear el fixture por falta del
  permiso delivery_notes:create y no completo la limpieza esperada.

Estos resultados documentan el estado recibido del proyecto. La publicacion
no incorpora cambios funcionales para corregir esas suites.

## Revision de privacidad

Se revisaron las coincidencias de contrasenas: solo permanecen datos sinteticos
de fixtures y claves de pruebas aisladas, o prefijos completados con aleatoriedad.
Se excluyeron scripts antiguos con accesos fijos, documentos de terceros,
manuscritos e informes generados. La rama publicada parte de un commit nuevo,
sin los commits locales anteriores, para que los archivos excluidos tampoco
viajen en su historial. La carpeta original y sus commits se conservan.
