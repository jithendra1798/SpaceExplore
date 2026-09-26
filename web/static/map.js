import { buildSurface } from './terrain.js';

/**
 * Mission map: an orbital-style view of the surface with the rover driving on it.
 *
 * The ground is a lit relief image generated from the snapshot (terrain.js).
 * Ground the rover has not seen stays dim and blurred, like low-resolution
 * orbital context. Everything the rover lives through is drawn in place:
 * wheel tracks, drill holes, probe readings, event callouts at the spot they
 * happened, and a dust storm that darkens the light and blows across the view.
 *
 * `drawPlanet()` keeps the CONTRACTS §6 signature. Calling it again for the
 * same canvas updates the scene; the rover and camera animate between calls.
 */

// Display scale only: the simulator grid is unitless. At 10 m a tile, boulders
// come out at 0.5-2 m and rim craters at 40-60 m across, which reads as Mars.
export const TILE_METERS = 10;
const MIN_VIEW_TILES = 24;
const SCAN_RADIUS = 5;
const MASK_RES = 6;
const SUN_SHADOW = { x: 0.7071, y: 0.7071 }; // sun in the north-west casts south-east
const WIND = { x: 0.92, y: 0.38 };
const DIRECTIONS = {
  N: [0, -1], NE: [1, -1], E: [1, 0], SE: [1, 1], S: [0, 1], SW: [-1, 1], W: [-1, 0], NW: [-1, -1],
};
const SEVERITY_COLORS = { info: '#63e0c3', minor: '#f4c66c', major: '#ff9a55', critical: '#ff5d6c' };
const EVENT_TEXT = {
  STUCK: 'Bogged down in soft sand',
  FREED: 'Wheels free again',
  FALL: 'Slid over a crater rim',
  WHEEL_DAMAGE: 'Wheel damage',
  BATTERY_LOW: 'Battery low',
  BATTERY_CRITICAL: 'Battery critical',
  STORM_ONSET: 'Dust storm arrives',
  STORM_END: 'Dust storm clears',
  DISCOVERY: 'Deposit drilled',
  DEATH: 'Rover lost',
  GUARDRAIL_BLOCK: 'Safety rule blocked a move',
  NAV_DRIFT: 'Lost its position in the dust',
  GROUND_UPLINK: 'Earth uplinked a new route',
};
const PREVIEW_COLORS = {
  mars: { bedrock: '#b9926c', regolith: '#a2653f', sand: '#5c463b', rocks: '#8a5b40', crater_edge: '#94603f', ice: '#cdd3d3', unknown: '#8c5536' },
  icy: { bedrock: '#b0b4ba', regolith: '#939aa4', sand: '#6f7684', rocks: '#7c828c', crater_edge: '#8a909a', ice: '#c4deec', unknown: '#8a929c' },
};

const views = new WeakMap();
const surfaces = new Map();

/**
 * Draw terrain, fog of war, science, rover path, events and the rover.
 *
 * ``knownTiles`` accepts simulator records ({x, y, terrain}) or Atlas
 * map_knowledge documents ({loc: [x, y], terrain, science_hint, slip_probed}).
 * snapshot.terrain is row-major (terrain[y][x]).
 *
 * Optional extras beyond the contract: ``sol``, ``actions`` (this sol's plan
 * outcomes), ``rover`` ({battery, stuck}), ``waypoints`` ([{pos, sol}]),
 * ``weatherTau`` and ``minimap`` (a second canvas for the overview inset).
 */
export function drawPlanet(canvas, snapshot, options = {}) {
  if (!canvas || !snapshot?.terrain?.length) return;
  let view = views.get(canvas);
  if (!view) {
    view = createView(canvas);
    views.set(canvas, view);
  }
  setScene(view, snapshot, options);
  render(view, performance.now());
}

// ---------------------------------------------------------------------------
// Surface generation and caching
// ---------------------------------------------------------------------------

let worker = null;
let workerBroken = false;
let jobCounter = 0;
const jobs = new Map();

function runJob(input, done) {
  if (!worker && !workerBroken && typeof Worker !== 'undefined') {
    try {
      worker = new Worker(new URL('./terrain-worker.js', import.meta.url), { type: 'module' });
      worker.onmessage = (event) => {
        const job = jobs.get(event.data.id);
        jobs.delete(event.data.id);
        job?.done(event.data.rgba, event.data.width);
      };
      worker.onerror = () => {
        // Module workers are unavailable in a few older browsers: build inline.
        workerBroken = true;
        worker = null;
        const pending = [...jobs.values()];
        jobs.clear();
        pending.forEach((job) => buildInline(job.input, job.done));
      };
    } catch {
      workerBroken = true;
      worker = null;
    }
  }
  if (worker) {
    jobCounter += 1;
    jobs.set(jobCounter, { input, done });
    worker.postMessage({ id: jobCounter, input });
  } else {
    buildInline(input, done);
  }
}

function buildInline(input, done) {
  setTimeout(() => {
    const surface = buildSurface(input);
    done(surface.rgba, surface.width);
  }, 16);
}

function surfaceFor(snapshot) {
  const size = snapshot.size || snapshot.terrain.length;
  const planet = snapshot.planet === 'icy' ? 'icy' : 'mars';
  const terrain = snapshot.terrain;
  let signature = 0;
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const code = String(terrain[y]?.[x] || '').charCodeAt(0) || 0;
      signature = (Math.imul(signature, 31) + code) | 0;
    }
  }
  const key = `${snapshot.seed}|${planet}|${size}|${signature}`;
  let entry = surfaces.get(key);
  if (entry) return entry;

  entry = { key, size, planet, ready: false, preview: previewCanvas(terrain, size, planet), image: null, fog: null, small: null };
  surfaces.set(key, entry);
  const input = {
    terrain: terrain.map((row) => row.map((value) => (value === 'unknown' ? 'regolith' : value))),
    size, seed: Number(snapshot.seed) || 1, planet, craters: snapshot.craters || null,
  };
  runJob(input, (rgba, width) => {
    const image = makeCanvas(width, width);
    image.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(rgba.buffer || rgba), width, width), 0, 0);
    entry.image = image;
    entry.fog = fogFrom(image, size);
    entry.small = scaled(image, Math.min(width, 480));
    entry.ready = true;
  });
  return entry;
}

function makeCanvas(width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

function scaled(source, width) {
  const canvas = makeCanvas(width, width);
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(source, 0, 0, width, width);
  return canvas;
}

/** Blurry flat-colour stand-in shown for the second the relief takes to build. */
function previewCanvas(terrain, size, planet) {
  const canvas = makeCanvas(size, size);
  const ctx = canvas.getContext('2d');
  const colors = PREVIEW_COLORS[planet];
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      ctx.fillStyle = colors[terrain[y]?.[x]] || colors.unknown;
      ctx.fillRect(x, y, 1, 1);
    }
  }
  return canvas;
}

/** Unseen ground: low resolution, desaturated and dark, like old orbital context. */
function fogFrom(image, size) {
  const width = size * 4;
  const canvas = scaled(image, width);
  const ctx = canvas.getContext('2d');
  const data = ctx.getImageData(0, 0, width, width);
  const px = data.data;
  for (let i = 0; i < px.length; i += 4) {
    const lum = px[i] * 0.3 + px[i + 1] * 0.55 + px[i + 2] * 0.15;
    px[i] = (px[i] * 0.35 + lum * 0.65) * 0.25;
    px[i + 1] = (px[i + 1] * 0.35 + lum * 0.65) * 0.235;
    px[i + 2] = (px[i + 2] * 0.35 + lum * 0.65) * 0.24;
  }
  ctx.putImageData(data, 0, 0);
  return canvas;
}

