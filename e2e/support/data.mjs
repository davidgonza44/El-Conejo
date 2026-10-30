// Sufijo corto y único por ejecución: los datos creados por un spec no chocan con otros.
const RUN = Date.now().toString(36).slice(-5).toUpperCase();
let counter = 0;

export const uid = () => `${RUN}${(counter++).toString(36).toUpperCase()}`;
