# SpaceExplore: Self-Healing Rover Harness

> "Our rover is 20 light-minutes from the nearest engineer. When its agent fails, nobody can patch it. So the harness patches itself."

Built at the MongoDB Harness Engineering & Model Wrangling Hackathon (NYC, September 26, 2026).

An **Explorer** agent drives a rover across a simulated planet with sand traps, dust storms and wheel wear. When the rover gets into trouble, an **Engineer** agent rewrites the Explorer's harness: its rules, guardrails, tool access and memory policy. A patch is kept only if it scores better on held-out planets. It must also pass an immutable constitution. MongoDB Atlas holds the harness lineage, telemetry, incidents and vector memory, and its change streams trigger each repair.

Everything in this repo was built today at the hackathon; the commit history starts at 1:29 PM on September 26, 2026.

## How MongoDB Atlas is used

Atlas is the rover's memory and the harness's version control. Every arrow in the self-repair loop goes through it:

| Atlas feature | Collection | Role in the loop | Code |
| --- | --- | --- | --- |
| Change streams | `events` | A live major or critical incident wakes the Engineer the moment it is written, with no polling | [db/watch.py](db/watch.py) |
| Atlas Vector Search (`$vectorSearch`), Voyage AI embeddings | `memories` | Each sol the Explorer recalls the lessons and past incidents most relevant to its situation, filtered by kind and planet | [db/memory.py](db/memory.py) |
| Geospatial `$geoNear` on a `2d` index | `map_knowledge` | The Explorer is told about known hazards near the rover | [db/memory.py](db/memory.py) |
| Documents with a unique `version` index | `harness_versions`, `patches` | Every harness version with its parent, the patch ops, the incident that caused it and its held-out scores, including rejected patches | [db/harness.py](db/harness.py) |
| Time-series collection | `telemetry` | Per-sol rover state and the Explorer's reasoning, which the Engineer reads to diagnose incidents and the UI replays | [explorer/mission.py](explorer/mission.py) |

[scripts/setup_db.py](scripts/setup_db.py) creates all of it, and [scripts/check_db.py](scripts/check_db.py) tests it on the live cluster.

## Docs

| Doc | What's in it |
| --- | --- |
| [docs/PROJECT.md](docs/PROJECT.md) | Problem, architecture, timeline, demo script, risks |
| [docs/CONTRACTS.md](docs/CONTRACTS.md) | Every shared interface: sim API, data models, harness config, patch ops, MongoDB collections |
| [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) | Who needs what from whom, the stub to use meanwhile, delivery status, contract change log |
| [docs/workstreams/](docs/workstreams/) | One brief per contributor |

## Team

| Workstream | Owner | Brief |
| --- | --- | --- |
| A · Planet Simulator + Mission Control UI | manikanta | [A-planet-sim.md](docs/workstreams/A-planet-sim.md) |
| B · Explorer Agent + Harness Runtime | Jithendra | [B-explorer-harness.md](docs/workstreams/B-explorer-harness.md) |
| C · Engineer Agent + Evaluation | lambdabypi | [C-engineer-eval.md](docs/workstreams/C-engineer-eval.md) |
| D · MongoDB Atlas + Demo | Kavitha | [D-atlas-demo.md](docs/workstreams/D-atlas-demo.md) |

## Results from today's runs (Atlas sandbox, planet seed 42)

| Harness | Author | Held-out score (seeds 101–103) | Live on seed 42 |
| --- | --- | --- | --- |
| v1 | human baseline | −24.2 | Fell at a crater edge in the dust storm and died on sol 13 (score −45.5) |
| v2 | Engineer | −5.2, **rejected** by constitution rule C3: its rover died on seed 102, where the baseline survived | Sheltered through the storm and survived 30 sols (score +53.0) |
| v3 | Engineer, from v1's crater-edge fall | **+14.0, accepted**, now active | |

The Engineer's v3 patch: enable `probe_terrain`, add an `avoid_terrain` guardrail for `crater_edge`, and add a rule to probe crater edges before crossing them.

## Run the demo

```bash
uv run uvicorn web.server:app --port 8765                                   # Mission Control UI
uv run python -m explorer.run --version 1 --seed 42 --sols 30 --sol-delay 1  # v1 hits the storm
uv run python -m engineer.step --latest                                     # Engineer: diagnose, patch, evaluate
uv run python -m engineer.watch                                             # or: wake on every live incident
uv run python -m explorer.run --version active --seed 42 --sols 30 --sol-delay 1
uv run pytest                                                               # 35 tests
```

## Setup

```bash
uv sync                                    # installs deps into .venv
cp .env.example .env                       # fill in MONGODB_URI (hackathon sandbox), ANTHROPIC_API_KEY, VOYAGE_API_KEY
uv run python -m scripts.setup_db          # collections, indexes, vector index, harness v1; safe to re-run
uv run python -m scripts.setup_db --check  # connection test: reports what exists, changes nothing
uv run python -m scripts.check_db          # tests embeddings, $vectorSearch, $geoNear and the change stream
```

Run modules from the repo root with `uv run python -m <package>.<module>`. Shared types live in `contracts/`.

```bash
uv run python -m explorer.run --version active --seed 42 --sols 30 --sol-delay 1   # one live mission
```

**Fake data for UI work:** `uv run python -m scripts.seed_fake` fills a separate database, `rover_fake`, with two missions and a v1 to v4 lineage (every doc has `fake: true`). Run the UI with `MONGODB_DB=rover_fake` to use it. `--clean` drops it. The real `rover` database never holds fake data.
