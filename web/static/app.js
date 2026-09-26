import { drawPlanet } from './map.js';

const $ = (selector) => document.querySelector(selector);
const byId = (id) => document.getElementById(id);
const colors = { info: '#55d6be', minor: '#ffd166', major: '#ff8c42', critical: '#ff4d6d' };
const hazards = new Set(['sand', 'rocks', 'crater_edge', 'ice']);
const state = {
  mission: null, replay: null, source: 'preview', missionNote: '', index: 0,
  timer: null, harness: null, selectedVersion: null, showTruth: false,
};

function safe(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

function fmt(value, digits = 1) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : '—';
}

function point(value) {
  if (Array.isArray(value) && value.length >= 2) return [Number(value[0]), Number(value[1])];
  if (value && typeof value === 'object') {
    if (Array.isArray(value.loc)) return point(value.loc);
    if (Array.isArray(value.pos)) return point(value.pos);
    if (Number.isFinite(Number(value.x)) && Number.isFinite(Number(value.y))) return [Number(value.x), Number(value.y)];
  }
  return null;
}

function prettyEvent(type) {
  const names = {
    STUCK: 'Rover stuck in soft sand', FREED: 'Rover freed itself', FALL: 'Crater fall',
    WHEEL_DAMAGE: 'Wheel damage', BATTERY_LOW: 'Battery running low',
    BATTERY_CRITICAL: 'Battery critically low', STORM_ONSET: 'Dust storm begins',
    STORM_END: 'Dust storm eases', DISCOVERY: 'Science deposit confirmed',
    DEATH: 'Rover mission ended', GUARDRAIL_BLOCK: 'Safety rule blocked an action',
    NAV_DRIFT: 'Lost position in the dust storm', GROUND_UPLINK: 'Earth uplinked a new route',
  };
  return names[type] || String(type || 'Mission event').replaceAll('_', ' ').toLowerCase().replace(/^./, (x) => x.toUpperCase());
}

/** Storm drift and Earth uplink status as of this frame. */
function groundStatus(frame) {
  const events = (frame.events || []).filter((event) => Number(event.sol) <= Number(frame.sol || 0));
  const drift = [...events].reverse().find((event) => event.type === 'NAV_DRIFT');
  const uplink = [...events].reverse().find((event) => event.type === 'GROUND_UPLINK');
  const holding = Boolean(drift && (!uplink || Number(uplink.sol) < Number(drift.sol)));
  const destination = uplink?.details?.destination;
  const pos = point(frame.pos);
  const following = Boolean(!holding && uplink && destination && pos && (pos[0] !== destination[0] || pos[1] !== destination[1]));
  return { drift, uplink, holding, following };
}

function currentEventAt(sol) {
  return (state.replay?.frames?.[state.index]?.events || []).filter((event) => Number(event.sol) === Number(sol));
}

async function getJson(url) {
  const response = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { message = (await response.json()).detail || message; } catch { /* use status text */ }
    throw new Error(message);
  }
  return response.json();
}

function missionKey(mission) {
  return mission?.mission_id ?? mission?._id;
}

function updateSource(source, note) {
  state.source = source || 'preview';
  state.missionNote = note || '';
  const badge = byId('source-badge');
  const preview = state.source === 'preview';
  badge.classList.toggle('preview', preview);
  badge.classList.toggle('atlas', !preview);
  badge.lastElementChild.textContent = preview ? 'Local simulator preview' : 'Atlas · read-only';
  const noteBox = byId('source-note');
  if (preview) {
    noteBox.hidden = false;
    noteBox.textContent = 'Local preview data: the rover mission is a deterministic simulator replay. Engineer patches, harness versions, and held-out scores are illustrative examples until Atlas contains real records.';
  } else if (note) {
    noteBox.hidden = false;
    noteBox.textContent = `Atlas data · ${note}`;
  } else {
    noteBox.hidden = true;
    noteBox.textContent = '';
  }
  byId('connection-state').textContent = preview ? 'Showing deterministic local preview' : 'Connected to Atlas · read only';
}

function setLoading(message) {
  byId('connection-state').textContent = message;
  byId('map-caption').textContent = message;
}

// Missions shown in the picker for the demo: Earth-dependent baseline, then the self-improved harness.
const DEMO_MISSIONS = ['m_1790454106_42_live_v1_f4d1', 'm_1790460855_42_live_v6_a31a'];

