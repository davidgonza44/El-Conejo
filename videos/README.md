# Videos tutoriales

Un video MP4 por funcionalidad del sistema (13 en total), hecho con [Remotion](https://www.remotion.dev/): cada
paso muestra una captura real del sistema, se acerca a la zona que importa y la explica con un texto breve.

Las capturas salen de una base SQLite temporal con datos ficticios (`captura/demo_seed.py`): catálogo de
ferretería, clientes, notas de entrega y un ciclo de compra. No se usa la base real ni datos de personas.

## Archivos

| Archivo | Para qué sirve |
|---|---|
| `src/guion.js` | Títulos, roles y texto de cada paso. Es lo que hay que editar para cambiar lo que dice un video. |
| `src/Tutorial.jsx`, `src/tema.js` | Diseño de la portada, los pasos y el cierre, con los colores de `DESIGN.md`. |
| `captura/demo_server.py`, `captura/demo_seed.py` | Servidor de demostración: el arnés E2E con datos ficticios. |
| `captura/capturar.mjs` | Recorre el sistema con Playwright y guarda una captura por paso y la zona a resaltar. |
| `renderizar.mjs` | Genera los MP4 en `out/`. |

`public/`, `src/capturas.json` y `out/` se generan; no se versionan.

## Regenerar

Desde la raíz del repositorio, en una terminal deja corriendo el servidor de demostración:

```bash
E2E_PORT=5098 .venv/Scripts/python -B videos/captura/demo_server.py
```

En otra terminal:

```bash
cd videos
npm install
npm run capturar
npm run renderizar
```

- `npm run renderizar -- productos chatbot` renderiza solo esos videos (los ids están en `src/guion.js`).
- `npm run renderizar -- --fotogramas` guarda fotogramas sueltos en `out/fotogramas/` para revisar sin renderizar todo.
- `npm run estudio` abre Remotion Studio para ver y ajustar los videos en el navegador.
- La captura crea datos (una nota, un movimiento, compras): reinicia el servidor antes de repetirla.
