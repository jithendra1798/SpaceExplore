# Workstream D: MongoDB Atlas, Mission Control UI and Demo

**Owner:** _(name)_ · **Folders:** `db/`, `web/` (except `web/static/map.js`), `scripts/`, `README.md`, `pyproject.toml`, `.env.example`

You own the platform everyone writes to, and the window the judges watch through. MongoDB Atlas must be a core part of the project, not decoration, and the UI must show the agents at work. A dashboard-led project is banned, as is Streamlit. You also own the submission.

## What you provide

| To | What | Contract | By |
| --- | --- | --- | --- |
| B, C | `pyproject.toml`, `.env.example`, `db/client.py` | CONTRACTS §1, §5.1 | 2:20 |
| C | Harness v1 seeded in `harness_versions` | CONTRACTS §3.2 | 2:30 |
| B | `db/memory.py`: `embed`, `add_memory`, `search_memories`, `hazards_near` | CONTRACTS §5.1 | 3:15 |
| C | `db/watch.py`: `watch_incidents()` | CONTRACTS §5.1 | 3:15 |
| All | UI, 1-minute video, README, submission | | 4:40 |

## What you consume, and your stubs

| Need | From | Stub until delivered |
| --- | --- | --- |
| Snapshot fixture (D03) | A, by 2:30 | A random grid generated in JS |
| Mission data (D12) | B, by 3:30 | `scripts/seed_fake.py` inserts a fake 30-sol mission |
| Lineage and patch data (D14, D17) | C, by 3:45 | Fake v1 to v4 lineage in `scripts/seed_fake.py` |
| `map.js` renderer (D15) | A, by 3:45 | Coloured squares per tile |

## Build order

1. **2:05–2:20 · Atlas and project setup.**
   - Confirm all four teammates can reach the **hackathon Atlas sandbox cluster**; finalists are only eligible if they built on it.
   - Create a database user and network access, and share the URI privately, never in git.
   - Push `pyproject.toml` (uv; deps: pymongo, pydantic, anthropic, voyageai, fastapi, uvicorn), `.env.example` and `db/client.py`. **This delivers D02.**
2. **2:20–2:40 · Collections and indexes.** Write `scripts/setup_db.py` to create everything in CONTRACTS §5:
   - the `telemetry` time-series collection
   - the unique and `2d` indexes
   - the `memories_vec` Atlas Vector Search index (`create_search_index` with type `vectorSearch`, filter fields `kind` and `planet`)
   - the harness v1 seed. **This delivers D04.**

   Also write `scripts/seed_fake.py`.
3. **2:40–3:10 · Helpers.**
   - `db/memory.py` embeds with Voyage AI. Record the model and dimension in the change log in DEPENDENCIES.md; they must match the index. It also runs `$vectorSearch` and `$geoNear`.
   - `db/watch.py` is a change stream on `events`, filtered to inserts where `mode` is `live` and `severity` is major or critical.
   - `db/harness.py` holds the harness getters.
   - Push. **This delivers D07, D08 and D09.**
4. **3:10–4:15 · Mission Control UI.**
   - `web/server.py` (FastAPI):
     - `GET /api/missions` and `/api/missions/{id}`, including the world snapshot
     - `GET /api/missions/{id}/telemetry` and `/api/missions/{id}/events`
     - `GET /api/harness` for the lineage
     - `GET /api/harness/{a}/diff/{b}`
     - `GET /api/patches`
     - `GET /api/live`: server-sent events tailing change streams on `events` and `patches`
   - Static `web/static/index.html` and `app.js`, with three views:
     - **Mission:** A's planet canvas with the rover path, a sol scrubber for replay, an event feed, and the Explorer's reasoning for the selected sol.
     - **Engineer:** a live timeline per patch: incident, diagnosis, patch ops, evaluation scores per seed, then accepted or rejected.
     - **Harness Lab:** the lineage tree v1 to vN with held-out scores. Clicking a version shows its diff (rules, guardrails, tools, context policy), its rationale, and a link to the incident that caused it.
5. **4:15–4:40 · Ship it.**
   - Record the 1-minute video; the script is in [PROJECT.md](../PROJECT.md#demo-script).
   - Finish the README: setup, architecture, and **what was built today**.
   - Check that the repo is public.
   - Submit with all four members added.

## Rules that matter

- **Show MongoDB doing real work.** Change streams wake the Engineer. Vector search feeds the Explorer's memory. `$geoNear` feeds hazard awareness. Documents store the harness lineage. Have Atlas open in a browser tab during judging.
- **Agents are the hero, not charts.** Every panel should answer "what did the agent do, and why?"
- **Real data only in the demo.** Delete the fake-seed data before recording, or filter it out with a `fake: true` flag.

## Done when

- [ ] `scripts/setup_db.py` runs cleanly on the sandbox cluster
- [ ] B and C are using the helpers
- [ ] The UI replays a real mission from Atlas
- [ ] The live Engineer timeline updates without a page refresh
- [ ] The Harness Lab shows a real diff between two versions
- [ ] The video is uploaded, the repo is public, and the submission is sent by 4:40

## Stretch

- A Compare view: v1 and vN replaying the same seed side by side.
