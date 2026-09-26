import { buildSurface } from './terrain.js';

// Builds the lit surface off the main thread so replay controls stay responsive.
self.onmessage = (event) => {
  const { id, input } = event.data;
  const surface = buildSurface(input);
  self.postMessage({ id, width: surface.width, rgba: surface.rgba }, [surface.rgba.buffer]);
};
