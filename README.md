# SpaceExplore: Self-Healing Rover Harness

> "Our rover is 20 light-minutes from the nearest engineer. When its agent fails, nobody can patch it. So the harness patches itself."

Built at the MongoDB Harness Engineering & Model Wrangling Hackathon (NYC, September 26, 2026).

An **Explorer** agent drives a rover across a simulated planet with sand traps, dust storms and wheel wear. When the rover gets into trouble, an **Engineer** agent rewrites the Explorer's harness: its rules, guardrails, tool access and memory policy. A patch is kept only if it scores better on held-out planets. It must also pass an immutable constitution. MongoDB Atlas holds the harness lineage, telemetry, incidents and vector memory, and its change streams trigger each repair.

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
| A · Planet Simulator + Mission Control UI | _TBD_ | [A-planet-sim.md](docs/workstreams/A-planet-sim.md) |
| B · Explorer Agent + Harness Runtime | Jithendra | [B-explorer-harness.md](docs/workstreams/B-explorer-harness.md) |
| C · Engineer Agent + Evaluation | _TBD_ | [C-engineer-eval.md](docs/workstreams/C-engineer-eval.md) |
| D · MongoDB Atlas + Demo | _TBD_ | [D-atlas-demo.md](docs/workstreams/D-atlas-demo.md) |

## Setup

```bash
uv sync                      # installs deps into .venv
cp .env.example .env         # fill in MONGODB_URI (hackathon sandbox), ANTHROPIC_API_KEY, VOYAGE_API_KEY
uv run python -c "from contracts import load_harness_v1; print(load_harness_v1().enabled_tools())"
```

Run modules from the repo root with `uv run python -m <package>.<module>`. Shared types live in `contracts/`.
