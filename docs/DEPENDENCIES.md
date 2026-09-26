# Dependency Log

This file lists every point where one workstream needs something from another. Each row names the stub the consumer uses until the real piece lands, so nobody is ever blocked.

**When you deliver:** change the row's Status to `Delivered`, add the commit hash, and post in team chat.
**When you are late:** change Status to `Late`, write a new ETA in Notes, and the consumer keeps using the stub.

Workstreams: **A** Planet Sim + UI · **B** Explorer + Harness Runtime · **C** Engineer + Eval · **D** Atlas + Demo. Section numbers such as §2 refer to [CONTRACTS.md](CONTRACTS.md).

## Dependencies

| ID | Consumer | Needs | Provider | Contract | Needed by | Stub until delivered | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| D01 | B, C, D | `contracts/models.py` | B | §3.1 | 2:15 | Copy the shapes from CONTRACTS.md | Delivered | 2:40 PM. Also includes `HarnessConfig`, `HarnessPatch`, `EventDoc` and `load_harness_v1()` |
| D02 | B, C | `pyproject.toml`, `.env.example`, `db/client.py` with `get_db()` | D | §1, §5.1 | 2:20 | Plain `pymongo.MongoClient(os.environ["MONGODB_URI"])` | Delivered | 2:40 PM, pushed by B to unblock the team; D owns these files from now on |
| D03 | A | Fake UI data: `scripts/seed_fake.py` (mission, telemetry, events, lineage, patches; all `fake: true`) | D | §5 | 3:15 | Hand-written fixture JSON | Delivered | 3:36 PM, 4ab52b3. In its own database `rover_fake` (see change log): 2 missions, v1 to v4 lineage, 5 patches. `--clean` drops it. Replaces the snapshot fixture, which is now internal to A |
| D04 | C | Harness v1 seeded in Atlas | D | §3.2 | 2:30 | Load the JSON in CONTRACTS §3.2 from a file | Delivered | 3:36 PM, 4ab52b3. `scripts/setup_db.py` ran on the sandbox: all collections and indexes, `memories_vec` ready, v1 active. Safe to re-run |
| D05 | C | `contracts/scoring.py` and `contracts/constitution.py` | C | §3.5, §3.6 | 2:20 | n/a (C owns both) | Delivered | 2:40 PM, pushed by B. Includes `check_patch()` (C2) and `accept()` (C3); C owns these files from now on |
| D06 | B | `World` API: `observe`, `step`, `action_cost`, `metrics`, `snapshot` | A | §2 | 3:30 | `explorer/fake_world.py` (pushed; same API, 12×12 grid, sand band, storm on sols 5–8) | Open | B switches automatically once `sim/world.py` exists |
| D07 | B | Memory helpers: `add_memory`, `search_memories`, `embed` | D | §5.1 | 3:15 | Insert without embedding; retrieve newest K by `kind` | Delivered | 3:36 PM, 4ab52b3. `db/memory.py`, Voyage `voyage-3.5-lite`, 1024 dims (change log). Needs `VOYAGE_API_KEY`; without it memories are stored unembedded and B falls back to recency |
| D08 | B | `hazards_near()` using `$geoNear` | D | §5.1 | 3:15 | Filter an in-memory list of known hazards | Delivered | 3:36 PM, 4ab52b3. `db/memory.py`; tested on the cluster by `scripts/check_db.py` |
| D09 | C | Change-stream helper `watch_incidents()` | D | §5.1 | 3:15 | Poll `events` every 5 s for new major or critical live events | Delivered | 3:36 PM, 4ab52b3. `db/watch.py`; tested on the cluster by `scripts/check_db.py` |
| D10 | C | `run_mission()` in both eval and live modes | B | §4 | 3:30 | `engineer/fake_runner.py`: heuristic score from config (see C's workstream doc) | Delivered | 2:55 PM in `explorer/mission.py`, tested with 3 parallel eval runs on the fake world. Extra kwargs: `log_to_db`, `planner`, `fake_world`, `verbose` |
| D11 | C | Real incident events in Atlas | B | §5, §5.2 | 3:30 | Insert the example event from CONTRACTS §5.2 by hand | Delivered | 3:13 PM: `run_mission` writes `events` (sim events plus `GUARDRAIL_BLOCK`); verified on a local Mongo. Appears in Atlas once `.env` has the sandbox URI |
| D12 | A | Real `missions`, `telemetry`, `events`, `map_knowledge` data | B | §5 | 3:30 | D's `scripts/seed_fake.py` | Delivered | 3:13 PM: code writes all four; verified on a local Mongo. Filter `world_source: "sim"` for the demo |
| D13 | B | Active harness written by the Engineer | C | §3.2 | 3:45 | `db.harness.get_active_harness()` returns v1 | Open | B always reads the active version at mission start |
| D14 | A | Real `harness_versions` and `patches` lineage | C | §3.2, §3.4 | 3:45 | Fake lineage v1 to v4 in D's `scripts/seed_fake.py` | Open | |
| D15 | — | Planet renderer `web/static/map.js` | A | §6 | — | — | n/a | UI moved to A at 2:55 PM, so this is no longer a cross-team dependency |
| D16 | C | Tuned seeds: v1 fails on at least 2 of the 3 eval seeds; demo seed 42 has a sand trap and a storm | A | §2.5 | 3:45 | Any seeds | Open | |
| D17 | A, D | Pre-run evolution lineage (5+ versions) for the demo | C | §3.2 | 4:15 | Whatever lineage exists at 4:15 | Open | Real data only in the demo |

## Integration checkpoints

| Time | Check | Pass means |
| --- | --- | --- |
| 3:15 | Each module runs alone on stubs | A: `python -m sim.demo --seed 42` prints 30 sols. B: explorer runs 10 sols on `FakeWorld` with a real LLM. C: fake-runner loop produces an accepted patch. D: `setup_db.py` and `seed_fake.py` ran on the sandbox cluster |
| 3:45 | One full loop on real parts | A live mission on seed 42 logs an incident. The Engineer proposes a patch, evaluates it on 101–103, and writes a new version. A's UI shows it |
| 4:15 | Feature freeze | Only bug fixes after this |

If a piece is not ready at a checkpoint, cut it from the demo. Never present a stub as the real thing: the judges disqualify teams that cannot show clearly what they built.

## Contract change log

Add a row **before** you push any change to [CONTRACTS.md](CONTRACTS.md) or `contracts/`. After 2:15, changes may only add things.

| Time | Who | Change | Affects |
| --- | --- | --- | --- |
| 1:50 PM | Planning | Initial contracts | All |
| 2:40 PM | B | `DIRECTIONS` in `contracts/models.py`: north is y − 1, so N = (0, −1) and E = (1, 0) | A, B, D |
| 2:40 PM | B | `HarnessPatch` gains optional `diagnosis` and `reason` (why a patch was invalid or rejected) | C, D |
| 2:40 PM | B | `EventDoc` model: a `SimEvent` plus `mission_id`, `mode`, `harness_version`, `ts` | B, C, D |
| 2:40 PM | B | `fixtures/incident.json` added from CONTRACTS §5.2 | C |
| 2:55 PM | All | UI moved from D to A: `web/` is A's; D keeps Atlas, helpers, fake data and the submission | A, D |
| 2:55 PM | B | `mission_id` is now `m_<unixtime>_<seed>_<mode>_v<version>_<4 hex>`, so parallel baseline and candidate runs never collide | A, C |
| 2:55 PM | B | `run_mission` gains optional `log_to_db`, `planner`, `fake_world`, `verbose` | C |
| 2:55 PM | B | `avoid_terrain` checks slip probed on any earlier sol (not only this sol): the rover probes one sol and crosses the next | C |
| 2:55 PM | B | Telemetry docs also carry `reasoning`, `planned`, `stuck` and `events`; `blocked` holds `{guardrail_id, action, reason, rewritten_to}` | A |
| 3:13 PM | B | `missions` docs gain `world_source`: `"sim"` or `"fake"`. Use it to filter out runs on B's fake world | A, C |
| 3:13 PM | B | B creates `telemetry` as a time-series collection if it is missing, so `scripts/setup_db.py` must skip it when it already exists | D |
| 3:13 PM | B | `pyproject.toml`: pytest config added; run `uv run pytest` | All |
| 3:25 PM | D | `map_knowledge` index `{mission_id: 1, loc: 1}` is **not unique**: `loc` is an array, so a unique index compares x and y separately and rejects distinct tiles that share a coordinate, e.g. (3,5) and (5,7). Verified on the cluster by `scripts/check_db.py` | B |
| 3:25 PM | D | Embeddings: Voyage `voyage-3.5-lite`, 1024 dims, cosine. `memories_vec` filter fields `kind` and `planet` | B, C |
| 3:25 PM | D | `add_memory` stores a missing `planet` as `"any"`; `search_memories(planet=p)` matches `p` or `"any"`. C: write lessons with `db.memory.add_memory("lesson", text, harness_version=...)` so they are embedded and apply on every planet | B, C |
| 3:25 PM | D | Fake UI data lives in its own database, `rover_fake` (docs still carry `fake: true`), so it cannot collide with real harness versions, wake the Engineer or leak into memory. A: run the UI with `MONGODB_DB=rover_fake` until real data lands | A |
| 3:25 PM | D | `watch_incidents(callback, db=None)` gains an optional `db` | C |
| 3:40 PM | B | `missions.status` can now be `"failed"`, with an `error` string, when a run crashes. Missions stuck at `running` from before 3:40 were crashes; ignore them | A, C |
| 4:10 PM | B | Integration of D07 and D08 verified on the sandbox: live mission via `db.memory` (`$geoNear` hazards nearest-first). `$vectorSearch` needs `VOYAGE_API_KEY` in each runner's `.env`; without it B falls back to recency | B, D |