async function loadMissionList() {
  try {
    const response = await getJson('/api/missions');
    const all = response.items || [];
    const pinned = all.filter((m) => DEMO_MISSIONS.includes(missionKey(m)));
    const live = pinned.length ? pinned : all.filter((m) => m.mode !== 'eval' && m.world_source !== 'fake' && m.status === 'done');
    const missions = (live.length ? live : all).sort((x, y) => Number(x.harness_version) - Number(y.harness_version) || String(y.started_at).localeCompare(String(x.started_at)));
    const picker = byId('mission-select');
    picker.replaceChildren();
    missions.forEach((mission) => {
      const option = document.createElement('option');
      option.value = missionKey(mission);
      const v = Number(mission.harness_version);
      const who = v === 1 ? 'Human-written harness (waits for Earth)' : `Self-improved harness v${v}`;
      const m = mission.metrics || {};
      const fate = m.alive === false ? `lost on sol ${m.sols_survived}` : m.sols_survived ? `survived ${m.sols_survived} sols` : mission.status;
      option.textContent = `v${v} · ${who} · ${fate} · seed ${mission.seed}`;
      picker.append(option);
    });
    if (!missions.length) throw new Error('No missions are available yet.');
    picker.addEventListener('change', () => loadMission(picker.value));
    await loadMission(picker.value);
  } catch (error) {
    updateSource('preview', '');
    byId('connection-state').textContent = 'Mission data could not be loaded';
    byId('mission-summary').innerHTML = `<div class="error-card">${safe(error.message)}</div>`;
    byId('map-caption').textContent = 'Mission data could not be loaded.';
  }
}

async function loadMission(id) {
  stopPlayback();
  setLoading('Loading the mission and replay…');
  try {
    const [detail, replay] = await Promise.all([
      getJson(`/api/missions/${encodeURIComponent(id)}`),
      getJson(`/api/missions/${encodeURIComponent(id)}/replay`),
    ]);
    state.mission = detail.mission;
    state.replay = prepareReplay(replay);
    state.index = 0;
    updateSource(detail.source || replay.source, detail.note || replay.note);
    setupSlider();
    renderMission();
  } catch (error) {
    byId('map-caption').textContent = `Could not load this replay: ${error.message}`;
    byId('connection-state').textContent = 'Mission replay unavailable';
  }
}

/**
 * Missions recorded without a world snapshot only know the tiles the rover saw.
 * Fill the terrain from the final map knowledge once, so the surface is built
 * one time rather than on every sol. Also derive end-of-sol stops for the map.
 */
function prepareReplay(replay) {
  const frames = replay?.frames || [];
  const snapshot = replay?.snapshot;
  if (snapshot?.terrain?.length && frames.length) {
    const terrain = snapshot.terrain.map((row) => [...row]);
    let filled = false;
    for (const tile of frames.at(-1).known_tiles || []) {
      const p = point(tile.loc ?? tile.pos ?? tile);
      if (p && terrain[p[1]]?.[p[0]] === 'unknown' && tile.terrain) {
        terrain[p[1]][p[0]] = tile.terrain;
        filled = true;
      }
    }
    if (filled) replay = { ...replay, snapshot: { ...snapshot, terrain } };
  }
  const waypoints = [];
  let last = null;
  for (const frame of frames) {
    const p = point(frame.pos);
    if (p && (!last || p[0] !== last[0] || p[1] !== last[1])) {
      waypoints.push({ pos: p, sol: Number(frame.sol) });
      last = p;
    }
  }
  return { ...replay, waypoints };
}

function setupSlider() {
  const frames = state.replay?.frames || [];
  const slider = byId('sol-slider');
  slider.min = '0';
  slider.max = String(Math.max(0, frames.length - 1));
  slider.value = '0';
  const stormSol = (state.replay?.snapshot?.storms || [])[0]?.start_sol
    ?? (state.replay?.frames || []).flatMap((frame) => frame.events || []).find((event) => event.type === 'STORM_ONSET')?.sol;
  byId('storm-tick').textContent = stormSol == null ? 'Weather' : `Storm · sol ${stormSol}`;
  byId('timeline-end').textContent = `Sol ${frames.at(-1)?.sol ?? state.replay?.max_sols ?? '—'}`;
  const span = Math.max(1, frames.length - 1);
  const indexOfSol = (sol) => Math.max(0, frames.findIndex((frame) => Number(frame.sol) >= Number(sol)));
  const marks = [];
  for (const storm of state.replay?.snapshot?.storms || []) {
    const from = indexOfSol(Number(storm.start_sol) - 2) / span * 100;
    const to = Math.min(100, indexOfSol(Number(storm.start_sol) + Number(storm.length || 3)) / span * 100);
    marks.push(`<i class="storm" style="left:${from}%;width:${Math.max(1, to - from)}%"></i>`);
  }
  for (const event of frames.at(-1)?.events || []) {
    if (['STORM_ONSET', 'STORM_END'].includes(event.type)) continue;
    marks.push(`<i style="left:${indexOfSol(event.sol) / span * 100}%;--mark:${colors[event.severity] || colors.info}" title="${safe(prettyEvent(event.type))}"></i>`);
  }
  byId('timeline-marks').innerHTML = marks.join('');
  slider.setAttribute('aria-valuetext', frames.length ? `Sol ${frames[0].sol}` : 'No telemetry');
}

