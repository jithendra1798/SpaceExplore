# Workstream B: Explorer Agent and Harness Runtime

**Owner:** _(name)_ · **Folders:** `explorer/`, `contracts/models.py`

You build the rover's brain and the runtime that turns a harness config into behaviour. Everything the Engineer changes flows through your code: the rules and prompt, the context policy, the enabled tools and the guardrails. If a field in the harness config does nothing at runtime, the Engineer cannot improve anything.

## What you provide

| To | What | Contract | By |
| --- | --- | --- | --- |
| A, C, D | `contracts/models.py` | CONTRACTS §3.1 | 2:15 |
| C | `run_mission()`, thread-safe, in `live` and `eval` modes | CONTRACTS §4 | 3:30 |
| C | Real incident events in Atlas | CONTRACTS §5 | 3:30 |
| D | Real `missions`, `telemetry`, `events`, `map_knowledge` docs | CONTRACTS §5 | 3:30 |

## What you consume, and your stubs

| Need | From | Stub until delivered |
| --- | --- | --- |
| `World` API (D06) | A, by 3:15 | Write `explorer/fake_world.py`: a 10×10 open grid, one sand tile, a storm on sol 5 |
| `get_db()` (D02) | D, by 2:20 | `pymongo.MongoClient(os.environ["MONGODB_URI"])` |
| Memory helpers (D07) and `hazards_near` (D08) | D, by 3:15 | Newest K memories by `kind`; an in-memory hazard list |
| Active harness from the Engineer (D13) | C, by 3:45 | Load the v1 JSON from CONTRACTS §3.2 |

## Build order

1. **2:05–2:15 · Contracts.** Type up CONTRACTS §3.1 into `contracts/models.py`, add a `HarnessConfig` model for §3.2, and push. **This delivers D01.**
2. **2:15–2:45 · One sol, end to end, on `FakeWorld`.**
   - `explorer/context.py` builds the prompt from:
     - the harness system prompt
     - the numbered rules
     - the active guardrails, in plain words
     - the observation
     - the last `recent_sols` sols
     - retrieved memories
     - nearby hazards
   - `explorer/agent.py` makes one LLM call per sol with a single tool, `submit_plan(reasoning, actions)`. Its `tool` enum lists **only the enabled tools** from the harness. A disabled tool is simply absent, which is how "tool access" evolves. Use temperature 0.
3. **2:45–3:15 · Guardrails and constitution.** `explorer/guardrails.py`:
   - Implement every type in the catalog (CONTRACTS §3.3) plus constitution rule C1 (§3.6).
   - Input: the planned actions. Output: the allowed actions plus the blocks.
   - Each block becomes a `GUARDRAIL_BLOCK` event.
   - `avoid_terrain` needs to know whether this sol's plan probed the tile first, so track probe results within the sol.
4. **3:15–3:30 · `run_mission()` and logging.**
   - Swap `FakeWorld` for `sim.World`.
   - Write to Atlas per the mode rules in CONTRACTS §4.
   - Store the world snapshot on the `missions` doc for live runs.
   - Push. **This delivers D10, D11 and D12.**
5. **3:30–3:45 · Memory.**
   - After each major or critical event, write an `incident` memory in plain words, for example: "Sol 8: moved E 4 steps onto sand without probing; stuck 4 sols; lost 18% battery."
   - Write `discovery` memories on DISCOVERY events.
   - When `summarize_every > 0`, write a `summary` memory every N sols. Build it from a template; only use an LLM if time allows.
   - Retrieval follows `context_policy`.
6. **CLI.** `python -m explorer.run --version active --seed 42 --sols 30 --sol-delay 1.0`

## Rules that matter

- **One LLM call per sol, not per move.** 30 sols is 30 calls. The Engineer's evaluation runs 6 missions per patch, so latency adds up fast.
- **Thread safety.** C calls `run_mission` from 3 threads at once. Avoid global state; create clients per call or use thread-safe ones. pymongo's `MongoClient` is thread-safe.
- **Guardrails live in code, not only in the prompt.** The prompt tells the model about the guardrails so it does not keep hitting them, but the runtime enforces them regardless.
- **Log the reasoning.** Put the model's `reasoning` string on each telemetry doc. The Engineer reads it to diagnose incidents, and the UI shows it.

## Done when

- [ ] `python -m explorer.run --version 1 --seed 42 --sols 30` runs against the real sim and Atlas
- [ ] Disabling a tool in the config removes it from the model's options
- [ ] A guardrail block shows up as an event
- [ ] 3 parallel `run_mission(mode="eval")` calls finish without errors
- [ ] With `memory_k > 0`, retrieved memories appear in the prompt, and you can show one in the demo

## Stretch

- An LLM-written mission summary every N sols: long-horizon compression of memory.