// ---------------------------------------------------------------------------
// Scene state
// ---------------------------------------------------------------------------

function createView(canvas) {
  const view = {
    canvas,
    ctx: canvas.getContext('2d'),
    scene: null,
    cam: null,
    user: { zoom: 1, panX: 0, panY: 0 },
    rover: null,
    effects: [],
    dust: [],
    mask: null,
    maskKey: '',
    reveal: null,
    haze: null,
    lastFrame: 0,
  };
  bindInteraction(view);
  const loop = (now) => {
    if (!canvas.isConnected) {
      view.raf = null;
      return;
    }
    if (!document.hidden && now - view.lastFrame > 30) render(view, now);
    view.raf = requestAnimationFrame(loop);
  };
  view.raf = requestAnimationFrame(loop);
  return view;
}

function bindInteraction(view) {
  const { canvas } = view;
  canvas.addEventListener('wheel', (event) => {
    event.preventDefault();
    view.user.zoom = Math.max(0.45, Math.min(3.2, view.user.zoom * Math.exp(event.deltaY * 0.0012)));
  }, { passive: false });
  let drag = null;
  canvas.addEventListener('pointerdown', (event) => {
    drag = { x: event.clientX, y: event.clientY, panX: view.user.panX, panY: view.user.panY };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', (event) => {
    if (!drag || !view.cam) return;
    const tilePx = canvas.clientWidth / view.cam.viewW;
    view.user.panX = drag.panX - (event.clientX - drag.x) / tilePx;
    view.user.panY = drag.panY - (event.clientY - drag.y) / tilePx;
  });
  const end = () => { drag = null; };
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);
  canvas.addEventListener('dblclick', () => {
    view.user = { zoom: 1, panX: 0, panY: 0 };
  });
}

function pointOf(value) {
  if (!value) return null;
  if (Array.isArray(value) && value.length >= 2) {
    const x = Number(value[0]);
    const y = Number(value[1]);
    return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
  }
  if (typeof value === 'object') {
    const raw = value.loc ?? value.pos;
    if (raw && raw !== value) return pointOf(raw);
    const x = Number(value.x);
    const y = Number(value.y);
    return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
  }
  return null;
}

function setScene(view, snapshot, options) {
  const {
    path = [], roverPos = null, knownTiles = [], events = [], showTruth = false,
    weatherTau = 0.5, sol = null, actions = [], rover = {}, waypoints = [], minimap = null,
  } = options;
  const size = snapshot.size || snapshot.terrain.length;
  const known = new Map();
  for (const tile of knownTiles || []) {
    const point = pointOf(tile.loc ?? tile.pos ?? tile);
    if (point) known.set(`${point.x},${point.y}`, { ...tile, x: point.x, y: point.y });
  }
  const pathPoints = (path || []).map(pointOf).filter(Boolean);
  for (const point of pathPoints) {
    const key = `${point.x},${point.y}`;
    if (!known.has(key)) known.set(key, { x: point.x, y: point.y });
  }
  const roverPoint = pointOf(roverPos) || pathPoints.at(-1) || pointOf(snapshot.start);
  if (roverPoint && !known.has(`${roverPoint.x},${roverPoint.y}`)) known.set(`${roverPoint.x},${roverPoint.y}`, { ...roverPoint });

  const previous = view.scene;
  const scene = {
    snapshot, size, known, path: pathPoints, rover: roverPoint, events: events || [],
    showTruth, tau: Number(weatherTau) || 0.5, sol: sol == null ? null : Number(sol),
    actions: (actions || []).map(normalizeAction), telemetry: rover || {},
    waypoints: (waypoints || []).map((item) => ({ ...pointOf(item.pos ?? item), sol: item.sol })).filter((item) => Number.isFinite(item.x)),
    minimap, surface: surfaceFor(snapshot),
  };
  view.scene = scene;

  const maskKey = `${size}|${[...known.keys()].sort().join(';')}`;
  if (maskKey !== view.maskKey) {
    view.mask = buildMask(known, size);
    view.maskKey = maskKey;
  }

  const now = performance.now();
  const heading = headingFor(scene, previous) ?? view.rover?.heading ?? 0;
  if (roverPoint) {
    const target = { x: roverPoint.x + 0.5, y: roverPoint.y + 0.5 };
    if (!view.rover || previous?.snapshot !== snapshot) {
      view.rover = { x: target.x, y: target.y, from: target, to: target, t0: now, dur: 1, heading, headingFrom: heading };
    } else {
      const current = roverAt(view.rover, now);
      const distance = Math.hypot(target.x - current.x, target.y - current.y);
      const jump = previous && scene.sol != null && previous.sol != null && Math.abs(scene.sol - previous.sol) > 1;
      view.rover = {
        from: current, to: target, t0: now,
        dur: jump || distance > 6 ? 1 : Math.min(1400, 380 + distance * 170),
        headingFrom: current.heading ?? heading, heading,
      };
    }
  }

  // One-shot effects for the sol that just started playing.
  if (previous && scene.sol != null && scene.sol !== previous.sol && roverPoint) {
    const at = { x: roverPoint.x + 0.5, y: roverPoint.y + 0.5 };
    for (const action of scene.actions) {
      if (action.tool === 'scan' && action.ok !== false) view.effects.push({ kind: 'scan', at, t0: now + 250, dur: 1700 });
      if (action.tool === 'drill' && action.ok !== false) view.effects.push({ kind: 'drill', at, t0: now + 200, dur: 1500 });
      if (action.tool === 'probe_terrain') {
        const [dx, dy] = DIRECTIONS[String(action.args.direction || 'E').toUpperCase()] || [1, 0];
        view.effects.push({ kind: 'probe', at, to: { x: at.x + dx, y: at.y + dy }, t0: now + 150, dur: 1200 });
      }
    }
  }
  if (previous && scene.sol != null && scene.sol !== previous.sol && roverPoint
    && scene.events.some((event) => event.type === 'GROUND_UPLINK' && Number(event.sol) === scene.sol)) {
    view.effects.push({ kind: 'uplink', at: { x: roverPoint.x + 0.5, y: roverPoint.y + 0.5 }, t0: now + 200, dur: 2600 });
  }
  if (!view.cam) view.cam = cameraTarget(view, scene);
}

/** Where the storm drift and the Earth uplink stand at the current sol. */
function groundOps(scene) {
  const drift = [...scene.events].reverse().find((event) => event.type === 'NAV_DRIFT');
  const uplink = [...scene.events].reverse().find((event) => event.type === 'GROUND_UPLINK');
  const holding = Boolean(drift && (!uplink || Number(uplink.sol) < Number(drift.sol)));
  let route = null;
  if (uplink && !holding) {
    const destination = pointOf(uplink.details?.destination);
    const arrived = destination && scene.rover && scene.rover.x === destination.x && scene.rover.y === destination.y;
    if (destination && (!arrived || Number(uplink.sol) === scene.sol)) route = { uplink, destination };
  }
  return { drift, holding, route };
}