function renderMission() {
  const mission = state.mission;
  const replay = state.replay;
  if (!mission || !replay?.frames?.length) return;
  const frame = replay.frames[state.index];
  const sol = Number(frame.sol || 0);
  const maxSol = Number(replay.max_sols || replay.frames.at(-1)?.sol || 0);
  const slider = byId('sol-slider');
  slider.value = String(state.index);
  slider.setAttribute('aria-valuetext', `Sol ${sol}${Number(frame.tau) > 2 ? ', dust storm active' : ''}`);
  byId('sol-number').textContent = String(sol);
  byId('sol-output').textContent = `SOL ${sol} / ${maxSol}`;

  const metrics = mission.metrics || replay.metrics || {};
  const summary = [
    ['Mission', `Seed ${mission.seed ?? replay.seed ?? '—'}`],
    ['Planet', mission.planet || replay.planet || '—'],
    ['Science returned', `${fmt(metrics.science, 1)} pts`],
    ['Distance traveled', `${metrics.distance ?? '—'} tiles`],
    ['Mission result', mission.status || (metrics.alive === false ? 'ended' : `${metrics.sols_survived ?? maxSol} sols`)],
  ];
  byId('mission-summary').innerHTML = summary.map(([label, value]) => `<div class="summary-cell"><span>${safe(label)}</span><strong>${safe(value)}</strong></div>`).join('');

  renderEnvironment(frame, replay);
  renderRover(frame);
  renderActions(frame, replay);
  renderCaption(frame);
  renderEventFeed(frame);
  const canvas = byId('planet');
  const known = (frame.known_tiles || []).filter((tile) => Number(tile.first_seen_sol ?? 0) <= sol);
  drawPlanet(canvas, replay.snapshot, {
    path: frame.path || [], roverPos: frame.pos, knownTiles: known,
    events: (frame.events || []).filter((event) => Number(event.sol) <= sol),
    showTruth: state.showTruth, weatherTau: frame.tau,
    sol, actions: frame.actions || frame.planned || [],
    rover: { battery: frame.battery, stuck: Boolean(frame.stuck) },
    waypoints: replay.waypoints || [], minimap: byId('minimap'),
  });
  byId('map-title').textContent = `${(mission.planet || replay.planet || 'Planet').replace(/^./, (x) => x.toUpperCase())} surface · sol ${sol}`;
}

function renderEnvironment(frame, replay) {
  const tau = Number(frame.tau ?? 0.5);
  const trend = Number(frame.tau_trend ?? 0);
  const storm = tau > 2;
  const building = !storm && trend > 0.25;
  const solarBase = (replay.planet || replay.snapshot?.planet) === 'icy' ? 21 : 30;
  const panelDust = Number(frame.panel_dust || 0);
  const solarGain = solarBase * Math.exp(-tau / 1.5) * (1 - panelDust);
  const sunlight = Math.round((solarGain / solarBase) * 100);
  const sky = document.querySelector('.hud-sky');
  sky.classList.toggle('storm', storm);
  sky.classList.toggle('building', building);
  byId('weather-badge').textContent = storm ? 'Dust storm' : building ? 'Dust rising' : tau > 0.8 ? 'Hazy sky' : 'Clear sky';
  const arrow = trend > 0.05 ? '↑' : trend < -0.05 ? '↓' : '';
  byId('weather-line').textContent = `τ ${tau.toFixed(2)}${arrow} · sunlight ${sunlight}%`;
  byId('weather-line').title = `Dust opacity (tau) ${tau.toFixed(2)}: about 0.5 is clear, above 2 is a storm. Solar charging ≈ +${solarGain.toFixed(1)}% battery per sol.`;
  const sun = byId('hud-sun');
  sun.style.opacity = String(Math.max(0.25, sunlight / 100));
  sun.style.filter = storm ? 'blur(2px) sepia(0.8)' : building ? 'blur(1px)' : 'none';
}

