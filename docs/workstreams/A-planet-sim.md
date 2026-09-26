# Workstream A: Planet Simulator

**Owner:** _(name)_ · **Folders:** `sim/`, `fixtures/snapshot.json`, `web/static/map.js`

You build the world the rover lives in. It must be deterministic, fast and hard in the right ways. Harness v1 should fail in ways the Engineer can learn from. Your code has no LLM calls, no database and no network, so you depend on nobody.

## What you provide

| To | What | Contract | By |
| --- | --- | --- | --- |
| D | `fixtures/snapshot.json` for seed 42 | CONTRACTS §2.4 | 2:30 |
| B | `World` API: `observe`, `step`, `action_cost`, `metrics`, `snapshot` | CONTRACTS §2 | 3:15 |
| C | Tuned seed lists (train, eval, demo) | CONTRACTS §2.5 | 3:45 |
| D | `web/static/map.js` exporting `drawPlanet()` | CONTRACTS §6 | 3:45 |

## What you consume

Only `contracts/models.py` (D01). Until B pushes it at 2:15, define the pydantic models locally from CONTRACTS §3.1 and switch the import later.

## Build order

1. **2:05–2:30 · World generation.**
   - Build a 40×40 grid, seeded with `random.Random(seed)`.
   - Place clusters of `sand` with a hidden `slip` of 0.2–0.9, `rocks` fields, and `crater_edge` rings.
   - Add 8–12 science deposits worth 5–20 points each.
   - Implement `snapshot()`, write `fixtures/snapshot.json` for seed 42, and push. **This delivers D03.**
2. **2:30–3:00 · Actions and energy.**
   - Implement the six tools and costs in CONTRACTS §2.2, plus `action_cost()`.
   - Implement the stuck mechanics, wheel wear, crater falls, and the battery and solar model.
3. **3:00–3:15 · Weather, events and metrics.**
   - Build a storm schedule per seed: `tau` rises for 2 sols before the peak, so `tau_trend` gives an early warning.
   - Emit the events in CONTRACTS §2.3 and implement `metrics()`.
   - Push. **This delivers D06.**
4. **3:15–3:45 · Tuning and seeds.**
   - Write `sim/policies.py` with three scripted policies:
     - `random`: random moves.
     - `greedy`: heads straight for the nearest science.
     - `careful`: probes sand, shelters in storms.
   - Tune until `greedy` gets stuck or dies on at least 2 of seeds 101–103 within 30 sols, and `careful` survives all three. That gap is what the Engineer must discover.
   - Make seed 42 dramatic: a sand trap on the direct path to the nearest science, and a storm starting around sol 12.
   - Push. **This delivers D16.**
5. **3:45–4:15 · Planet renderer.**
   - Write `web/static/map.js` with `drawPlanet()` (CONTRACTS §6).
   - Draw terrain colours, fog of war for tiles the rover has not seen, science markers, the rover and its path, and event markers.
   - Test it on your own page `web/static/map-dev.html` with the fixture. **This delivers D15.**

## Rules that matter

- **Determinism.**
  - Use one `Random(seed)` for world generation.
  - Use a separate `Random(seed * 1000 + sol)` for runtime randomness, so different actions do not change the world.
  - Add a test: the same seed and the same actions give identical metrics.
- **Speed.** `step()` must take under 5 ms. The Engineer runs hundreds of sols during evaluation.
- **Hidden values stay hidden.** `observe()` never exposes `slip` or science locations; only `probe_terrain` and `scan` reveal them, with noise. `snapshot()` shows ground truth for the UI only.
- **Scan noise.** About 30% of anomaly signals are false positives. This tests whether the agent can tell a real discovery from noise.

## Done when

- [ ] `python -m sim.demo --seed 42 --policy greedy` prints 30 sols with events
- [ ] The determinism test passes
- [ ] `greedy` fails on at least 2 of 101–103 and `careful` survives all 3
- [ ] `map.js` renders the fixture on the dev page

## Stretch

- An icy moon preset (`planet="icy"`) with `ice` crevasses, less sunlight and colder nights, on demo seed 7.
