# Workstream A: Planet Simulator and Mission Control UI

**Owner:** _(name)_ · **Folders:** `sim/`, `web/`, `fixtures/snapshot.json`

You build two things:
- the world the rover lives in: deterministic, fast, and hard in the right ways, so harness v1 fails in ways the Engineer can learn from
- the window the judges watch through

The sim has no LLM, no database and no network. The UI only reads from Atlas. So you depend on nobody except for data to display, and you can fake that.

## What you provide

| To | What | Contract | By |
| --- | --- | --- | --- |
| B | `World` API: `observe`, `step`, `action_cost`, `metrics`, `snapshot` | CONTRACTS §2 | 3:30 |
| C | Tuned seed lists (train, eval, demo) | CONTRACTS §2.5 | 3:45 |
| All | Mission Control UI reading real data from Atlas | CONTRACTS §5, §6 | 4:15 |

Until `sim/world.py` lands, B runs on `explorer/fake_world.py`, which uses the same API. Match that file's behaviour if anything in CONTRACTS §2 is unclear.

## What you consume, and your stubs

| Need | From | Stub until delivered |
| --- | --- | --- |
| `contracts/models.py`, `db/client.py` | B, D | Delivered |
| Fake UI data (`scripts/seed_fake.py`) | D, by 3:15 | Write the fixture JSON by hand |
| Real mission, telemetry and event data (D12) | B, by 3:30 | D's fake data |
| Real lineage and patches (D14, D17) | C, by 3:45 | D's fake data |

## Build order

1. **Now to 3:05 · World generation.**
   - Build a 40×40 grid, seeded with `random.Random(seed)`.
   - Place clusters of `sand` with a hidden `slip` of 0.2–0.9, `rocks` fields, and `crater_edge` rings.
   - Add 8–12 science deposits worth 5–20 points each.
   - Implement `snapshot()` and write `fixtures/snapshot.json` for seed 42.
2. **3:05–3:30 · Actions, energy, weather, events, metrics.**
   - Implement the six tools and costs in CONTRACTS §2.2, plus `action_cost()`.
   - Implement stuck mechanics, wheel wear, crater falls, and the battery and solar model.
   - Build a storm schedule per seed; `tau` rises for 2 sols before the peak.
   - Emit the events in CONTRACTS §2.3 and implement `metrics()`.
   - Push. **This delivers D06.**
3. **3:30–4:15 · Mission Control UI.** Do not use Streamlit: the rules ban it. Use FastAPI plus static HTML and JS on a `<canvas>`.
   - `web/server.py`, read-only:
     - `GET /api/missions` and `/api/missions/{id}`, including the world snapshot
     - `GET /api/missions/{id}/telemetry` and `/api/missions/{id}/events`
     - `GET /api/harness` for the lineage
     - `GET /api/harness/{a}/diff/{b}`
     - `GET /api/patches`
     - `GET /api/live`: server-sent events tailing change streams on `events` and `patches`
   - `web/static/index.html`, `app.js` and `map.js` (`drawPlanet()`, CONTRACTS §6), with three views:
     - **Mission:** the planet canvas with the rover path and fog of war, a sol scrubber for replay, an event feed, and the Explorer's `reasoning` for the selected sol (on each telemetry doc).
     - **Engineer:** a live timeline per patch: incident, diagnosis, patch ops, evaluation scores per seed, then accepted or rejected.
     - **Harness Lab:** the lineage tree v1 to vN with held-out scores. Clicking a version shows its diff (rules, guardrails, tools, context policy), its rationale, and a link to the incident that caused it.
4. **In parallel with step 3 · Seeds.**
   - Make seed 42 dramatic: a sand trap on the direct path to the nearest science, and a storm starting around sol 12.
   - Check that seeds 101–103 each contain sand and a storm, so v1 fails and a patched harness can do better.
   - Push the seed lists. **This delivers D16.**

## Rules that matter

- **Determinism.** Use one `Random(seed)` for world generation and a separate `Random(seed * 1000 + sol)` for runtime randomness. The same seed and the same actions must give identical metrics.
- **Speed.** `step()` must take under 5 ms. The Engineer runs hundreds of sols during evaluation.
- **Hidden values stay hidden.** `observe()` never exposes `slip` or science locations; only `probe_terrain` and `scan` reveal them, with noise. `snapshot()` shows ground truth for the UI only.
- **Agents are the hero, not charts.** Every panel should answer "what did the agent do, and why?" A dashboard-led project is banned by the rules.
- **Real data only in the demo.** Filter out D's fake data (`fake: true`) before recording.

## Done when

- [ ] `python -m sim.demo --seed 42` prints 30 sols with events, and the determinism test passes
- [ ] B's `run_mission` runs on `sim.World`
- [ ] The UI replays a real mission from Atlas
- [ ] The live Engineer timeline updates without a page refresh
- [ ] The Harness Lab shows a real diff between two versions

## Stretch

- A Compare view: v1 and vN replaying the same seed side by side.
- An icy moon preset (`planet="icy"`), demo seed 7.