function renderRover(frame) {
  const position = point(frame.pos) || [0, 0];
  const [x, y] = position;
  const battery = Number(frame.battery ?? 0);
  const wheel = Number(frame.wheel_health ?? 1);
  const stuck = Boolean(frame.stuck);
  const sol = Number(frame.sol || 0);
  const tile = (frame.known_tiles || []).find((record) => {
    const p = point(record.loc ?? record.pos ?? record);
    return p && p[0] === x && p[1] === y && Number(record.first_seen_sol ?? 0) <= sol;
  });
  const terrain = tile?.terrain || state.replay?.snapshot?.terrain?.[y]?.[x];
  byId('position-value').textContent = `x ${x} · y ${y}`;
  byId('battery-value').textContent = `${Math.round(battery)}%`;
  byId('wheel-value').textContent = `${Math.round(wheel * 100)}%`;
  byId('terrain-value').textContent = terrain && terrain !== 'unknown' ? terrain.replaceAll('_', ' ') : 'unmapped';
  byId('battery-meter').style.width = `${Math.max(0, Math.min(100, battery))}%`;
  byId('battery-meter').style.background = battery < 20 ? 'var(--red)' : battery < 40 ? 'var(--gold)' : 'var(--mint)';
  byId('wheel-meter').style.width = `${Math.max(0, Math.min(100, wheel * 100))}%`;
  const tools = (frame.actions || []).map((raw) => (raw.action || raw).tool);
  const ground = groundStatus(frame);
  const pill = byId('rover-state');
  pill.textContent = ground.holding ? 'WAITING FOR EARTH' : stuck ? 'STUCK' : battery < 20 ? 'LOW POWER'
    : tools.includes('drill') ? 'DRILLING' : tools.includes('scan') ? 'SCANNING'
    : tools.includes('move') ? 'DRIVING' : tools.includes('shelter') ? 'SHELTERING' : sol === 0 ? 'LANDED' : 'IDLE';
  pill.classList.toggle('danger', stuck || battery < 10);
  pill.classList.toggle('warning', ground.holding || (!stuck && battery >= 10 && battery < 25));
}

/** One line on the map saying what physically happened this sol. */
function renderCaption(frame) {
  const sol = Number(frame.sol || 0);
  const caption = byId('map-caption');
  const current = currentEventAt(sol);
  const actions = (frame.actions || []).map(actionDetails);
  const moves = actions.filter((item) => item.tool === 'move');
  const names = { N: 'north', NE: 'north-east', E: 'east', SE: 'south-east', S: 'south', SW: 'south-west', W: 'west', NW: 'north-west' };
  let text;
  let alert = false;
  const stuckNow = current.find((event) => event.type === 'STUCK');
  const freed = current.find((event) => event.type === 'FREED');
  const found = current.find((event) => event.type === 'DISCOVERY');
  const drift = current.find((event) => event.type === 'NAV_DRIFT');
  const uplink = current.find((event) => event.type === 'GROUND_UPLINK');
  const ground = groundStatus(frame);
  if (drift) {
    alert = true;
    const off = drift.details?.offset || [0, 0];
    text = `Driving blind in the dust storm: visual odometry lost lock and the wheels slid on drifted soil. The rover ended about ${Math.round(Math.hypot(off[0], off[1]) * 10)} m from its planned stop, so it halts and waits for Earth.`;
  } else if (uplink) {
    const dest = uplink.details?.destination || [];
    text = `Uplink from Earth: position fixed from orbital images, plus a hazard-free route to (${dest[0]}, ${dest[1]}): ${(uplink.details?.directions || []).join(' → ') || 'hold'}.`;
  } else if (ground.holding) {
    alert = true;
    text = `Holding position. Earth is locating the rover from orbit; the new route is due at the end of sol ${ground.drift?.details?.uplink_sol ?? '?'}.`;
  } else if (ground.following || (ground.uplink && Number(ground.uplink.sol) === sol - 1)) {
    text = 'Driving the route uplinked from Earth, back to where it was headed.';
  } else if (sol === 0) text = 'Landed. The rover can see about 30 m around it; everything dim is still unknown.';
  else if (stuckNow) {
    alert = true;
    text = `Drove into soft sand and bogged down${stuckNow.details?.slip != null ? ` (slip ${Number(stuckNow.details.slip).toFixed(2)})` : ''}. The wheels are digging in.`;
  } else if (freed) text = 'The wheels found grip and the rover climbed out of the sand.';
  else if (found) text = `Drilled the target and confirmed a deposit: +${Math.round(found.details?.value || 0)} science points.`;
  else if (frame.stuck) {
    alert = true;
    text = 'Still stuck. Each attempt to drive out spins the wheels and drains power.';
  } else if (moves.length) {
    const raw = (frame.actions || []).find((item) => (item.action || item).tool === 'move');
    const args = (raw?.action || raw)?.args || {};
    const direction = names[String(args.direction || '').toUpperCase()] || 'ahead';
    const steps = Number(args.steps || 1);
    text = moves.some((item) => !item.ok) ? `Tried to drive ${direction}, but the move failed: ${moves.find((item) => !item.ok).detail}` : `Drove ${direction} about ${steps * 10} m.`;
  } else if (actions.some((item) => item.tool === 'scan')) text = 'Scanned the ground within about 50 m for science signals.';
  else if (actions.some((item) => item.tool === 'probe_terrain')) text = 'Probed the ground ahead to measure how soft it is.';
  else if (actions.some((item) => item.tool === 'shelter')) text = 'Sheltering: instruments powered down to ride out the dust.';
  else text = 'Holding position.';
  if (Number(frame.tau) > 2 && !alert) text += ' Dust is cutting the sunlight.';
  caption.innerHTML = `<b>SOL ${safe(sol)}</b>${safe(text)}`;
  caption.classList.toggle('alert', alert);
}

