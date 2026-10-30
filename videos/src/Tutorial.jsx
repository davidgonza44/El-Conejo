import { AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig, Easing } from 'remotion';
import { TransitionSeries, linearTiming } from '@remotion/transitions';
import { fade } from '@remotion/transitions/fade';
import { slide } from '@remotion/transitions/slide';
import capturas from './capturas.json';
import { C, FUENTE, MONO, T } from './tema.js';
import { VIDEOS } from './guion.js';

const MUESTRAS = [C.naranja, C.cielo, C.ok, C.advertencia, C.peligro, C.morado];
const VISTA = { x: 600, y: 128, ancho: 1240, barra: 46 };
VISTA.alto = Math.round((VISTA.ancho * 900) / 1440);
const K = VISTA.ancho / 1440; // px de pantalla capturada -> px del video

const entrada = (frame, desde, fps) => spring({ frame: frame - desde, fps, config: { damping: 200 } });

function Muestra({ color, size = 14, style }) {
    return <span style={{ display: 'inline-block', width: size, height: size, borderRadius: size / 4, background: color,
        boxShadow: 'inset 0 0 0 1px rgba(25,28,26,.12)', flex: 'none', ...style }} />;
}

function Marca({ opacity = 1 }) {
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 18, opacity }}>
            <Img src={staticFile('logo.png')} style={{ width: 64, height: 64, objectFit: 'contain', borderRadius: 12,
                background: C.superficie, border: `1px solid ${C.regla}` }} />
            <div style={{ lineHeight: 1.2 }}>
                <div style={{ fontSize: 26, fontWeight: 700, color: C.tinta }}>El Conejo</div>
                <div style={{ fontSize: 18, color: C.apagado }}>Ferretería y Construcciones El Conejo C.A.</div>
            </div>
        </div>
    );
}

function Portada({ video, numero, total }) {
    const frame = useCurrentFrame();
    const { fps } = useVideoConfig();
    const sube = (desde) => ({ opacity: entrada(frame, desde, fps),
        transform: `translateY(${interpolate(entrada(frame, desde, fps), [0, 1], [28, 0])}px)` });
    return (
        <AbsoluteFill style={{ background: C.papel, padding: '110px 140px', fontFamily: FUENTE, justifyContent: 'space-between' }}>
            <div style={sube(0)}><Marca /></div>
            <div>
                <div style={{ ...sube(6), fontFamily: MONO, fontSize: 24, color: C.naranja, letterSpacing: '.04em', marginBottom: 22 }}>
                    TUTORIAL {String(numero).padStart(2, '0')} DE {total}
                </div>
                <h1 style={{ ...sube(10), margin: 0, fontSize: 112, lineHeight: 1.02, fontWeight: 700, letterSpacing: '-0.035em', color: C.tinta }}>
                    {video.titulo}
                </h1>
                <p style={{ ...sube(18), margin: '28px 0 0', fontSize: 38, color: C.tinta2, maxWidth: 1250, lineHeight: 1.3 }}>
                    {video.resumen}
                </p>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', gap: 14 }}>
                    {MUESTRAS.map((color, i) => {
                        const s = entrada(frame, 24 + i * 4, fps);
                        return <Muestra key={color} color={color} size={44} style={{ transform: `scale(${s})`, opacity: s }} />;
                    })}
                </div>
                <div style={{ ...sube(30), fontSize: 26, color: C.tinta2, background: C.superficie, border: `1px solid ${C.regla}`,
                    borderRadius: 10, padding: '14px 22px' }}>
                    Lo usa: <strong style={{ color: C.tinta }}>{video.rol}</strong>
                </div>
            </div>
        </AbsoluteFill>
    );
}

/** Cámara: muestra la pantalla completa y luego se acerca a la zona del paso. */
function camara(zona, progreso) {
    if (!zona) return { escala: 1 + 0.05 * progreso, x: 0, y: 0 };
    const w = zona.w * K, h = zona.h * K;
    const meta = Math.min(1.85, Math.max(1, Math.min((VISTA.ancho * 0.78) / w, (VISTA.alto * 0.72) / h)));
    const escala = 1 + (meta - 1) * progreso;
    const cx = (zona.x + zona.w / 2) * K, cy = (zona.y + zona.h / 2) * K;
    // Lleva el centro de la zona al centro de la vista sin dejar ver bordes vacíos.
    const limite = (centro, lado) => Math.min(0, Math.max(lado - lado * escala, lado / 2 - centro * escala));
    return { escala, x: limite(cx, VISTA.ancho), y: limite(cy, VISTA.alto) };
}