/** Planned-vs-actual after a drift, position uncertainty while holding, and the uplinked route. */
function drawGroundOps(ctx, scene, rover, t) {
  const { drift, holding, route } = groundOps(scene);
  const d = t.dpr;
  const recentDrift = drift && (holding || Number(drift.sol) === scene.sol);
  if (recentDrift) {
    const start = pointOf(drift.details?.start);
    const planned = pointOf(drift.details?.planned);
    if (start && planned) {
      ctx.save();
      ctx.setLineDash([t.tile * 0.14, t.tile * 0.1]);
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.6)';
      ctx.lineWidth = Math.max(1.5, t.tile * 0.035);
      ctx.beginPath();
      ctx.moveTo(t.sx(start.x + 0.5), t.sy(start.y + 0.5));
      ctx.lineTo(t.sx(planned.x + 0.5), t.sy(planned.y + 0.5));
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(t.sx(planned.x + 0.5), t.sy(planned.y + 0.5), t.tile * 0.28, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
      label(ctx, t, t.sx(planned.x + 0.5), t.sy(planned.y + 0.5) - t.tile * 0.5, 'planned stop', 'rgba(255, 255, 255, 0.85)', 0.72);
    }
  }
  if (holding && rover) {
    const pulse = 0.5 + 0.5 * Math.sin(t.now / 420);
    const cx = t.sx(rover.x);
    const cy = t.sy(rover.y);
    const radius = t.tile * (1.35 + pulse * 0.12);
    ctx.save();
    const glow = ctx.createRadialGradient(cx, cy, radius * 0.3, cx, cy, radius);
    glow.addColorStop(0, 'rgba(244, 198, 108, 0)');
    glow.addColorStop(1, `rgba(244, 198, 108, ${0.1 + pulse * 0.08})`);
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.setLineDash([t.tile * 0.12, t.tile * 0.09]);
    ctx.strokeStyle = `rgba(244, 198, 108, ${0.6 + pulse * 0.3})`;
    ctx.lineWidth = Math.max(1.5, t.tile * 0.035);
    ctx.stroke();
    ctx.restore();
    label(ctx, t, cx, cy + radius + 10 * d, `position unknown ±${Math.round(TILE_METERS * 1.5)} m · waiting for Earth`, '#f4d58f', 0.74);
  }
  if (route) {
    const points = [pointOf(route.uplink.details?.fix), ...(route.uplink.details?.route || []).map(pointOf)].filter(Boolean);
    ctx.save();
    ctx.strokeStyle = 'rgba(128, 220, 255, 0.95)';
    ctx.lineWidth = Math.max(2, t.tile * 0.05);
    ctx.setLineDash([t.tile * 0.16, t.tile * 0.1]);
    ctx.lineDashOffset = -t.now / 30;
    ctx.shadowColor = 'rgba(0, 0, 0, 0.6)';
    ctx.shadowBlur = 4 * d;
    ctx.beginPath();
    points.forEach((point, index) => {
      const x = t.sx(point.x + 0.5);
      const y = t.sy(point.y + 0.5);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.restore();
    // Destination flag.
    const fx = t.sx(route.destination.x + 0.5);
    const fy = t.sy(route.destination.y + 0.5);
    ctx.save();
    ctx.strokeStyle = '#f4efe8';
    ctx.lineWidth = Math.max(1.5, 1.6 * d);
    ctx.beginPath();
    ctx.moveTo(fx, fy);
    ctx.lineTo(fx, fy - t.tile * 0.6);
    ctx.stroke();
    ctx.fillStyle = '#80dcff';
    ctx.beginPath();
    ctx.moveTo(fx, fy - t.tile * 0.6);
    ctx.lineTo(fx + t.tile * 0.32, fy - t.tile * 0.5);
    ctx.lineTo(fx, fy - t.tile * 0.4);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }
}

function normalizeAction(raw) {
  const action = raw?.action || raw || {};
  return {
    tool: action.tool || action.name || (typeof action === 'string' ? action : 'wait'),
    args: action.args || raw?.args || {},
    ok: raw?.ok,
  };
}

function headingFor(scene, previous) {
  const move = [...scene.actions].reverse().find((action) => action.tool === 'move' && action.args?.direction);
  if (move) {
    const [dx, dy] = DIRECTIONS[String(move.args.direction).toUpperCase()] || [1, 0];
    return Math.atan2(dy, dx);
  }
  const points = scene.path;
  for (let i = points.length - 1; i > 0; i -= 1) {
    const dx = points[i].x - points[i - 1].x;
    const dy = points[i].y - points[i - 1].y;
    if (dx || dy) return Math.atan2(dy, dx);
  }
  return null;
}

function roverAt(rover, now) {
  const t = Math.max(0, Math.min(1, (now - rover.t0) / rover.dur));
  const e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
  let delta = rover.heading - rover.headingFrom;
  while (delta > Math.PI) delta -= Math.PI * 2;
  while (delta < -Math.PI) delta += Math.PI * 2;
  const turn = Math.min(1, t * 3);
  return {
    x: rover.from.x + (rover.to.x - rover.from.x) * e,
    y: rover.from.y + (rover.to.y - rover.from.y) * e,
    heading: rover.headingFrom + delta * turn,
    moving: t < 1 && (rover.to.x !== rover.from.x || rover.to.y !== rover.from.y),
  };
}

/** Soft-edged mask of seen ground, MASK_RES pixels per tile. */
function buildMask(known, size) {
  const width = size * MASK_RES;
  const alpha = new Float32Array(width * width);
  for (const tile of known.values()) {
    if (tile.x < 0 || tile.y < 0 || tile.x >= size || tile.y >= size) continue;
    for (let y = 0; y < MASK_RES; y += 1) {
      for (let x = 0; x < MASK_RES; x += 1) alpha[(tile.y * MASK_RES + y) * width + tile.x * MASK_RES + x] = 1;
    }
  }
  const blurred = boxBlur(boxBlur(alpha, width, 3), width, 3);
  const canvas = makeCanvas(width, width);
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(width, width);
  // Ragged, not rectangular: what the cameras saw ends where terrain blocks them.
  const noise = (x, y) => {
    const h = (Math.imul(x, 374761393) + Math.imul(y, 668265263)) | 0;
    const m = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((m ^ (m >>> 16)) >>> 0) / 4294967296;
  };
  const cell = MASK_RES / 2;
  for (let i = 0; i < blurred.length; i += 1) {
    const x = (i % width) / cell;
    const y = Math.floor(i / width) / cell;
    const ix = Math.floor(x);
    const iy = Math.floor(y);
    const fx = x - ix;
    const fy = y - iy;
    const a = noise(ix, iy);
    const b = noise(ix + 1, iy);
    const c = noise(ix, iy + 1);
    const d = noise(ix + 1, iy + 1);
    const n = a + (b - a) * fx + (c - a) * fy + (a - b - c + d) * fx * fy;
    const edge = blurred[i] + (n - 0.5) * 0.3 * Math.min(1, 4 * blurred[i] * (1 - blurred[i]) * 1.6);
    const t = Math.max(0, Math.min(1, (edge - 0.22) / 0.56));
    image.data[i * 4 + 3] = 255 * t * t * (3 - 2 * t);
  }
  ctx.putImageData(image, 0, 0);
  return canvas;
}

function boxBlur(source, width, radius) {
  const tmp = new Float32Array(source.length);
  const out = new Float32Array(source.length);
  const span = radius * 2 + 1;
  for (let y = 0; y < width; y += 1) {
    for (let x = 0; x < width; x += 1) {
      let sum = 0;
      for (let k = -radius; k <= radius; k += 1) sum += source[y * width + Math.max(0, Math.min(width - 1, x + k))];
      tmp[y * width + x] = sum / span;
    }
  }
  for (let y = 0; y < width; y += 1) {
    for (let x = 0; x < width; x += 1) {
      let sum = 0;
      for (let k = -radius; k <= radius; k += 1) sum += tmp[Math.max(0, Math.min(width - 1, y + k)) * width + x];
      out[y * width + x] = sum / span;
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Camera
// ---------------------------------------------------------------------------

function cameraTarget(view, scene) {
  const aspect = (view.canvas.clientHeight || view.canvas.height) / (view.canvas.clientWidth || view.canvas.width) || 1;
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  const include = (x, y) => {
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x + 1);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y + 1);
  };
  for (const point of scene.path) include(point.x, point.y);
  if (scene.rover) include(scene.rover.x, scene.rover.y);
  if (!Number.isFinite(minX)) include(scene.size / 2, scene.size / 2);
  const pad = 5;
  let viewW = Math.max(MIN_VIEW_TILES, maxX - minX + pad * 2, (maxY - minY + pad * 2) / aspect);
  viewW = Math.min(viewW, scene.size * 1.15) * view.user.zoom;
  const rover = scene.rover ? { x: scene.rover.x + 0.5, y: scene.rover.y + 0.5 } : { x: (minX + maxX) / 2, y: (minY + maxY) / 2 };
  let cx = (minX + maxX) / 2 * 0.45 + rover.x * 0.55;
  let cy = (minY + maxY) / 2 * 0.45 + rover.y * 0.55;
  cx += view.user.panX;
  cy += view.user.panY;
  // Keep the camera over the planet surface instead of the void past its edge.
  const viewH = viewW * aspect;
  const margin = 0;
  cx = viewW >= scene.size + margin * 2 ? scene.size / 2 : Math.max(viewW / 2 - margin, Math.min(scene.size + margin - viewW / 2, cx));
  cy = viewH >= scene.size + margin * 2 ? scene.size / 2 : Math.max(viewH / 2 - margin, Math.min(scene.size + margin - viewH / 2, cy));
  return { cx, cy, viewW };
}

function updateCamera(view, dt) {
  const target = cameraTarget(view, view.scene);
  if (!view.cam) {
    view.cam = target;
    return;
  }
  const k = 1 - Math.exp(-dt * 3.2);
  view.cam.cx += (target.cx - view.cam.cx) * k;
  view.cam.cy += (target.cy - view.cam.cy) * k;
  view.cam.viewW += (target.viewW - view.cam.viewW) * k;
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function render(view, now) {
  const { canvas, ctx, scene } = view;
  if (!scene || !ctx) return;
  const dt = Math.min(0.1, (now - (view.lastFrame || now)) / 1000);
  view.lastFrame = now;

  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const cssW = canvas.clientWidth || canvas.width;
  const cssH = canvas.clientHeight || canvas.height;
  const W = Math.max(1, Math.round(cssW * dpr));
  const H = Math.max(1, Math.round(cssH * dpr));
  if (canvas.width !== W || canvas.height !== H) {
    canvas.width = W;
    canvas.height = H;
  }

  updateCamera(view, dt);
  const cam = view.cam;
  const tile = W / cam.viewW;
  const x0 = cam.cx - cam.viewW / 2;
  const y0 = cam.cy - (H / tile) / 2;
  const t = {
    tile, dpr, W, H, now,
    sx: (x) => (x - x0) * tile,
    sy: (y) => (y - y0) * tile,
  };
  const rover = view.rover ? roverAt(view.rover, now) : null;
  const storm = Math.max(0, Math.min(1, (scene.tau - 0.7) / 3.2));

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalCompositeOperation = 'source-over';
  ctx.globalAlpha = 1;
  ctx.fillStyle = scene.surface.planet === 'icy' ? '#0f1216' : '#120c0a';
  ctx.fillRect(0, 0, W, H);

  drawGround(view, t);
  drawKnowledge(ctx, scene, t);
  drawTracks(ctx, scene, rover, t);
  drawGroundOps(ctx, scene, rover, t);
  drawWaypoints(ctx, scene, t);
  drawSiteMarks(ctx, scene, t);
  drawEffects(view, t, 'ground');
  if (rover) drawRover(ctx, rover, scene, t);
  drawEffects(view, t, 'air');
  drawAtmosphere(view, t, storm, dt);
  drawCallouts(ctx, scene, t);
  drawVignette(ctx, t);
  drawScaleBar(ctx, t);
  if (!scene.surface.ready) drawBadge(ctx, t, 'Rendering terrain relief…');
  if (scene.minimap) drawMinimap(view, rover);
}

function drawGround(view, t) {
  const { ctx, scene } = view;
  const surface = scene.surface;
  const size = scene.size;
  const x = t.sx(0);
  const y = t.sy(0);
  const span = size * t.tile;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';

  const lit = surface.ready ? surface.image : surface.preview;
  const fog = surface.ready ? surface.fog : surface.preview;
  if (scene.showTruth) {
    ctx.drawImage(lit, x, y, span, span);
    return;
  }
  ctx.save();
  if (!surface.ready) ctx.filter = 'brightness(0.3) saturate(0.4)';
  ctx.drawImage(fog, x, y, span, span);
  ctx.restore();

  if (!view.reveal || view.reveal.width !== t.W || view.reveal.height !== t.H) view.reveal = makeCanvas(t.W, t.H);
  const rctx = view.reveal.getContext('2d');
  rctx.globalCompositeOperation = 'source-over';
  rctx.clearRect(0, 0, t.W, t.H);
  rctx.imageSmoothingEnabled = true;
  rctx.imageSmoothingQuality = 'high';
  rctx.drawImage(lit, x, y, span, span);
  rctx.globalCompositeOperation = 'destination-in';
  rctx.drawImage(view.mask, x, y, span, span);
  rctx.globalCompositeOperation = 'source-over';
  ctx.drawImage(view.reveal, 0, 0);
}

/** Probe readings, scan signals and (with ground truth on) hidden slip and deposits. */
function drawKnowledge(ctx, scene, t) {
  const { tile } = t;
  const snapshot = scene.snapshot;
  const drilled = new Set(scene.events.filter((event) => event.type === 'DISCOVERY').map((event) => {
    const point = pointOf(event.pos ?? event.loc);
    return point ? `${point.x},${point.y}` : '';
  }));

  if (scene.showTruth) {
    for (const [key, slip] of Object.entries(snapshot.slip || {})) {
      const [x, y] = key.split(',').map(Number);
      const value = Number(slip);
      if (!Number.isFinite(value)) continue;
      const cx = t.sx(x + 0.5);
      const cy = t.sy(y + 0.5);
      const radius = tile * 0.75;
      const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
      gradient.addColorStop(0, `rgba(255, 70, 60, ${0.18 + value * 0.42})`);
      gradient.addColorStop(1, 'rgba(255, 70, 60, 0)');
      ctx.fillStyle = gradient;
      ctx.fillRect(cx - radius, cy - radius, radius * 2, radius * 2);
      if (value >= 0.5 && tile > 34) label(ctx, t, cx, cy - tile * 0.28, `slip ${value.toFixed(2)}`, '#ffb3aa', 0.78);
    }
    for (const deposit of snapshot.science || []) {
      const point = pointOf(deposit.pos ?? deposit);
      if (!point || drilled.has(`${point.x},${point.y}`)) continue;
      reticle(ctx, t, point.x + 0.5, point.y + 0.5, '#ffe28a', `${Math.round(Number(deposit.value) || 0)} pts`, false);
    }
  }

  for (const tileRecord of scene.known.values()) {
    const slip = tileRecord.slip_probed ?? tileRecord.slip;
    if (slip != null && Number.isFinite(Number(slip))) {
      const value = Number(slip);
      const color = value > 0.5 ? '#ff6b5e' : value > 0.3 ? '#f4c66c' : '#7fe0a8';
      const px = t.sx(tileRecord.x);
      const py = t.sy(tileRecord.y);
      ctx.save();
      ctx.setLineDash([tile * 0.08, tile * 0.06]);
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(1.2, tile * 0.03);
      roundRect(ctx, px + tile * 0.08, py + tile * 0.08, tile * 0.84, tile * 0.84, tile * 0.12);
      ctx.stroke();
      ctx.restore();
      label(ctx, t, px + tile / 2, py + tile * 0.22, `probe · slip ${value.toFixed(2)}`, color, 0.72);
    }
    if (tileRecord.science_hint && !drilled.has(`${tileRecord.x},${tileRecord.y}`)) {
      reticle(ctx, t, tileRecord.x + 0.5, tileRecord.y + 0.5, '#9fe3ff', 'signal', true);
    }
  }
}

function reticle(ctx, t, x, y, color, text, dashed) {
  const cx = t.sx(x);
  const cy = t.sy(y);
  const radius = Math.max(6, t.tile * 0.36);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = Math.max(1.2, t.tile * 0.028);
  ctx.shadowColor = 'rgba(0, 0, 0, 0.6)';
  ctx.shadowBlur = 4 * t.dpr;
  if (dashed) ctx.setLineDash([radius * 0.35, radius * 0.25]);
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);
  for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    ctx.beginPath();
    ctx.moveTo(cx + dx * radius * 0.55, cy + dy * radius * 0.55);
    ctx.lineTo(cx + dx * radius * 1.3, cy + dy * radius * 1.3);
    ctx.stroke();
  }
  ctx.restore();
  if (text && t.tile > 22) label(ctx, t, cx, cy - radius - 9 * t.dpr, text, color, 0.72);
}

/** Paired wheel ruts following the drive, with tread marks when close. */
function drawTracks(ctx, scene, rover, t) {
  const points = scene.path.map((point) => ({ x: point.x + 0.5, y: point.y + 0.5 }));
  if (rover && points.length) {
    // While the rover is mid-drive, the ruts end under its wheels.
    const last = points.at(-1);
    if (Math.hypot(last.x - rover.x, last.y - rover.y) > 0.01) points[points.length - 1] = { x: rover.x, y: rover.y };
  }
  if (points.length < 2) return;
  const gauge = 0.17;
  for (const side of [-1, 1]) {
    ctx.save();
    ctx.beginPath();
    points.forEach((point, index) => {
      const prev = points[Math.max(0, index - 1)];
      const next = points[Math.min(points.length - 1, index + 1)];
      let dx = next.x - prev.x;
      let dy = next.y - prev.y;
      const length = Math.hypot(dx, dy) || 1;
      dx /= length;
      dy /= length;
      const x = t.sx(point.x - dy * gauge * side);
      const y = t.sy(point.y + dx * gauge * side);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = 'rgba(38, 20, 12, 0.26)';
    ctx.lineWidth = Math.max(1.2, t.tile * 0.065);
    ctx.stroke();
    if (t.tile > 40) {
      ctx.setLineDash([t.tile * 0.035, t.tile * 0.045]);
      ctx.strokeStyle = 'rgba(255, 220, 190, 0.1)';
      ctx.lineWidth = Math.max(1, t.tile * 0.06);
      ctx.stroke();
    }
    ctx.restore();
  }
}

/** End-of-drive markers labelled with the sol, as on real traverse maps. */
function drawWaypoints(ctx, scene, t) {
  const current = scene.rover;
  let lastLabel = null;
  for (const waypoint of scene.waypoints) {
    if (current && waypoint.x === current.x && waypoint.y === current.y) continue;
    if (scene.sol != null && waypoint.sol > scene.sol) continue;
    const x = t.sx(waypoint.x + 0.5);
    const y = t.sy(waypoint.y + 0.5);
    ctx.save();
    ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
    ctx.strokeStyle = 'rgba(20, 12, 8, 0.8)';
    ctx.lineWidth = 1.2 * t.dpr;
    ctx.beginPath();
    ctx.arc(x, y, Math.max(2.2 * t.dpr, t.tile * 0.05), 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
    if (waypoint.sol != null && t.tile > 24 && (!lastLabel || Math.hypot(lastLabel.x - x, lastLabel.y - y) > t.tile * 1.6)) {
      ctx.save();
      ctx.font = `600 ${10.5 * t.dpr}px "DM Mono", ui-monospace, monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.lineWidth = 3 * t.dpr;
      ctx.strokeStyle = 'rgba(15, 9, 6, 0.8)';
      ctx.strokeText(String(waypoint.sol), x, y + t.tile * 0.12);
      ctx.fillStyle = 'rgba(255, 255, 255, 0.92)';
      ctx.fillText(String(waypoint.sol), x, y + t.tile * 0.12);
      ctx.restore();
      lastLabel = { x, y };
    }
  }
}

/** Physical traces left behind: drill holes with tailings, churned sand where it bogged down. */
function drawSiteMarks(ctx, scene, t) {
  for (const event of scene.events) {
    const point = pointOf(event.pos ?? event.loc);
    if (!point) continue;
    const cx = t.sx(point.x + 0.5);
    const cy = t.sy(point.y + 0.5);
    if (event.type === 'DISCOVERY') {
      const offset = t.tile * 0.22;
      ctx.save();
      ctx.fillStyle = 'rgba(214, 190, 170, 0.55)';
      ctx.beginPath();
      ctx.ellipse(cx + offset, cy + offset * 0.4, t.tile * 0.09, t.tile * 0.06, 0.4, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#1b100b';
      ctx.beginPath();
      ctx.arc(cx + offset, cy + offset * 0.4, Math.max(1.5, t.tile * 0.028), 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    if (event.type === 'STUCK') {
      ctx.save();
      for (let i = 0; i < 14; i += 1) {
        const angle = i * 2.39996;
        const r = t.tile * (0.18 + (i % 5) * 0.06);
        ctx.fillStyle = i % 2 ? 'rgba(40, 26, 20, 0.35)' : 'rgba(150, 118, 96, 0.25)';
        ctx.beginPath();
        ctx.ellipse(cx + Math.cos(angle) * r, cy + Math.sin(angle) * r, t.tile * 0.07, t.tile * 0.04, angle, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }
  }
}

function drawEffects(view, t, layer) {
  const { ctx } = view;
  view.effects = view.effects.filter((effect) => t.now < effect.t0 + effect.dur);
  for (const effect of view.effects) {
    const p = (t.now - effect.t0) / effect.dur;
    if (p < 0) continue;
    const cx = t.sx(effect.at.x);
    const cy = t.sy(effect.at.y);
    if (layer === 'ground' && effect.kind === 'scan') {
      const radius = SCAN_RADIUS * t.tile * (0.15 + 0.85 * Math.sqrt(p));
      ctx.save();
      ctx.strokeStyle = `rgba(140, 230, 255, ${0.65 * (1 - p)})`;
      ctx.lineWidth = Math.max(1.5, t.tile * 0.05);
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.stroke();
      const gradient = ctx.createRadialGradient(cx, cy, radius * 0.7, cx, cy, radius);
      gradient.addColorStop(0, 'rgba(140, 230, 255, 0)');
      gradient.addColorStop(1, `rgba(140, 230, 255, ${0.14 * (1 - p)})`);
      ctx.fillStyle = gradient;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    if (layer === 'ground' && effect.kind === 'probe') {
      const tx = t.sx(effect.to.x);
      const ty = t.sy(effect.to.y);
      ctx.save();
      ctx.strokeStyle = `rgba(255, 214, 120, ${0.9 * (1 - p)})`;
      ctx.lineWidth = Math.max(1.5, t.tile * 0.04);
      ctx.setLineDash([t.tile * 0.08, t.tile * 0.06]);
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(tx, ty);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(tx, ty, t.tile * (0.2 + p * 0.25), 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
    if (layer === 'air' && effect.kind === 'uplink') {
      // Signal arriving from Earth (via orbiter relay), straight down onto the rover.
      ctx.save();
      const fade = p < 0.8 ? 1 : (1 - p) / 0.2;
      const beam = ctx.createLinearGradient(cx, 0, cx, cy);
      beam.addColorStop(0, 'rgba(128, 220, 255, 0)');
      beam.addColorStop(1, `rgba(128, 220, 255, ${0.5 * fade})`);
      ctx.strokeStyle = beam;
      ctx.lineWidth = Math.max(2, t.tile * 0.06);
      ctx.beginPath();
      ctx.moveTo(cx, 0);
      ctx.lineTo(cx, cy);
      ctx.stroke();
      for (let k = 0; k < 3; k += 1) {
        const q = (p * 2.2 + k / 3) % 1;
        ctx.strokeStyle = `rgba(128, 220, 255, ${(1 - q) * 0.8 * fade})`;
        ctx.lineWidth = Math.max(1.5, t.tile * 0.04);
        ctx.beginPath();
        ctx.arc(cx, cy, t.tile * (0.4 + q * 1.4), 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();
    }
    if (layer === 'air' && effect.kind === 'drill') {
      ctx.save();
      for (let i = 0; i < 10; i += 1) {
        const angle = i * 0.63 + p;
        const r = t.tile * (0.15 + p * 0.55) * (0.6 + (i % 3) * 0.2);
        ctx.fillStyle = `rgba(205, 160, 120, ${0.35 * (1 - p)})`;
        ctx.beginPath();
        ctx.arc(cx + Math.cos(angle) * r + t.tile * 0.2, cy + Math.sin(angle) * r, t.tile * (0.06 + p * 0.1), 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }
  }
}

/** Top-down six-wheel rover: rocker-bogie wheels, deck, mast, RTG and a real shadow. */
function drawRover(ctx, rover, scene, t) {
  const cx = t.sx(rover.x);
  const cy = t.sy(rover.y);
  const s = Math.max(10 * t.dpr, t.tile * 0.62);
  const stuck = Boolean(scene.telemetry.stuck);
  const battery = Number(scene.telemetry.battery);
  const wobble = stuck ? Math.sin(t.now / 90) * 0.02 : 0;

  // Status halo, so the rover is findable at any zoom.
  const status = stuck ? '#ff6b5e' : Number.isFinite(battery) && battery < 25 ? '#f4c66c' : '#63e0c3';
  const pulse = 0.5 + 0.5 * Math.sin(t.now / (stuck ? 180 : 600));
  ctx.save();
  ctx.strokeStyle = status;
  ctx.globalAlpha = stuck ? 0.45 + pulse * 0.4 : 0.35 + pulse * 0.15;
  ctx.lineWidth = Math.max(1.5, s * 0.045);
  ctx.beginPath();
  ctx.arc(cx, cy, s * (0.95 + pulse * 0.08), 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();

  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(rover.heading + wobble);

  // Shadow cast away from the north-west sun.
  ctx.save();
  ctx.rotate(-(rover.heading + wobble));
  ctx.translate(SUN_SHADOW.x * s * 0.16, SUN_SHADOW.y * s * 0.16);
  ctx.rotate(rover.heading + wobble);
  ctx.fillStyle = 'rgba(15, 6, 2, 0.42)';
  roundRect(ctx, -s * 0.5, -s * 0.42, s * 1.0, s * 0.84, s * 0.14);
  ctx.fill();
  ctx.restore();

  if (stuck) {
    ctx.fillStyle = 'rgba(70, 50, 40, 0.55)';
    for (const wx of [-0.32, 0, 0.32]) {
      for (const wy of [-0.36, 0.36]) {
        ctx.beginPath();
        ctx.ellipse(wx * s, wy * s, s * 0.14, s * 0.1, 0, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  // Suspension arms.
  ctx.strokeStyle = '#5b5f66';
  ctx.lineWidth = Math.max(1, s * 0.035);
  for (const side of [-1, 1]) {
    ctx.beginPath();
    ctx.moveTo(-s * 0.34, side * s * 0.33);
    ctx.lineTo(s * 0.34, side * s * 0.33);
    ctx.stroke();
  }
  // Wheels.
  ctx.fillStyle = '#26282c';
  for (const wx of [-0.34, 0, 0.34]) {
    for (const side of [-1, 1]) {
      roundRect(ctx, wx * s - s * 0.095, side * s * 0.36 - s * 0.065, s * 0.19, s * 0.13, s * 0.03);
      ctx.fill();
      if (s > 26) {
        ctx.strokeStyle = 'rgba(160, 160, 160, 0.35)';
        ctx.lineWidth = 1;
        for (let k = -2; k <= 2; k += 1) {
          ctx.beginPath();
          ctx.moveTo(wx * s + k * s * 0.035, side * s * 0.36 - s * 0.06);
          ctx.lineTo(wx * s + k * s * 0.035, side * s * 0.36 + s * 0.06);
          ctx.stroke();
        }
      }
    }
  }
  // Body.
  const body = ctx.createLinearGradient(-s * 0.3, -s * 0.25, s * 0.3, s * 0.25);
  body.addColorStop(0, '#f1ece2');
  body.addColorStop(1, '#b9b2a6');
  ctx.fillStyle = body;
  roundRect(ctx, -s * 0.34, -s * 0.24, s * 0.66, s * 0.48, s * 0.06);
  ctx.fill();
  ctx.strokeStyle = 'rgba(40, 36, 32, 0.55)';
  ctx.lineWidth = Math.max(1, s * 0.02);
  ctx.stroke();
  // Deck details.
  ctx.fillStyle = 'rgba(70, 72, 78, 0.35)';
  roundRect(ctx, -s * 0.22, -s * 0.14, s * 0.3, s * 0.12, s * 0.02);
  ctx.fill();
  roundRect(ctx, -s * 0.22, s * 0.03, s * 0.2, s * 0.1, s * 0.02);
  ctx.fill();
  // RTG at the rear.
  ctx.fillStyle = '#3a3c42';
  roundRect(ctx, -s * 0.5, -s * 0.1, s * 0.17, s * 0.2, s * 0.03);
  ctx.fill();
  ctx.strokeStyle = '#6a6d74';
  ctx.lineWidth = Math.max(1, s * 0.018);
  for (let k = -1; k <= 1; k += 1) {
    ctx.beginPath();
    ctx.moveTo(-s * 0.5, k * s * 0.05);
    ctx.lineTo(-s * 0.33, k * s * 0.05);
    ctx.stroke();
  }
  // Robotic arm stowed at the front.
  ctx.strokeStyle = '#8d8f95';
  ctx.lineWidth = Math.max(1, s * 0.04);
  ctx.beginPath();
  ctx.moveTo(s * 0.3, s * 0.16);
  ctx.lineTo(s * 0.44, s * 0.05);
  ctx.lineTo(s * 0.44, -s * 0.12);
  ctx.stroke();
  // Remote sensing mast and camera head.
  ctx.fillStyle = '#e7e2d8';
  ctx.beginPath();
  ctx.arc(s * 0.2, -s * 0.15, s * 0.07, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#2a2d33';
  roundRect(ctx, s * 0.17, -s * 0.22, s * 0.14, s * 0.08, s * 0.02);
  ctx.fill();
  ctx.restore();

  // Compact battery gauge beside the rover.
  if (Number.isFinite(battery) && t.tile > 20) {
    const bw = Math.max(26 * t.dpr, s * 0.8);
    const bh = 4 * t.dpr;
    const bx = cx - bw / 2;
    const by = cy + s * 0.78;
    ctx.save();
    ctx.fillStyle = 'rgba(10, 8, 7, 0.7)';
    roundRect(ctx, bx - 1.5 * t.dpr, by - 1.5 * t.dpr, bw + 3 * t.dpr, bh + 3 * t.dpr, 3 * t.dpr);
    ctx.fill();
    ctx.fillStyle = battery < 20 ? '#ff5d6c' : battery < 40 ? '#f4c66c' : '#63e0c3';
    roundRect(ctx, bx, by, bw * Math.max(0, Math.min(1, battery / 100)), bh, 2 * t.dpr);
    ctx.fill();
    ctx.restore();
  }
}

/** Planet-wide dust: dimmer, redder light, drifting dust clouds and wind-blown streaks. */
function drawAtmosphere(view, t, storm, dt) {
  const { ctx } = view;
  const icy = view.scene.surface.planet === 'icy';
  if (storm > 0.01) {
    ctx.save();
    ctx.globalCompositeOperation = 'multiply';
    ctx.fillStyle = icy
      ? `rgb(${255 - 110 * storm}, ${255 - 95 * storm}, ${255 - 70 * storm})`
      : `rgb(${255 - 80 * storm}, ${255 - 125 * storm}, ${255 - 150 * storm})`;
    ctx.fillRect(0, 0, t.W, t.H);
    ctx.restore();

    if (!view.haze) view.haze = hazeTexture();
    const drift = t.now / 1000;
    ctx.save();
    for (const [scale, speed, alpha] of [[5.5, 26, 0.55], [2.6, 48, 0.4]]) {
      const size = 256 * scale * t.dpr;
      const ox = ((drift * speed * WIND.x * t.dpr) % size + size) % size;
      const oy = ((drift * speed * WIND.y * t.dpr) % size + size) % size;
      ctx.globalAlpha = alpha * storm;
      for (let x = -size + ox; x < t.W; x += size) {
        for (let y = -size + oy; y < t.H; y += size) ctx.drawImage(view.haze, x, y, size, size);
      }
    }
    ctx.restore();

    ctx.save();
    ctx.fillStyle = icy ? `rgba(190, 205, 225, ${0.14 * storm})` : `rgba(150, 98, 60, ${0.16 * storm})`;
    ctx.fillRect(0, 0, t.W, t.H);
    ctx.restore();
  }

  // Wind-blown dust streaks; a few even on calm sols.
  const wanted = Math.round(8 + storm * 220);
  while (view.dust.length < wanted) view.dust.push(newDust(t, true));
  if (view.dust.length > wanted) view.dust.length = wanted;
  const speed = (60 + storm * 520) * t.dpr;
  ctx.save();
  ctx.lineCap = 'round';
  for (let i = 0; i < view.dust.length; i += 1) {
    const particle = view.dust[i];
    particle.x += WIND.x * speed * particle.v * dt;
    particle.y += WIND.y * speed * particle.v * dt;
    if (particle.x > t.W + 40 || particle.y > t.H + 40) view.dust[i] = newDust(t, false);
    const length = (4 + storm * 26) * particle.v * t.dpr;
    ctx.strokeStyle = icy ? `rgba(220, 230, 245, ${particle.a * (0.25 + storm)})` : `rgba(226, 178, 132, ${particle.a * (0.25 + storm)})`;
    ctx.lineWidth = particle.w * t.dpr;
    ctx.beginPath();
    ctx.moveTo(particle.x, particle.y);
    ctx.lineTo(particle.x - WIND.x * length, particle.y - WIND.y * length);
    ctx.stroke();
  }
  ctx.restore();
}

function newDust(t, anywhere) {
  const fromLeft = Math.random() < 0.7;
  return {
    x: anywhere ? Math.random() * t.W : fromLeft ? -20 : Math.random() * t.W,
    y: anywhere ? Math.random() * t.H : fromLeft ? Math.random() * t.H : -20,
    v: 0.5 + Math.random() * 0.8,
    a: 0.12 + Math.random() * 0.25,
    w: 0.6 + Math.random() * 1.3,
  };
}

/** Tileable soft noise used for drifting dust clouds. */
function hazeTexture() {
  const size = 128;
  const canvas = makeCanvas(size, size);
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(size, size);
  const period = 8;
  const lattice = Array.from({ length: period * period }, () => Math.random());
  const at = (i, j) => lattice[((j % period + period) % period) * period + ((i % period + period) % period)];
  const noise = (x, y, freq) => {
    const fx = (x / size) * freq;
    const fy = (y / size) * freq;
    const i = Math.floor(fx);
    const j = Math.floor(fy);
    const tx = fx - i;
    const ty = fy - j;
    const ux = tx * tx * (3 - 2 * tx);
    const uy = ty * ty * (3 - 2 * ty);
    const scale = period / freq;
    const a = at(i * scale, j * scale);
    const b = at((i + 1) * scale, j * scale);
    const c = at(i * scale, (j + 1) * scale);
    const d = at((i + 1) * scale, (j + 1) * scale);
    return a + (b - a) * ux + (c - a) * uy + (a - b - c + d) * ux * uy;
  };
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const value = noise(x, y, 2) * 0.55 + noise(x, y, 4) * 0.3 + noise(x, y, 8) * 0.15;
      const i = (y * size + x) * 4;
      image.data[i] = 200;
      image.data[i + 1] = 150;
      image.data[i + 2] = 108;
      image.data[i + 3] = Math.max(0, Math.min(255, (value - 0.35) * 420));
    }
  }
  ctx.putImageData(image, 0, 0);
  return canvas;
}

/** Callouts pinned to where things happened. Only this sol's events get text. */
function drawCallouts(ctx, scene, t) {
  const stacks = new Map();
  for (const event of scene.events) {
    const point = pointOf(event.pos ?? event.loc);
    if (!point) continue;
    if (['STORM_ONSET', 'STORM_END'].includes(event.type)) continue; // the sky shows these
    const color = SEVERITY_COLORS[event.severity] || SEVERITY_COLORS.info;
    const cx = t.sx(point.x + 0.5);
    const cy = t.sy(point.y + 0.5);
    const isNow = scene.sol == null || Number(event.sol) === scene.sol;
    if (!isNow) {
      ctx.save();
      ctx.fillStyle = color;
      ctx.strokeStyle = 'rgba(12, 8, 6, 0.85)';
      ctx.lineWidth = 1.5 * t.dpr;
      const r = 4 * t.dpr;
      ctx.beginPath();
      ctx.moveTo(cx, cy - t.tile * 0.45);
      ctx.lineTo(cx - r, cy - t.tile * 0.45 - r * 1.8);
      ctx.lineTo(cx + r, cy - t.tile * 0.45 - r * 1.8);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.restore();
      continue;
    }
    const key = `${point.x},${point.y}`;
    const level = stacks.get(key) || 0;
    stacks.set(key, level + 1);
    const detail = eventDetail(event);
    const title = `SOL ${event.sol} · ${EVENT_TEXT[event.type] || String(event.type).replaceAll('_', ' ').toLowerCase()}`;
    callout(ctx, t, cx, cy - t.tile * 0.5, level, color, title, detail);
  }
}

function eventDetail(event) {
  const details = event.details || {};
  if (event.type === 'STUCK' && details.slip != null) return `${details.terrain || 'sand'} · slip ${Number(details.slip).toFixed(2)}`;
  if (event.type === 'DISCOVERY' && details.value != null) return `+${Math.round(details.value)} science pts`;
  if (event.type === 'GUARDRAIL_BLOCK') return details.reason || details.message || '';
  if (event.type === 'NAV_DRIFT' && Array.isArray(details.offset)) {
    const meters = Math.round(Math.hypot(details.offset[0], details.offset[1]) * TILE_METERS);
    return `${meters} m off plan · uplink due sol ${details.uplink_sol ?? '?'}`;
  }
  if (event.type === 'GROUND_UPLINK') {
    const dest = details.destination || [];
    return `${(details.directions || []).join(' ') || 'hold'} → (${dest[0]}, ${dest[1]})`;
  }
  if (details.terrain) return String(details.terrain).replaceAll('_', ' ');
  return '';
}

function callout(ctx, t, ax, ay, level, color, title, detail) {
  const d = t.dpr;
  ctx.save();
  ctx.font = `600 ${11.5 * d}px "DM Sans", system-ui, sans-serif`;
  const titleWidth = ctx.measureText(title).width;
  ctx.font = `${10.5 * d}px "DM Mono", ui-monospace, monospace`;
  const detailWidth = detail ? ctx.measureText(detail).width : 0;
  const w = Math.max(titleWidth, detailWidth) + 22 * d;
  const h = (detail ? 40 : 26) * d;
  const lift = (34 + level * (h / d + 8)) * d;
  let bx = ax + 14 * d;
  const by = ay - lift - h;
  if (bx + w > t.W - 10 * d) bx = ax - 14 * d - w;
  // Leader line.
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5 * d;
  ctx.beginPath();
  ctx.moveTo(ax, ay);
  ctx.lineTo(ax, by + h);
  ctx.lineTo(bx + (bx > ax ? 0 : w), by + h);
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(ax, ay, 3 * d, 0, Math.PI * 2);
  ctx.fill();
  // Label card.
  ctx.fillStyle = 'rgba(14, 10, 9, 0.84)';
  roundRect(ctx, bx, by, w, h, 6 * d);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.fillRect(bx, by + 5 * d, 3 * d, h - 10 * d);
  ctx.textBaseline = 'top';
  ctx.fillStyle = '#f4efe8';
  ctx.font = `600 ${11.5 * d}px "DM Sans", system-ui, sans-serif`;
  ctx.fillText(title, bx + 12 * d, by + 7 * d);
  if (detail) {
    ctx.fillStyle = '#c8bcb0';
    ctx.font = `${10.5 * d}px "DM Mono", ui-monospace, monospace`;
    ctx.fillText(detail, bx + 12 * d, by + 23 * d);
  }
  ctx.restore();
}

function label(ctx, t, x, y, text, color, scale = 0.8) {
  ctx.save();
  ctx.font = `600 ${13 * scale * t.dpr}px "DM Mono", ui-monospace, monospace`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineWidth = 3 * t.dpr;
  ctx.strokeStyle = 'rgba(12, 8, 6, 0.85)';
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
  ctx.restore();
}

function drawVignette(ctx, t) {
  const gradient = ctx.createRadialGradient(t.W / 2, t.H / 2, Math.min(t.W, t.H) * 0.35, t.W / 2, t.H / 2, Math.hypot(t.W, t.H) * 0.62);
  gradient.addColorStop(0, 'rgba(0, 0, 0, 0)');
  gradient.addColorStop(1, 'rgba(0, 0, 0, 0.5)');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, t.W, t.H);
}

/** Scale bar and north arrow, bottom-left. */
function drawScaleBar(ctx, t) {
  const d = t.dpr;
  const target = 120 * d;
  const metersPerPx = TILE_METERS / t.tile;
  const nice = [10, 20, 25, 50, 100, 200, 250, 500];
  const meters = nice.find((value) => value / metersPerPx >= target * 0.55) || 500;
  const length = meters / metersPerPx;
  const x = 18 * d;
  const y = t.H - 22 * d;
  ctx.save();
  ctx.strokeStyle = 'rgba(12, 8, 6, 0.8)';
  ctx.lineWidth = 5 * d;
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x + length, y);
  ctx.stroke();
  ctx.strokeStyle = '#f4efe8';
  ctx.lineWidth = 2 * d;
  ctx.beginPath();
  ctx.moveTo(x, y - 5 * d);
  ctx.lineTo(x, y);
  ctx.lineTo(x + length, y);
  ctx.lineTo(x + length, y - 5 * d);
  ctx.stroke();
  ctx.font = `500 ${10.5 * d}px "DM Mono", ui-monospace, monospace`;
  ctx.fillStyle = '#f4efe8';
  ctx.textBaseline = 'bottom';
  ctx.shadowColor = 'rgba(0, 0, 0, 0.9)';
  ctx.shadowBlur = 4 * d;
  ctx.fillText(`${meters} m`, x + 4 * d, y - 6 * d);
  // North arrow.
  const nx = x + length + 26 * d;
  const ny = y - 8 * d;
  ctx.beginPath();
  ctx.moveTo(nx, ny - 12 * d);
  ctx.lineTo(nx - 5 * d, ny + 3 * d);
  ctx.lineTo(nx, ny);
  ctx.lineTo(nx + 5 * d, ny + 3 * d);
  ctx.closePath();
  ctx.fill();
  ctx.textAlign = 'center';
  ctx.fillText('N', nx, ny - 14 * d);
  ctx.restore();
}

function drawBadge(ctx, t, text) {
  const d = t.dpr;
  ctx.save();
  ctx.font = `500 ${11 * d}px "DM Mono", ui-monospace, monospace`;
  const w = ctx.measureText(text).width + 20 * d;
  ctx.fillStyle = 'rgba(14, 10, 9, 0.8)';
  roundRect(ctx, t.W / 2 - w / 2, t.H / 2 - 14 * d, w, 28 * d, 6 * d);
  ctx.fill();
  ctx.fillStyle = '#e8ddd2';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, t.W / 2, t.H / 2);
  ctx.restore();
}

/** Whole-map inset: explored ground, the traverse, and where the main view is looking. */
function drawMinimap(view, rover) {
  const canvas = view.scene.minimap;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const W = Math.round((canvas.clientWidth || canvas.width) * dpr);
  const H = Math.round((canvas.clientHeight || canvas.height) * dpr);
  if (canvas.width !== W || canvas.height !== H) {
    canvas.width = W;
    canvas.height = H;
  }
  const { scene } = view;
  const surface = scene.surface;
  const scale = W / scene.size;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(surface.ready ? surface.fog : surface.preview, 0, 0, W, H);
  if (!view.miniReveal || view.miniReveal.width !== W) view.miniReveal = makeCanvas(W, H);
  const rctx = view.miniReveal.getContext('2d');
  rctx.globalCompositeOperation = 'source-over';
  rctx.clearRect(0, 0, W, H);
  rctx.drawImage(surface.ready ? surface.small : surface.preview, 0, 0, W, H);
  if (!scene.showTruth) {
    rctx.globalCompositeOperation = 'destination-in';
    rctx.drawImage(view.mask, 0, 0, W, H);
  }
  ctx.drawImage(view.miniReveal, 0, 0);

  const points = scene.path;
  if (points.length > 1) {
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 1.5 * dpr;
    ctx.beginPath();
    points.forEach((point, index) => {
      const x = (point.x + 0.5) * scale;
      const y = (point.y + 0.5) * scale;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
  if (rover) {
    ctx.fillStyle = scene.telemetry.stuck ? '#ff6b5e' : '#63e0c3';
    ctx.strokeStyle = '#0c0806';
    ctx.lineWidth = 1.5 * dpr;
    ctx.beginPath();
    ctx.arc(rover.x * scale, rover.y * scale, 3.5 * dpr, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  const cam = view.cam;
  if (cam) {
    const aspect = view.canvas.height / view.canvas.width;
    const vw = cam.viewW * scale;
    const vh = cam.viewW * aspect * scale;
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.75)';
    ctx.lineWidth = 1 * dpr;
    ctx.strokeRect(cam.cx * scale - vw / 2, cam.cy * scale - vh / 2, vw, vh);
  }
}

function roundRect(ctx, x, y, w, h, r) {
  const radius = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

export default drawPlanet;