function actionDetails(raw, index) {
  const action = raw.action || raw;
  const tool = action.tool || action.name || (typeof action === 'string' ? action : 'observe');
  const args = action.args || raw.args || {};
  const descriptions = {
    move: `Move ${args.direction || ''}${args.steps ? ` · ${args.steps} tile${args.steps === 1 ? '' : 's'}` : ''}`.trim(),
    scan: 'Search nearby ground for possible science signals',
    drill: 'Drill this location to verify and collect a deposit',
    probe_terrain: 'Measure the slip risk on nearby terrain',
    shelter: 'Reduce power use during the storm',
    wait: 'Hold position',
  };
  const result = raw.ok === false ? 'Blocked' : raw.ok === true ? 'Done' : 'Planned';
  const detail = raw.message || descriptions[tool] || (typeof action === 'string' ? action : 'Rover action');
  return { tool, detail, result, ok: raw.ok !== false, index };
}

function renderActions(frame, replay) {
  const actions = (frame.actions || frame.planned || []).map(actionDetails);
  const list = byId('action-list');
  if (!actions.length) {
    list.innerHTML = '<span class="muted">At mission start, the rover is positioned and waiting for its first plan.</span>';
  } else {
    list.innerHTML = actions.map((action, index) => `<div class="action-item"><span class="action-index">${String(index + 1).padStart(2, '0')}</span><span class="action-name">${safe(action.tool.replaceAll('_', ' '))}<span class="action-desc">${safe(action.detail)}</span></span><span class="action-result ${action.ok ? '' : 'failed'}">${safe(action.result)}</span></div>`).join('');
  }
  const current = currentEventAt(frame.sol);
  const stuck = current.find((event) => event.type === 'STUCK');
  const storm = current.find((event) => event.type === 'STORM_ONSET') || Number(frame.tau) > 2;
  const freed = current.find((event) => event.type === 'FREED');
  const discovery = current.find((event) => event.type === 'DISCOVERY');
  const telemetryReason = frame.reasoning || frame.rationale || frame.explanation;
  let reasoning = telemetryReason;
  let kind = telemetryReason ? 'MISSION TELEMETRY' : 'SIMULATOR REFERENCE POLICY';
  const drift = current.find((event) => event.type === 'NAV_DRIFT');
  const uplink = current.find((event) => event.type === 'GROUND_UPLINK');
  const ground = groundStatus(frame);
  if (!reasoning && drift) reasoning = 'It kept driving through the dust storm. With the cameras blinded, visual odometry lost lock and the rover slid off course, so it stopped until Earth can fix its position.';
  else if (!reasoning && uplink) reasoning = 'Earth located the rover in orbital images and uplinked a route around the mapped hazards back to the original destination.';
  else if (!reasoning && ground.holding) reasoning = 'Driving is disabled after the storm drift. The rover waits for the next communication window with Earth.';
  else if (!reasoning && ground.following) reasoning = 'The rover drives the route Earth uplinked instead of its own straight-line plan.';
  if (!reasoning && stuck) reasoning = 'The direct eastbound route entered high-slip sand without probing first. The rover is stuck and spends energy trying to free itself.';
  else if (!reasoning && freed) reasoning = 'A recovery attempt freed the rover. It returns to the direct route toward the science signal.';
  else if (!reasoning && storm) reasoning = 'Planet-wide dust opacity is high, reducing solar charging. This reference policy keeps following its current plan instead of sheltering.';
  else if (!reasoning && discovery) reasoning = 'The rover reached the signal and drilled the deposit. Science has been added to the mission total.';
  else if (!reasoning && actions.some((item) => item.tool === 'scan')) reasoning = 'The rover is scanning nearby terrain for science signals. A signal can be a false positive, so drilling is needed to confirm it.';
  else if (!reasoning && actions.some((item) => item.tool === 'drill')) reasoning = 'The rover is drilling its current tile to check whether an anomaly signal marks a real deposit.';
  else if (!reasoning && actions.some((item) => item.tool === 'move')) reasoning = 'The greedy reference policy follows the strongest known anomaly in a straight line. It does not probe sand or route around visible hazards.';
  else if (!reasoning) reasoning = 'No reasoning note was recorded for this sol. The rover is monitoring its instruments and waiting for the next plan.';
  byId('reasoning-kind').textContent = kind;
  byId('reasoning-text').textContent = reasoning;
  const block = current.find((event) => event.type === 'GUARDRAIL_BLOCK');
  const note = byId('guardrail-note');
  note.hidden = !block;
  note.textContent = block ? `Safety guardrail: ${block.details?.reason || block.details?.message || 'the proposed action was blocked before execution.'}` : '';
}

