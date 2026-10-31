import { Composition, registerRoot } from 'remotion';
import { VIDEOS } from './guion.js';
import { ALTO, ANCHO, FPS, duracion } from './tema.js';
import { Tutorial } from './Tutorial.jsx';

function Raiz() {
    return VIDEOS.map((video) => (
        <Composition key={video.id} id={video.id} component={Tutorial} defaultProps={{ id: video.id }}
            durationInFrames={duracion(video)} fps={FPS} width={ANCHO} height={ALTO} />
    ));
}

registerRoot(Raiz);
