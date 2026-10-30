// Tokens de DESIGN.md («La carta de colores») y tiempos de los videos (30 fps).
import { loadFont as loadHanken } from '@remotion/google-fonts/HankenGrotesk';
import { loadFont as loadMono } from '@remotion/google-fonts/JetBrainsMono';

export const FUENTE = loadHanken('normal', { weights: ['400', '500', '600', '700'], subsets: ['latin', 'latin-ext'] }).fontFamily;
export const MONO = loadMono('normal', { weights: ['500'], subsets: ['latin'] }).fontFamily;

export const C = {
    papel: '#f3f4f1', riel: '#eceee9', superficie: '#ffffff', tinte: '#f7f8f5',
    regla: '#e2e4df', reglaFuerte: '#cdd1ca', tinta: '#191c1a', tinta2: '#3a403c', apagado: '#626a65',
    naranja: '#b45309', naranjaViva: '#e07b10', naranjaSuave: '#fbf4ec', cielo: '#1f6fa8',
    ok: '#1e7a45', advertencia: '#c98c00', peligro: '#b42318', morado: '#7a5af0',
};

export const FPS = 30;
export const ANCHO = 1920;
export const ALTO = 1080;
export const T = { portada: 110, paso: 170, cierre: 95, transicion: 15 };

export const duracion = (video) =>
    T.portada + video.pasos.length * T.paso + T.cierre - (video.pasos.length + 1) * T.transicion;