function renderEventFeed(frame) {
  const events = (frame.events || []).filter((event) => Number(event.sol) <= Number(frame.sol || 0));
  const feed = byId('event-feed');
  byId('event-count').textContent = String(events.length);
  const recent = [...events].reverse().slice(0, 8);
  if (!recent.length) {
    feed.innerHTML = '<li class="empty-state">No mission events recorded yet. The feed will explain incidents and discoveries as they happen.</li>';
    return;
  }
  feed.innerHTML = recent.map((event) => {
    const location = point(event.pos ?? event.loc);
    const where = location ? ` · (${location[0]}, ${location[1]})` : '';
    const details = event.details?.terrain ? ` · ${event.details.terrain}` : '';
    return `<li style="--event-color:${colors[event.severity] || colors.info}"><time>Sol ${safe(event.sol)}</time><span>${safe(prettyEvent(event.type))}${safe(where)}${safe(details)}</span></li>`;
  }).join('');
}

function stopPlayback() {
  if (state.timer) window.clearInterval(state.timer);
  state.timer = null;
  byId('play').innerHTML = '▶ <span>Play replay</span>';
}

function startPlayback() {
  if (!state.replay?.frames?.length) return;
  if (state.index >= state.replay.frames.length - 1) state.index = 0;
  byId('play').innerHTML = 'Ⅱ <span>Pause replay</span>';
  renderMission();
  state.timer = window.setInterval(() => {
    if (state.index >= state.replay.frames.length - 1) {
      stopPlayback();
      return;
    }
    state.index += 1;
    renderMission();
  }, 800);
}

const TOOL_NAMES = { move: 'Drive', scan: 'Scan', drill: 'Drill', wait: 'Wait', probe_terrain: 'Test the ground', shelter: 'Shelter from storms' };

function plainOp(op) {
  const p = op.params || {};
  switch (op.op) {
    case 'enable_tool': return `Learned a new skill: ${TOOL_NAMES[op.tool] || op.tool}`;
    case 'disable_tool': return `Stopped using: ${TOOL_NAMES[op.tool] || op.tool}`;
    case 'add_rule': return `New rule: ${op.text}`;
    case 'remove_rule': return `Dropped a rule (${op.id})`;
    case 'add_guardrail':
    case 'update_guardrail': return guardText({ type: op.type, params: p, id: op.id }, op.op === 'add_guardrail' ? 'New safety check: ' : 'Tightened safety check: ');
    case 'remove_guardrail': return `Removed safety check ${op.id}`;
    case 'set_context': return `Memory: ${String(op.field).replaceAll('_', ' ')} → ${op.value}`;
    case 'set_param': return `Setting: ${String(op.field).replaceAll('_', ' ')} → ${op.value}`;
    case 'edit_prompt': return 'Rewrote part of its mission briefing';
    default: return op.op || 'Change';
  }
}