function Cursor({ frame, zona, escala }) {
    if (!zona) return null;
    const llegada = interpolate(frame, [22, 58], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
        easing: Easing.bezier(0.33, 1, 0.68, 1) });
    const destino = { x: (zona.x + zona.w / 2) * K, y: (zona.y + zona.h / 2) * K };
    const x = interpolate(llegada, [0, 1], [VISTA.ancho * 0.92, destino.x]);
    const y = interpolate(llegada, [0, 1], [VISTA.alto * 0.95, destino.y]);
    const clic = interpolate(frame, [60, 80], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
    const tam = 30 / escala;
    return (
        <div style={{ position: 'absolute', left: x, top: y, opacity: interpolate(frame, [18, 26], [0, 1], { extrapolateRight: 'clamp' }) }}>
            {clic > 0 && clic < 1 && <span style={{ position: 'absolute', left: -tam, top: -tam, width: tam * 2, height: tam * 2,
                borderRadius: '50%', border: `${3 / escala}px solid ${C.naranjaViva}`, opacity: 1 - clic, transform: `scale(${0.4 + clic})` }} />}
            <svg width={tam} height={tam * 1.3} viewBox="0 0 20 26" style={{ position: 'absolute', left: -2 / escala, top: -2 / escala }}>
                <path d="M2 2 L2 21 L7 16.5 L10.5 24 L14 22.5 L10.5 15 L17 15 Z" fill={C.tinta} stroke="#fff" strokeWidth="1.6" strokeLinejoin="round" />
            </svg>
        </div>
    );
}

