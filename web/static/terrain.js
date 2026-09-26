/**
 * Procedural orbital-style surface for a simulator snapshot.
 *
 * The simulator only knows a tile type per cell. This module turns that grid
 * into a lit relief image, the way an orbital camera would see it: height from
 * noise, crater bowls, dune ripples and boulders, then hillshading and cast
 * shadows from a low sun. It is pure (no DOM), so it can be timed in Node.
 */

export const PX_PER_TILE = 32;

// Mars: ochre dust, pale fractured bedrock, dark basaltic dunes.
// Icy moon: grey regolith, bright blue-white ice.
const PALETTES = {
  mars: {
    regolith: [168, 106, 70], regolithAlt: [140, 86, 58], dust: [186, 124, 84],
    bedrock: [196, 158, 120], crack: [104, 72, 52],
    sand: [92, 70, 60], sandCrest: [122, 94, 78],
    rockGround: [150, 98, 68], boulder: [92, 72, 62],
    ice: [206, 212, 212], craterFloor: [176, 114, 76],
    sky: [0.2, 0.13, 0.1], sun: [1.0, 0.94, 0.86],
  },
  icy: {
    regolith: [150, 156, 164], regolithAlt: [124, 130, 142], dust: [170, 176, 184],
    bedrock: [182, 186, 192], crack: [82, 92, 108],
    sand: [112, 118, 132], sandCrest: [146, 152, 166],
    rockGround: [132, 138, 148], boulder: [78, 84, 96],
    ice: [196, 222, 238], craterFloor: [160, 168, 178],
    sky: [0.12, 0.15, 0.22], sun: [0.95, 0.97, 1.0],
  },
};

// Sun from the north-west, the cartographic convention that keeps craters
// reading as bowls rather than domes.
const SUN_AZIMUTH = (-135 * Math.PI) / 180;
const SUN_ELEVATION = (38 * Math.PI) / 180;
const WIND = 0.55; // dune ripple crest orientation, radians