function guardText(g, prefix = '') {
  const p = g.params || {};
  const text = {
    min_battery_for_move: `no driving below ${p.threshold}% battery`,
    avoid_terrain: `never drive onto ${String(p.terrain || 'hazard').replaceAll('_', ' ')} without testing it first`,
    max_steps_per_move: `drive at most ${p.n} steps at a time`,
    shelter_when_tau_above: `shelter automatically when dust is above ${p.tau}`,
    no_drill_below_battery: `no drilling below ${p.threshold}% battery`,
  }[g.type] || `${g.type}`;
  return prefix + text;
}

function plainReason(reason) {
  if (!reason) return '';
  if (/died/i.test(reason)) return 'Not adopted: in a test run the rover died where the old version survived. Safety comes first.';
  return reason.replace(/^C\d:\s*/, '');
}

function firstSentence(text) {
  const t = String(text || '').trim();
  const m = t.match(/^(.{20,220}?[.!?])(\s|$)/);
  return m ? m[1] : t.slice(0, 220);
}

async function loadEngineer() {
  const timeline = byId('patch-timeline');
  timeline.innerHTML = '<div class="loading-card">Loading…</div>';
  try {
    state.harness = await getJson('/api/harness');
    updateSource(state.harness.source || state.source, state.harness.note || state.missionNote);
    renderEngineer();
    renderHarness();
  } catch (error) {
    timeline.innerHTML = `<div class="error-card">Could not load: ${safe(error.message)}</div>`;
  }
}

function renderEngineer() {
  const patches = state.harness?.patches || [];
  byId('engineer-overview').innerHTML = '';
  const timeline = byId('patch-timeline');
  if (!patches.length) {
    timeline.innerHTML = '<div class="loading-card">No repairs yet. When the rover gets into trouble, the Engineer\'s fix appears here.</div>';
    return;
  }
  timeline.innerHTML = [...patches].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || ''))).map((patch) => {
    const ok = patch.status === 'accepted';
    const to = patch.result_version ?? patch.candidate_version;
    const ops = (patch.ops || []).map((op) => `<li>${safe(plainOp(op))}</li>`).join('');
    const ev = patch.eval || {};
    const score = ev.baseline_score != null && ev.candidate_score != null
      ? `<div class="action-line"><span class="al-icon">📈</span><span>Mission score <strong>${safe(fmt(ev.baseline_score))}</strong> → <strong class="${Number(ev.candidate_score) >= Number(ev.baseline_score) ? 'score-up' : 'score-down'}">${safe(fmt(ev.candidate_score))}</strong></span></div>` : '';
    return `<article class="patch-card simple">
      <div class="patch-top"><h2>v${safe(patch.base_version)} → v${safe(to ?? '?')}</h2><span class="patch-status ${ok ? 'accepted' : 'rejected'}">${ok ? '✅ Adopted' : '❌ Not adopted'}</span></div>
      <div class="action-line"><span class="al-icon">⚠️</span><span><strong>What went wrong:</strong> ${safe(firstSentence(patch.diagnosis || patch.rationale))}</span></div>
      <div class="action-line"><span class="al-icon">🔧</span><span><strong>What the Engineer changed:</strong><ul class="al-list">${ops}</ul></span></div>
      ${score}
      ${ok ? `<div class="action-line"><span class="al-icon">🚀</span><span>Now running on the rover as <strong>v${safe(to)}</strong>, with no call to Earth.</span></div>`
           : `<div class="action-line"><span class="al-icon">🛡️</span><span>${safe(plainReason(patch.reason))}</span></div>`}
    </article>`;
  }).join('');
}

async function selectVersion(version) {
  state.selectedVersion = Number(version);
  renderHarness();
  const versions = state.harness?.versions || [];
  renderVersionDetail(versions.find((item) => Number(item.version) === state.selectedVersion));
}