function Paso({ video, indice }) {
    const frame = useCurrentFrame();
    const { fps } = useVideoConfig();
    const paso = video.pasos[indice];
    const captura = capturas[paso.captura];
    const zona = captura?.zona ?? null;
    const progreso = interpolate(frame, [26, 70], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
        easing: Easing.bezier(0.65, 0, 0.35, 1) });
    const cam = camara(zona, progreso);
    const resalte = interpolate(frame, [62, 78], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
    const texto = (desde) => ({ opacity: entrada(frame, desde, fps),
        transform: `translateX(${interpolate(entrada(frame, desde, fps), [0, 1], [-24, 0])}px)` });
    return (
        <AbsoluteFill style={{ background: C.papel, fontFamily: FUENTE }}>
            <div style={{ position: 'absolute', left: 90, top: VISTA.y, width: 450, height: VISTA.alto + VISTA.barra,
                display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                <div style={{ ...texto(4), display: 'flex', alignItems: 'center', gap: 12, fontSize: 22, fontWeight: 600, color: C.naranja }}>
                    <Muestra color={C.naranja} size={16} />Paso {indice + 1} de {video.pasos.length}
                </div>
                <h2 style={{ ...texto(8), margin: '18px 0 0', fontSize: 54, lineHeight: 1.08, fontWeight: 700, letterSpacing: '-0.025em', color: C.tinta }}>
                    {paso.titulo}
                </h2>
                <p style={{ ...texto(14), margin: '24px 0 0', fontSize: 31, lineHeight: 1.42, color: C.tinta2 }}>{paso.texto}</p>
                <div style={{ ...texto(20), display: 'flex', gap: 8, marginTop: 40 }}>
                    {video.pasos.map((_, i) => <span key={i} style={{ height: 6, flex: 1, maxWidth: 56, borderRadius: 3,
                        background: i <= indice ? C.naranja : C.reglaFuerte }} />)}
                </div>
            </div>
            <div style={{ position: 'absolute', left: VISTA.x, top: VISTA.y, width: VISTA.ancho, background: C.superficie,
                borderRadius: 16, border: `1px solid ${C.reglaFuerte}`, overflow: 'hidden',
                opacity: entrada(frame, 0, fps), transform: `translateY(${interpolate(entrada(frame, 0, fps), [0, 1], [20, 0])}px)` }}>
                <div style={{ height: VISTA.barra, display: 'flex', alignItems: 'center', gap: 16, padding: '0 18px',
                    background: C.tinte, borderBottom: `1px solid ${C.regla}` }}>
                    <div style={{ display: 'flex', gap: 8 }}>{[0, 1, 2].map((i) => <Muestra key={i} color={C.reglaFuerte} size={12} style={{ borderRadius: 6 }} />)}</div>
                    <div style={{ flex: 1, maxWidth: 620, fontFamily: MONO, fontSize: 17, color: C.apagado, background: C.superficie,
                        border: `1px solid ${C.regla}`, borderRadius: 8, padding: '5px 14px' }}>
                        elconejo.local{captura?.url ?? ''}
                    </div>
                </div>
                <div style={{ position: 'relative', width: VISTA.ancho, height: VISTA.alto, overflow: 'hidden', background: C.papel }}>
                    <div style={{ position: 'absolute', inset: 0, transformOrigin: '0 0',
                        transform: `translate(${cam.x}px, ${cam.y}px) scale(${cam.escala})` }}>
                        {captura
                            ? <Img src={staticFile(captura.archivo)} style={{ width: VISTA.ancho, height: VISTA.alto, display: 'block' }} />
                            : <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', fontSize: 28, color: C.apagado }}>Captura pendiente</AbsoluteFill>}
                        {zona && <div style={{ position: 'absolute', left: zona.x * K - 6, top: zona.y * K - 6,
                            width: zona.w * K + 12, height: zona.h * K + 12, borderRadius: 10,
                            border: `${3 / cam.escala}px solid ${C.naranjaViva}`, opacity: resalte,
                            boxShadow: `0 0 0 ${4000}px rgba(25,28,26,${0.22 * resalte})` }} />}
                        <Cursor frame={frame} zona={zona} escala={cam.escala} />
                    </div>
                </div>
            </div>
        </AbsoluteFill>
    );
}

function Cierre({ video, numero }) {
    const frame = useCurrentFrame();
    const { fps } = useVideoConfig();
    const siguiente = VIDEOS[numero];
    return (
        <AbsoluteFill style={{ background: C.papel, fontFamily: FUENTE, alignItems: 'center', justifyContent: 'center', gap: 46 }}>
            <div style={{ opacity: entrada(frame, 0, fps) }}><Marca /></div>
            <div style={{ textAlign: 'center', opacity: entrada(frame, 6, fps) }}>
                <div style={{ fontSize: 64, fontWeight: 700, letterSpacing: '-0.03em', color: C.tinta }}>{video.titulo}</div>
                <div style={{ marginTop: 18, fontSize: 32, color: C.tinta2 }}>
                    Más detalle en el menú: <strong style={{ color: C.naranja }}>Guía de uso</strong>
                </div>
            </div>
            {siguiente && <div style={{ opacity: entrada(frame, 14, fps), fontSize: 24, color: C.apagado, display: 'flex', alignItems: 'center', gap: 12 }}>
                <Muestra color={C.cielo} size={14} />Siguiente: {siguiente.titulo}
            </div>}
        </AbsoluteFill>
    );
}

function Progreso() {
    const frame = useCurrentFrame();
    const { durationInFrames } = useVideoConfig();
    return <div style={{ position: 'absolute', left: 0, bottom: 0, height: 6, background: C.naranja,
        width: `${(frame / (durationInFrames - 1)) * 100}%` }} />;
}

export function Tutorial({ id }) {
    const numero = VIDEOS.findIndex((video) => video.id === id) + 1;
    const video = VIDEOS[numero - 1];
    const transicion = (presentation) => (
        <TransitionSeries.Transition presentation={presentation} timing={linearTiming({ durationInFrames: T.transicion })} />
    );
    return (
        <AbsoluteFill style={{ background: C.papel }}>
            <TransitionSeries>
                <TransitionSeries.Sequence durationInFrames={T.portada}>
                    <Portada video={video} numero={numero} total={VIDEOS.length} />
                </TransitionSeries.Sequence>
                {video.pasos.flatMap((paso, indice) => [
                    <TransitionSeries.Transition key={`t${indice}`} presentation={indice ? slide({ direction: 'from-right' }) : fade()}
                        timing={linearTiming({ durationInFrames: T.transicion })} />,
                    <TransitionSeries.Sequence key={paso.captura} durationInFrames={T.paso}>
                        <Paso video={video} indice={indice} />
                    </TransitionSeries.Sequence>,
                ])}
                {transicion(fade())}
                <TransitionSeries.Sequence durationInFrames={T.cierre}>
                    <Cierre video={video} numero={numero} />
                </TransitionSeries.Sequence>
            </TransitionSeries>
            <Progreso />
        </AbsoluteFill>
    );
}
