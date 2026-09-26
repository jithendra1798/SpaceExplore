# Workstream D: MongoDB Atlas Platform and Demo

**Owner:** _(name)_ · **Folders:** `db/`, `scripts/`, `README.md`, `pyproject.toml`, `.env.example`

You own the platform everyone writes to, and you own the submission. MongoDB Atlas must be a core part of the project, not decoration, and you make sure the judges can see that. The UI moved to workstream A.

## What you provide

| To | What | Contract | By |
| --- | --- | --- | --- |
| B, C | `pyproject.toml`, `.env.example`, `db/client.py` | CONTRACTS §1, §5.1 | Delivered (pushed by B) |
| C | Harness v1 seeded in `harness_versions` | CONTRACTS §3.2 | 3:05 |
| A | Fake UI data: `scripts/seed_fake.py` | CONTRACTS §5 | 3:15 |
| B | `db/memory.py`: `embed`, `add_memory`, `search_memories`, `hazards_near` | CONTRACTS §5.1 | 3:15 |
| C | `db/watch.py`: `watch_incidents()` | CONTRACTS §5.1 | 3:15 |
| All | 1-minute video, README, submission | | 4:40 |

## What you consume

Nothing is blocking. B's `explorer/memory.py` already calls `db.memory` when it exists and falls back to plain queries until then, so ship the helpers as soon as they work.

## Build order

1. **Now to 3:05 · Atlas access and collections.**
   - Confirm all four teammates can reach the **hackathon Atlas sandbox cluster**; finalists are only eligible if they built on it.
   - Share the URI privately, never in git.
   - Write `scripts/setup_db.py` to create everything in CONTRACTS §5:
     - the `telemetry` time-series collection
     - the unique and `2d` indexes
     - the `memories_vec` Atlas Vector Search index (`create_search_index` with type `vectorSearch`, filter fields `kind` and `planet`)
     - the harness v1 seed from `contracts/harness_v1.json`. **This delivers D04.**
2. **3:05–3:15 · Fake data.** Write `scripts/seed_fake.py`: one fake 30-sol mission plus a fake v1 to v4 lineage with patches. Mark every doc `fake: true` so A can filter them out.
3. **3:15–3:40 · Helpers.**
   - `db/memory.py` embeds with Voyage AI. Record the model and dimension in the change log in DEPENDENCIES.md; they must match the index. It also runs `$vectorSearch` and `$geoNear`.
   - `db/watch.py` is a change stream on `events`, filtered to inserts where `mode` is `live` and `severity` is major or critical.
   - `db/harness.py` holds the harness getters.
   - Match these signatures, which B's code calls:
     - `search_memories(query, kinds, k, planet) -> list[dict]`
     - `add_memory(kind, text, **fields)`
     - `hazards_near(mission_id, pos, radius) -> list[dict]`, where each result has `loc`, `terrain` and `slip_probed`
   - Push. **This delivers D07, D08 and D09.**
4. **3:40–4:15 · Demo prep.** Write the README (setup, architecture, and **what was built today**). Rehearse the demo script in [PROJECT.md](../PROJECT.md#demo-script). Get Atlas ready to show: collections, the vector index and change streams.
5. **4:15–4:40 · Ship it.**
   - Record the 1-minute video.
   - Delete the fake data.
   - Check that the repo is public.
   - Submit with all four members added.

## Rules that matter

- **Show MongoDB doing real work.** Change streams wake the Engineer. Vector search feeds the Explorer's memory. `$geoNear` feeds hazard awareness. Documents store the harness lineage. Have Atlas open in a browser tab during judging.
- **Real data only in the demo.** Judges disqualify work that is not clearly built today.

## Done when

- [ ] `scripts/setup_db.py` runs cleanly on the sandbox cluster
- [ ] B's missions retrieve memories through `$vectorSearch`
- [ ] C's watcher wakes on a live incident
- [ ] The video is uploaded, the repo is public, and the submission is sent by 4:40