function renderVersionDetail(version) {
  const detail = byId('version-detail');
  if (!version) { detail.innerHTML = '<div class="loading-card">Pick a version.</div>'; return; }
  const skills = Object.entries(version.tools || {}).filter(([, on]) => on).map(([t]) => `<span class="op-chip">${safe(TOOL_NAMES[t] || t)}</span>`).join('');
  const patch = (state.harness?.patches || []).find((pt) => Number(pt.result_version ?? pt.candidate_version) === Number(version.version));
  const changed = patch ? (patch.ops || []).map((op) => `<li>${safe(plainOp(op))}</li>`).join('') : '';
  const rules = (version.rules || []).map((r) => `<li>${safe(r.text)}</li>`).join('');
  const guards = (version.guardrails || []).map((g) => `<li>${safe(guardText(g))}</li>`).join('');
  const label = version.status === 'active' ? '🟢 Running on the rover' : version.status === 'rejected' ? '❌ Not adopted' : version.status === 'retired' ? '⏸ Replaced' : version.status;
  detail.innerHTML = `<div class="version-detail-header"><div><h2>Version ${safe(version.version)} · ${safe(label)}</h2>
      <p>${version.parent_version == null ? 'Written by humans: the starting point.' : `Written by the Engineer from v${safe(version.parent_version)}.`}</p></div></div>
    <div class="version-sections simple">
      ${changed ? `<section class="detail-section"><h3>What the Engineer changed</h3><ul class="al-list">${changed}</ul></section>` : ''}
      <section class="detail-section"><h3>Skills</h3><p>${skills || '—'}</p></section>
      <section class="detail-section"><h3>Safety checks</h3><ul class="al-list">${guards || '<li>None</li>'}</ul></section>
      <section class="detail-section"><h3>Rules</h3><ul class="al-list">${rules || '<li>None</li>'}</ul></section>
    </div>`;
}

function renderHarness() {
  const versions = [...(state.harness?.versions || [])].sort((a, b) => Number(a.version) - Number(b.version));
  const track = byId('lineage-track');
  if (!versions.length) { track.innerHTML = '<div class="loading-card">No versions yet.</div>'; return; }
  if (state.selectedVersion == null || !versions.some((item) => Number(item.version) === state.selectedVersion)) {
    state.selectedVersion = Number((versions.find((v) => v.status === 'active') || versions.at(-1)).version);
  }
  const icon = { active: '🟢', rejected: '❌', retired: '⏸' };
  track.innerHTML = versions.map((version) => {
    const selected = Number(version.version) === state.selectedVersion;
    return `<button class="version-node ${selected ? 'selected' : ''}" type="button" data-version="${safe(version.version)}" aria-pressed="${selected}"><span><span class="version-id">v${safe(version.version)}</span><strong>${icon[version.status] || ''} ${safe(version.author === 'human' ? 'Human baseline' : 'Engineer')}</strong></span></button>`;
  }).join('');
  track.querySelectorAll('[data-version]').forEach((button) => button.addEventListener('click', () => selectVersion(button.dataset.version)));
  if (!track.dataset.initialized) { track.dataset.initialized = 'true'; selectVersion(state.selectedVersion); }
}

function connectLiveFeed() {
  if (!('EventSource' in window)) return;
  const feed = new EventSource('/api/live');
  feed.addEventListener('status', (event) => {
    try {
      const status = JSON.parse(event.data);
      if (status.state === 'connected') byId('connection-state').textContent = 'Live updates connected';
      else if (status.state === 'preview') byId('connection-state').textContent = 'Preview mode · no Atlas change stream';
    } catch { /* Ignore malformed status payloads. */ }
  });
  feed.addEventListener('change', async () => {
    byId('connection-state').textContent = 'New Atlas event · refreshing Engineer and Harness views';
    state.harness = null;
    await loadEngineer();
  });
  feed.onerror = () => {
    if (state.source !== 'preview') byId('connection-state').textContent = 'Atlas connected · live stream reconnecting';
  };
}

function bindControls() {
  document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', async () => {
    const viewName = tab.dataset.view;
    document.querySelectorAll('.tab').forEach((item) => {
      const active = item === tab;
      item.classList.toggle('active', active);
      item.setAttribute('aria-selected', String(active));
    });
    document.querySelectorAll('.view').forEach((view) => {
      const active = view.id === `view-${viewName}`;
      view.classList.toggle('active', active);
      view.hidden = !active;
    });
    if (viewName !== 'mission' && !state.harness) await loadEngineer();
    if (viewName === 'harness' && state.harness) renderHarness();
  }));
  byId('sol-slider').addEventListener('input', () => {
    stopPlayback();
    state.index = Number(byId('sol-slider').value);
    renderMission();
  });
  byId('restart').addEventListener('click', () => {
    stopPlayback();
    state.index = 0;
    renderMission();
  });
  byId('play').addEventListener('click', () => state.timer ? stopPlayback() : startPlayback());
  byId('show-truth').addEventListener('change', (event) => {
    state.showTruth = event.target.checked;
    renderMission();
  });
}

bindControls();
connectLiveFeed();
loadMissionList();
