// Renderiza un MP4 por video en out/. Uso: npm run renderizar [-- id1 id2 ...]
import { bundle } from '@remotion/bundler';
import { renderMedia, renderStill, selectComposition } from '@remotion/renderer';
import fs from 'node:fs';
import path from 'node:path';
import { VIDEOS } from './src/guion.js';

const HERE = import.meta.dirname;
const OUT = path.join(HERE, 'out');
const pedidos = process.argv.slice(2).filter((arg) => !arg.startsWith('--'));
const fotogramas = process.argv.includes('--fotogramas');

fs.mkdirSync(OUT, { recursive: true });
const serveUrl = await bundle({ entryPoint: path.join(HERE, 'src', 'index.jsx') });
for (const [indice, video] of VIDEOS.entries()) {
    if (pedidos.length && !pedidos.includes(video.id)) continue;
    const composition = await selectComposition({ serveUrl, id: video.id, inputProps: { id: video.id } });
    const nombre = `${String(indice + 1).padStart(2, '0')}-${video.id}`;
    if (fotogramas) {
        // Revisión rápida: un fotograma de la portada y uno a mitad de cada paso.
        const marcas = [60, ...video.pasos.map((_, i) => 110 - 15 + i * 155 + 120)];
        for (const frame of marcas) {
            await renderStill({ serveUrl, composition, frame, inputProps: { id: video.id },
                output: path.join(OUT, 'fotogramas', `${nombre}-${frame}.png`) });
        }
        console.log(`fotogramas ${nombre}`);
        continue;
    }
    const inicio = Date.now();
    await renderMedia({ serveUrl, composition, codec: 'h264', crf: 20, inputProps: { id: video.id },
        outputLocation: path.join(OUT, `${nombre}.mp4`) });
    console.log(`listo ${nombre}.mp4 (${Math.round((Date.now() - inicio) / 1000)} s)`);
}