function hash2(x, y, s) {
  let h = (Math.imul(x, 374761393) + Math.imul(y, 668265263) + Math.imul(s, 1442695041)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

function vnoise(x, y, s) {
  const ix = Math.floor(x);
  const iy = Math.floor(y);
  const fx = x - ix;
  const fy = y - iy;
  const ux = fx * fx * (3 - 2 * fx);
  const uy = fy * fy * (3 - 2 * fy);
  const a = hash2(ix, iy, s);
  const b = hash2(ix + 1, iy, s);
  const c = hash2(ix, iy + 1, s);
  const d = hash2(ix + 1, iy + 1, s);
  return a + (b - a) * ux + (c - a) * uy + (a - b - c + d) * ux * uy;
}

function fbm(x, y, s, octaves) {
  let value = 0;
  let amplitude = 0.5;
  let frequency = 1;
  let norm = 0;
  for (let i = 0; i < octaves; i += 1) {
    value += amplitude * vnoise(x * frequency, y * frequency, s + i * 131);
    norm += amplitude;
    amplitude *= 0.5;
    frequency *= 2.03;
  }
  return value / norm;
}

const smoothstep = (a, b, x) => {
  const t = Math.max(0, Math.min(1, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

/**
 * Low-resolution field sampled with bilinear interpolation. Noise that only
 * varies over several tiles is evaluated per `step` pixels, not per pixel.
 */
function coarseField(width, step, fn) {
  const n = Math.ceil(width / step) + 2;
  const data = new Float32Array(n * n);
  for (let j = 0; j < n; j += 1) {
    for (let i = 0; i < n; i += 1) data[j * n + i] = fn(i * step, j * step);
  }
  return (px, py) => {
    const fx = px / step;
    const fy = py / step;
    const i = Math.floor(fx);
    const j = Math.floor(fy);
    const tx = fx - i;
    const ty = fy - j;
    const a = data[j * n + i];
    const b = data[j * n + i + 1];
    const c = data[(j + 1) * n + i];
    const d = data[(j + 1) * n + i + 1];
    return a + (b - a) * tx + (c - a) * ty + (a - b - c + d) * tx * ty;
  };
}

/**
 * Same as coarseField but Catmull-Rom interpolated, so its slope is smooth.
 * Use it for anything that feeds height: hillshading exposes bilinear kinks.
 */
function smoothField(width, step, fn) {
  const n = Math.ceil(width / step) + 4;
  const data = new Float32Array(n * n);
  for (let j = 0; j < n; j += 1) {
    for (let i = 0; i < n; i += 1) data[j * n + i] = fn((i - 1) * step, (j - 1) * step);
  }
  const cubic = (a, b, c, d, t) => b + 0.5 * t * (c - a + t * (2 * a - 5 * b + 4 * c - d + t * (3 * (b - c) + d - a)));
  return (px, py) => {
    const fx = px / step + 1;
    const fy = py / step + 1;
    const i = Math.floor(fx);
    const j = Math.floor(fy);
    const tx = fx - i;
    const ty = fy - j;
    const row = (r) => {
      const o = r * n + i;
      return cubic(data[o - 1], data[o], data[o + 1], data[o + 2], tx);
    };
    return cubic(row(j - 1), row(j), row(j + 1), row(j + 2), ty);
  };
}

/** Fit circles to crater-rim tiles when an older snapshot lacks `craters`. */
function inferCraters(terrain, size) {
  const seen = new Set();
  const craters = [];
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      if (terrain[y][x] !== 'crater_edge' || seen.has(y * size + x)) continue;
      const stack = [[x, y]];
      const cells = [];
      seen.add(y * size + x);
      while (stack.length) {
        const [cx, cy] = stack.pop();
        cells.push([cx, cy]);
        for (let dy = -2; dy <= 2; dy += 1) {
          for (let dx = -2; dx <= 2; dx += 1) {
            const nx = cx + dx;
            const ny = cy + dy;
            if (nx < 0 || ny < 0 || nx >= size || ny >= size) continue;
            if (terrain[ny][nx] !== 'crater_edge' || seen.has(ny * size + nx)) continue;
            seen.add(ny * size + nx);
            stack.push([nx, ny]);
          }
        }
      }
      if (cells.length < 4) continue;
      const mx = cells.reduce((sum, [cx]) => sum + cx, 0) / cells.length;
      const my = cells.reduce((sum, [, cy]) => sum + cy, 0) / cells.length;
      const radius = cells.reduce((sum, [cx, cy]) => sum + Math.hypot(cx - mx, cy - my), 0) / cells.length;
      if (radius >= 1.2) craters.push({ center: [mx, my], radius });
    }
  }
  return craters;
}

/**
 * Build the lit surface for a terrain grid.
 * Returns { width, rgba: Uint8ClampedArray, height: Float32Array, pxPerTile }.
 */
export function buildSurface({ terrain, size, seed = 1, planet = 'mars', craters }) {
  const P = PX_PER_TILE;
  const W = size * P;
  const palette = PALETTES[planet] || PALETTES.mars;
  const S = (Number(seed) * 7919) | 0;
  const craterList = (craters && craters.length ? craters : inferCraters(terrain, size))
    .map((crater) => ({ cx: crater.center[0] + 0.5, cy: crater.center[1] + 0.5, r: crater.radius }));

  // Per-type indicator grids at tile resolution; sampled bilinearly so
  // boundaries become organic instead of square.
  const types = ['sand', 'rocks', 'bedrock', 'crater_edge', 'ice'];
  const indicator = Object.fromEntries(types.map((type) => [type, new Float32Array(size * size)]));
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const grid = indicator[terrain[y]?.[x]];
      if (grid) grid[y * size + x] = 1;
    }
  }
  const present = Object.fromEntries(types.map((type) => [type, indicator[type].some((value) => value > 0)]));
  const sampleType = (grid, u, v) => {
    const fx = Math.max(0, Math.min(size - 1.001, u - 0.5));
    const fy = Math.max(0, Math.min(size - 1.001, v - 0.5));
    const i = Math.floor(fx);
    const j = Math.floor(fy);
    const tx = fx - i;
    const ty = fy - j;
    const i1 = Math.min(size - 1, i + 1);
    const j1 = Math.min(size - 1, j + 1);
    const a = grid[j * size + i];
    const b = grid[j * size + i1];
    const c = grid[j1 * size + i];
    const d = grid[j1 * size + i1];
    return a + (b - a) * tx + (c - a) * ty + (a - b - c + d) * tx * ty;
  };

  // Slowly varying noise, evaluated coarsely.
  const warpU = smoothField(W, 4, (px, py) => fbm(px / P * 0.8, py / P * 0.8, S + 11, 3) - 0.5);
  const warpV = smoothField(W, 4, (px, py) => fbm(px / P * 0.8 + 40.3, py / P * 0.8, S + 23, 3) - 0.5);
  const relief = smoothField(W, 8, (px, py) => fbm(px / P * 0.11, py / P * 0.11, S + 37, 4));
  const tone = coarseField(W, 4, (px, py) => fbm(px / P * 0.35, py / P * 0.35, S + 53, 4));
  const duneBody = smoothField(W, 4, (px, py) => fbm(px / P * 0.7, py / P * 0.7, S + 71, 3));
  const hummock = smoothField(W, 4, (px, py) => fbm(px / P * 1.6, py / P * 1.6, S + 5, 2));
  const edgeNoise = smoothField(W, 3, (px, py) => vnoise(px / P * 4, py / P * 4, S + 97) - 0.5);

  const height = new Float32Array(W * W);
  const albedo = new Float32Array(W * W * 3);
  const sandMask = new Float32Array(W * W);
  const rockMask = new Float32Array(W * W);
  const bedMask = new Float32Array(W * W);
  const rimMask = new Float32Array(W * W);
  const cosW = Math.cos(WIND);
  const sinW = Math.sin(WIND);

  for (let py = 0; py < W; py += 1) {
    for (let px = 0; px < W; px += 1) {
      const index = py * W + px;
      const u = (px + 0.5) / P;
      const v = (py + 0.5) / P;
      const wu = u + warpU(px, py) * 0.75;
      const wv = v + warpV(px, py) * 0.75;
      const grain = edgeNoise(px, py);
      const speckle = hash2(px, py, S + 401) - 0.5;

      const sand = smoothstep(0.34, 0.62, sampleType(indicator.sand, wu, wv) + grain * 0.12);
      const rocks = smoothstep(0.25, 0.6, sampleType(indicator.rocks, wu, wv));
      const bed = smoothstep(0.3, 0.62, sampleType(indicator.bedrock, wu, wv) + grain * 0.18);
      const rim = smoothstep(0.3, 0.7, sampleType(indicator.crater_edge, u, v));
      const ice = present.ice ? smoothstep(0.35, 0.62, sampleType(indicator.ice, wu, wv)) : 0;
      sandMask[index] = sand;
      rockMask[index] = rocks;
      bedMask[index] = bed;
      rimMask[index] = rim;

      // --- height, in tile units -------------------------------------
      let h = (relief(px, py) - 0.5) * 1.5;
      h += (hummock(px, py) - 0.5) * 0.07;
      let craterFloor = 0;
      for (const crater of craterList) {
        const dx = u - crater.cx;
        const dy = v - crater.cy;
        if (Math.abs(dx) > crater.r * 3 || Math.abs(dy) > crater.r * 3) continue;
        // A slightly irregular outline keeps the rim from looking stamped.
        const wobble = 1 + (edgeNoise(px, py) + warpU(px, py) * 0.6) * 0.14;
        const d = Math.hypot(dx, dy) / (crater.r * wobble);
        if (d < 1) {
          const bowl = 1 - d * d;
          h -= crater.r * 0.2 * bowl * (0.75 + 0.25 * bowl);
          craterFloor = Math.max(craterFloor, 1 - d);
        }
        h += crater.r * 0.085 * Math.exp(-(((d - 1) / 0.3) ** 2));
        if (d > 1) h += crater.r * 0.03 * Math.exp(-(d - 1) * 1.8);
      }
      // Sand pools in lows and carries transverse ripples plus a dune swell.
      if (sand > 0) {
        const body = duneBody(px, py);
        const along = (u * cosW + v * sinW) * 4.2 + (body - 0.5) * 3.2 + warpV(px, py) * 1.5;
        const ripple = Math.sin(along * Math.PI * 2) * (0.55 + body * 0.6);
        h += sand * (-0.06 + (body - 0.5) * 0.35 + ripple * 0.018);
      }
      if (ice > 0) h += ice * (-0.02);
      height[index] = h;

      // --- albedo --------------------------------------------------------
      const t = tone(px, py);
      const alt = smoothstep(0.35, 0.75, t);
      const fine = 0.95 + grain * 0.1 + speckle * 0.07;
      let r = palette.regolith[0] + (palette.regolithAlt[0] - palette.regolith[0]) * alt;
      let g = palette.regolith[1] + (palette.regolithAlt[1] - palette.regolith[1]) * alt;
      let b = palette.regolith[2] + (palette.regolithAlt[2] - palette.regolith[2]) * alt;
      // Bright dust mantles on high ground.
      const dust = smoothstep(0.55, 0.8, relief(px, py)) * 0.5;
      r += (palette.dust[0] - r) * dust;
      g += (palette.dust[1] - g) * dust;
      b += (palette.dust[2] - b) * dust;
      if (craterFloor > 0) {
        const k = craterFloor * 0.45;
        r += (palette.craterFloor[0] - r) * k;
        g += (palette.craterFloor[1] - g) * k;
        b += (palette.craterFloor[2] - b) * k;
      }
      if (rocks > 0) {
        r += (palette.rockGround[0] - r) * rocks * 0.7;
        g += (palette.rockGround[1] - g) * rocks * 0.7;
        b += (palette.rockGround[2] - b) * rocks * 0.7;
      }
      if (bed > 0) {
        const plate = bed * (1 - craterFloor * 0.7) * (0.72 + (tone(px, py) - 0.5) * 0.5);
        r += (palette.bedrock[0] - r) * plate;
        g += (palette.bedrock[1] - g) * plate;
        b += (palette.bedrock[2] - b) * plate;
      }
      if (sand > 0) {
        const body = duneBody(px, py);
        const along = (u * cosW + v * sinW) * 4.2 + (body - 0.5) * 3.2 + warpV(px, py) * 1.5;
        const crest = Math.max(0, Math.sin(along * Math.PI * 2)) ** 3 * (0.4 + body * 0.6);
        const sr = palette.sand[0] + (palette.sandCrest[0] - palette.sand[0]) * crest;
        const sg = palette.sand[1] + (palette.sandCrest[1] - palette.sand[1]) * crest;
        const sb = palette.sand[2] + (palette.sandCrest[2] - palette.sand[2]) * crest;
        r += (sr - r) * sand;
        g += (sg - g) * sand;
        b += (sb - b) * sand;
      }
      if (ice > 0) {
        r += (palette.ice[0] - r) * ice;
        g += (palette.ice[1] - g) * ice;
        b += (palette.ice[2] - b) * ice;
      }
      albedo[index * 3] = r * fine;
      albedo[index * 3 + 1] = g * fine;
      albedo[index * 3 + 2] = b * fine;
    }
  }

  addBedrockFractures(height, albedo, bedMask, W, P, S, palette);
  addSmallCraters(height, albedo, sandMask, rimMask, W, P, S, palette);
  addBoulders(height, albedo, rockMask, rimMask, sandMask, W, P, S, palette);

  const rgba = shade(height, albedo, W, P, palette);
  return { width: W, pxPerTile: P, rgba, height };
}

/** Voronoi fractures: light-toned bedrock broken into plates. */
function addBedrockFractures(height, albedo, bedMask, W, P, S, palette) {
  const cell = 0.8 * P;
  const cells = Math.ceil(W / cell) + 1;
  const pts = new Float32Array(cells * cells * 2);
  for (let j = 0; j < cells; j += 1) {
    for (let i = 0; i < cells; i += 1) {
      pts[(j * cells + i) * 2] = (i + 0.15 + hash2(i, j, S + 211) * 0.7) * cell;
      pts[(j * cells + i) * 2 + 1] = (j + 0.15 + hash2(i, j, S + 223) * 0.7) * cell;
    }
  }
  for (let py = 0; py < W; py += 1) {
    for (let px = 0; px < W; px += 1) {
      const index = py * W + px;
      const bed = bedMask[index];
      if (bed < 0.05) continue;
      const qx = px + (vnoise(px / 11, py / 11, S + 233) - 0.5) * cell * 0.5;
      const qy = py + (vnoise(px / 11 + 50, py / 11, S + 239) - 0.5) * cell * 0.5;
      const ci = Math.floor(qx / cell);
      const cj = Math.floor(qy / cell);
      let f1 = Infinity;
      let f2 = Infinity;
      for (let dj = -1; dj <= 1; dj += 1) {
        for (let di = -1; di <= 1; di += 1) {
          const i = ci + di;
          const j = cj + dj;
          if (i < 0 || j < 0 || i >= cells || j >= cells) continue;
          const d = Math.hypot(qx - pts[(j * cells + i) * 2], qy - pts[(j * cells + i) * 2 + 1]);
          if (d < f1) { f2 = f1; f1 = d; } else if (d < f2) f2 = d;
        }
      }
      const edge = (1 - smoothstep(0.3, 1.5, f2 - f1)) * (0.45 + vnoise(px / 7, py / 7, S + 241) * 0.8);
      height[index] += bed * (0.02 - edge * 0.014);
      const k = edge * bed * 0.26;
      albedo[index * 3] += (palette.crack[0] - albedo[index * 3]) * k;
      albedo[index * 3 + 1] += (palette.crack[1] - albedo[index * 3 + 1]) * k;
      albedo[index * 3 + 2] += (palette.crack[2] - albedo[index * 3 + 2]) * k;
    }
  }
}

/** Small, softened impact craters: the texture every real plain has. */
function addSmallCraters(height, albedo, sandMask, rimMask, W, P, S, palette) {
  const count = Math.round((W / P) * (W / P) * 0.05);
  for (let n = 0; n < count; n += 1) {
    const cx = hash2(n, 1, S + 501) * W;
    const cy = hash2(n, 2, S + 503) * W;
    const at = Math.floor(cy) * W + Math.floor(cx);
    if (sandMask[at] > 0.3 || rimMask[at] > 0.2) continue;
    const size = hash2(n, 3, S + 509);
    const radius = (0.12 + size * size * 0.55) * P;
    const fresh = hash2(n, 4, S + 521);
    const depth = (radius / P) * (0.08 + fresh * 0.14);
    const reach = Math.ceil(radius * 1.8);
    for (let y = Math.max(0, Math.floor(cy - reach)); y <= Math.min(W - 1, Math.ceil(cy + reach)); y += 1) {
      for (let x = Math.max(0, Math.floor(cx - reach)); x <= Math.min(W - 1, Math.ceil(cx + reach)); x += 1) {
        const d = Math.hypot(x + 0.5 - cx, y + 0.5 - cy) / radius;
        if (d > 1.8) continue;
        const index = y * W + x;
        let dh = d < 1 ? -depth * (1 - d * d) : 0;
        dh += depth * 0.45 * Math.exp(-(((d - 1) / 0.28) ** 2));
        height[index] += dh;
        if (d < 0.8) {
          const k = (0.8 - d) * 0.25;
          albedo[index * 3] += (palette.dust[0] - albedo[index * 3]) * k;
          albedo[index * 3 + 1] += (palette.dust[1] - albedo[index * 3 + 1]) * k;
          albedo[index * 3 + 2] += (palette.dust[2] - albedo[index * 3 + 2]) * k;
        }
      }
    }
  }
}

/** Boulders on a jittered grid, dense in rock fields and on crater rims. */
function addBoulders(height, albedo, rockMask, rimMask, sandMask, W, P, S, palette) {
  const cell = 0.2 * P;
  const cells = Math.ceil(W / cell);
  for (let j = 0; j < cells; j += 1) {
    for (let i = 0; i < cells; i += 1) {
      const cx = (i + 0.1 + hash2(i, j, S + 301) * 0.8) * cell;
      const cy = (j + 0.1 + hash2(i, j, S + 307) * 0.8) * cell;
      const at = Math.min(W - 1, Math.floor(cy)) * W + Math.min(W - 1, Math.floor(cx));
      const density = 0.01 + rockMask[at] * 0.8 + rimMask[at] * 0.28 - sandMask[at] * 0.01;
      if (hash2(i, j, S + 313) > density) continue;
      const size = hash2(i, j, S + 317);
      const radius = (0.03 + size * size * 0.14) * P * (rockMask[at] > 0.3 ? 1.1 : 0.65);
      const squash = 0.7 + hash2(i, j, S + 331) * 0.5;
      const angle = hash2(i, j, S + 337) * Math.PI;
      const ca = Math.cos(angle);
      const sa = Math.sin(angle);
      const tint = 0.8 + hash2(i, j, S + 347) * 0.4;
      const peak = (radius / P) * 0.9;
      const reach = Math.ceil(radius / Math.min(1, squash)) + 1;
      for (let y = Math.max(0, Math.floor(cy - reach)); y <= Math.min(W - 1, Math.ceil(cy + reach)); y += 1) {
        for (let x = Math.max(0, Math.floor(cx - reach)); x <= Math.min(W - 1, Math.ceil(cx + reach)); x += 1) {
          const dx = x + 0.5 - cx;
          const dy = y + 0.5 - cy;
          const lx = (dx * ca + dy * sa) / radius;
          const ly = (-dx * sa + dy * ca) / (radius * squash);
          const d2 = lx * lx + ly * ly;
          if (d2 >= 1) continue;
          const index = y * W + x;
          const bump = Math.sqrt(1 - d2) * peak;
          height[index] += bump;
          const k = Math.min(1, (1 - d2) * 3);
          albedo[index * 3] += (palette.boulder[0] * tint - albedo[index * 3]) * k;
          albedo[index * 3 + 1] += (palette.boulder[1] * tint - albedo[index * 3 + 1]) * k;
          albedo[index * 3 + 2] += (palette.boulder[2] * tint - albedo[index * 3 + 2]) * k;
        }
      }
    }
  }
}

/** Lambert hillshade, cast shadows marched toward the sun, sky ambient. */
function shade(height, albedo, W, P, palette) {
  const rgba = new Uint8ClampedArray(W * W * 4);
  const ce = Math.cos(SUN_ELEVATION);
  const lx = Math.cos(SUN_AZIMUTH) * ce;
  const ly = Math.sin(SUN_AZIMUTH) * ce;
  const lz = Math.sin(SUN_ELEVATION);
  const stepPx = 2;
  const steps = 22;
  const sdx = Math.cos(SUN_AZIMUTH) * stepPx;
  const sdy = Math.sin(SUN_AZIMUTH) * stepPx;
  const rise = (stepPx / P) * Math.tan(SUN_ELEVATION);
  const exaggerate = 1.6;
  const [skyR, skyG, skyB] = palette.sky;
  const [sunR, sunG, sunB] = palette.sun;

  for (let py = 0; py < W; py += 1) {
    for (let px = 0; px < W; px += 1) {
      const index = py * W + px;
      const xl = px > 0 ? index - 1 : index;
      const xr = px < W - 1 ? index + 1 : index;
      const yu = py > 0 ? index - W : index;
      const yd = py < W - 1 ? index + W : index;
      const dhx = ((height[xr] - height[xl]) * P) / (xr - xl || 1) * exaggerate;
      const dhy = ((height[yd] - height[yu]) * P) / ((yd - yu) / W || 1) * exaggerate;
      const inv = 1 / Math.hypot(dhx, dhy, 1);
      const nx = -dhx * inv;
      const ny = -dhy * inv;
      const nz = inv;
      const lambert = Math.max(0, nx * lx + ny * ly + nz * lz);

      const h0 = height[index];
      let occlusion = 0;
      let sx = px + 0.5;
      let sy = py + 0.5;
      for (let i = 1; i <= steps; i += 1) {
        sx += sdx;
        sy += sdy;
        if (sx < 0 || sy < 0 || sx >= W || sy >= W) break;
        const over = height[(sy | 0) * W + (sx | 0)] - (h0 + rise * i);
        if (over > 0) {
          occlusion = Math.max(occlusion, Math.min(1, over * 60 / (1 + i * 0.12)));
          if (occlusion >= 1) break;
        }
      }
      const direct = (lambert / lz) * (1 - occlusion * 0.82);
      const skyLight = 0.3 + nz * 0.12;
      const r = albedo[index * 3] / 255;
      const g = albedo[index * 3 + 1] / 255;
      const b = albedo[index * 3 + 2] / 255;
      const outR = r * (direct * 0.78 * sunR + skyLight * (0.55 + skyR));
      const outG = g * (direct * 0.78 * sunG + skyLight * (0.55 + skyG));
      const outB = b * (direct * 0.78 * sunB + skyLight * (0.55 + skyB));
      // Gentle filmic shoulder keeps sunlit slopes from clipping.
      rgba[index * 4] = 255 * (outR / (1 + outR * 0.18)) * 1.12;
      rgba[index * 4 + 1] = 255 * (outG / (1 + outG * 0.18)) * 1.12;
      rgba[index * 4 + 2] = 255 * (outB / (1 + outB * 0.18)) * 1.12;
      rgba[index * 4 + 3] = 255;
    }
  }
  return rgba;
}
