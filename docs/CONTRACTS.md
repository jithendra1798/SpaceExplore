# Shared Contracts

These are the interfaces between the four workstreams. If you code against the shapes in this file, you never have to wait for anyone. Until the real piece arrives, use the stub listed in [DEPENDENCIES.md](DEPENDENCIES.md).

**Change rule:** after 2:15 PM, changes may only add things, such as new optional fields. Never rename or remove a field. Every change goes into the change log in [DEPENDENCIES.md](DEPENDENCIES.md), and you post it in team chat before pushing.

---

## 1. Repo layout and ownership

```
SpaceExplore/
├── README.md               D
├── docs/                   everyone (edit your own workstream file)
├── contracts/              shared; changes are logged in DEPENDENCIES.md
│   ├── models.py           B writes at kickoff from section 3
│   ├── scoring.py          C writes at kickoff from section 3.5
│   ├── constitution.py     C writes at kickoff from section 3.6
│   └── harness_v1.json     copy of section 3.2
├── sim/                    A
├── explorer/               B
├── engineer/               C
├── db/                     D (Python helpers for Atlas)
├── web/                    A (server + UI)
├── scripts/                D (setup, seeding, demo helpers)
├── fixtures/               sample JSON anyone can use as a stub
├── pyproject.toml          D (uv)
└── .env.example            D
```

- Only edit files in folders you own. To change someone else's code, ask them.
- Everything is Python 3.11+ with pydantic v2, pymongo and the anthropic SDK. The UI is FastAPI plus static HTML/JS on a canvas. **Do not use Streamlit**: the hackathon rules ban it.
- Environment variables (never commit `.env`):

| Variable | Example | Used by |
| --- | --- | --- |
| `MONGODB_URI` | `mongodb+srv://...` from the hackathon Atlas sandbox | B, C, D |
| `MONGODB_DB` | `rover` | B, C, D |
| `ANTHROPIC_API_KEY` | | B, C |
| `EXPLORER_MODEL` | `claude-haiku-4-5-20251001` | B |
| `ENGINEER_MODEL` | `claude-sonnet-5` | C |
| `VOYAGE_API_KEY` | embeddings for vector search | D, B |

---

## 2. Planet simulator API (A provides, B consumes)

Module `sim/world.py`. It is pure Python: no LLM calls, no database, no network. The same seed with the same actions must always produce the same outcome.

```python
class World:
    def __init__(self, seed: int, planet: str = "mars", size: int = 40): ...
    sol: int                                   # current sol, starts at 0
    alive: bool
    def observe(self) -> Observation: ...      # what the rover can sense now
    def action_cost(self, action: Action) -> float: ...   # predicted battery % cost
    def step(self, actions: list[Action]) -> StepResult: ...  # runs ONE sol
    def metrics(self) -> Metrics: ...
    def snapshot(self) -> dict: ...            # full ground truth for the UI (2.4)
```

### 2.1 Terrain types

`bedrock`, `regolith`, `sand`, `rocks`, `crater_edge` (plus `ice` for the stretch-goal icy moon).

| Terrain | Hidden property | Hazard |
| --- | --- | --- |
| `sand` | `slip` 0.2–0.9 | Entering it: chance of getting stuck = slip |
| `rocks` | none | Each step: `wheel_health` −0.04 |
| `crater_edge` | none | Entering with `steps > 1`: FALL, wheel −0.4, battery −20% |

### 2.2 Tools (actions)

The rover submits up to `params.max_actions_per_sol` actions per sol, and they run in order.

| Tool | Args | Effect | Cost (battery %) |
| --- | --- | --- | --- |
| `move` | `direction`: N, NE, E, SE, S, SW, W, NW; `steps`: 1–5 | Moves step by step; stops early on a hazard | 2 per step (×2 if wheel < 0.4) |
| `probe_terrain` | `direction` | Returns slip of the adjacent tile ± 0.05 | 1 |
| `scan` | none | Returns anomaly signals within radius 5; some are false positives | 3 |
| `drill` | none | Collects science on the current tile, once | 6 |
| `shelter` | none | Low-power mode for the rest of the sol; night cost 4% instead of 12% | 0 |
| `wait` | none | Does nothing | 0 |

Energy per sol: solar gain = `30 × exp(−tau / 1.5) × (1 − panel_dust)`, minus the night heater (12%, or 4% in shelter). Battery at 0 means DEATH. A sets the exact constants, and these are starting values.

When stuck, every `move` fails, costs 3%, and has a 25% chance of freeing the rover.

### 2.3 Observation, Action, StepResult, Event, Metrics

See section 3 for the pydantic models. Event types emitted by the sim:

| Type | Severity | When |
| --- | --- | --- |
| `STUCK` | major | Rover gets stuck in sand |
| `FREED` | info | Rover gets free |
| `FALL` | critical | Entered a crater edge too fast |
| `WHEEL_DAMAGE` | minor (major if wheel < 0.4) | Drove over rocks |
| `BATTERY_LOW` | minor | Battery < 25% |
| `BATTERY_CRITICAL` | major | Battery < 10% |
| `STORM_ONSET` / `STORM_END` | info | tau crosses 2.0 |
| `DISCOVERY` | info | Drill collected science |
| `DEATH` | critical | Battery hit 0, or wheels at 0 while stuck |

B adds one more type: `GUARDRAIL_BLOCK` (minor), raised when a guardrail blocks or rewrites an action.

### 2.4 World snapshot (for the UI)

A pushes a sample to `fixtures/snapshot.json` by 2:30.

```json
{
  "seed": 42, "planet": "mars", "size": 40,
  "start": [4, 20],
  "terrain": [["bedrock", "sand", "..."], ["..."]],
  "slip": {"12,20": 0.8},
  "science": [{"pos": [18, 22], "value": 15}],
  "storms": [{"start_sol": 12, "peak_tau": 4.0, "length": 5}]
}
```

`terrain[y][x]` is row-major.

### 2.5 Seeds

| Set | Seeds | Use |
| --- | --- | --- |
| Train | 1–20 | Engineer runs the active harness here to find incidents |
| Held-out eval | 101, 102, 103 | Engineer compares baseline vs candidate; never used for training |
| Demo | 42 | Live demo. A guarantees a sand trap on the route to the nearest science and a storm starting around sol 12 |
| Icy moon (stretch) | 7 | Generalization demo on `planet="icy"` |

---

## 3. Data models (`contracts/models.py`)

B types these up at kickoff and pushes by 2:15. Until then, code against these shapes.

### 3.1 Core models

```python
from typing import Literal
from pydantic import BaseModel, Field

Tool = Literal["move", "probe_terrain", "scan", "drill", "shelter", "wait"]
Severity = Literal["info", "minor", "major", "critical"]

class TileView(BaseModel):
    x: int
    y: int
    terrain: str

class Observation(BaseModel):
    sol: int
    pos: tuple[int, int]
    battery: float          # 0-100 (%)
    wheel_health: float     # 0-1
    panel_dust: float       # 0-1
    stuck: bool
    tau: float              # dust opacity: ~0.5 clear, >2 storm
    tau_trend: float        # tau change vs previous sol (storm warning)
    local_tiles: list[TileView]   # radius 3; slip and science are hidden
    signals: list[str] = []       # e.g. "anomaly at (18,22) strength 0.7"

class Action(BaseModel):
    tool: Tool
    args: dict = {}

class ActionOutcome(BaseModel):
    action: Action
    ok: bool
    message: str
    data: dict = {}         # e.g. {"slip": 0.78} from probe_terrain

class SimEvent(BaseModel):
    type: str
    severity: Severity
    sol: int
    pos: tuple[int, int]
    details: dict = {}

class StepResult(BaseModel):
    sol: int
    executed: list[ActionOutcome]
    events: list[SimEvent]
    observation: Observation   # state after the sol ends
    alive: bool
    science_gained: float

class Metrics(BaseModel):
    sols_survived: int
    alive: bool
    science: float
    distance: int
    stuck_sols: int
    incidents: int          # count of major + critical events
    min_battery: float

class MissionResult(BaseModel):
    mission_id: str
    harness_version: int
    seed: int
    planet: str
    mode: Literal["live", "eval"]
    metrics: Metrics
    score: float
    incident_event_ids: list[str] = []
```

### 3.2 Harness config (the thing that evolves)

Stored in the `harness_versions` collection. Version 1 is **deliberately weak** so the Engineer has real fixes to find: `shelter` and `probe_terrain` are off, and memory is off.

```json
{
  "version": 1,
  "parent_version": null,
  "status": "active",
  "author": "human",
  "created_at": "2026-09-26T18:00:00Z",
  "rationale": "Baseline harness",
  "source_incidents": [],
  "system_prompt": "You are the autonomy software of a planetary rover. Earth is 20 light-minutes away and cannot help you. Maximise science collected while keeping the rover alive. Each sol, return a plan of up to 5 actions.",
  "rules": [
    {"id": "r001", "text": "Head toward the strongest anomaly signal and drill it.", "added_in": 1}
  ],
  "guardrails": [
    {"id": "g001", "type": "min_battery_for_move", "params": {"threshold": 15}, "added_in": 1}
  ],
  "tools": {"move": true, "scan": true, "drill": true, "wait": true, "shelter": false, "probe_terrain": false},
  "context_policy": {
    "recent_sols": 3,
    "memory_k": 0,
    "memory_kinds": ["lesson"],
    "hazards_within": 0,
    "summarize_every": 0
  },
  "params": {"max_actions_per_sol": 5, "max_steps_per_move": 5},
  "eval": null
}
```

`status` is one of `candidate`, `active`, `retired` or `rejected`. Exactly one version is `active` at a time.

`eval` is filled in by C: `{"seeds": [101,102,103], "score": 48.2, "per_seed": [{"seed": 101, "score": 50.1, "metrics": {...}}]}`.

**Context policy fields (B implements):**

| Field | Meaning |
| --- | --- |
| `recent_sols` | Include the last N sols' actions and outcomes in the prompt |
| `memory_k` | Retrieve K memories by vector search (0 = off) |
| `memory_kinds` | Which memory kinds to retrieve: `incident`, `discovery`, `lesson`, `summary` |
| `hazards_within` | Include known hazards within R tiles, from `map_knowledge` via `$geoNear` (0 = off) |
| `summarize_every` | Every N sols, write a `summary` memory of the mission so far (0 = off) |

### 3.3 Guardrail catalog (B enforces, C composes)

Guardrails are typed and enforced in code, not only in the prompt. The Engineer can add, tune or remove them, but only from this catalog.

| Type | Params | Effect |
| --- | --- | --- |
| `min_battery_for_move` | `threshold` (%) | Block `move` when battery < threshold |
| `avoid_terrain` | `terrain`, `max_slip` | Block moving onto that terrain unless it was probed this sol with slip ≤ max_slip |
| `max_steps_per_move` | `n` | Clip `move.steps` to n |
| `shelter_when_tau_above` | `tau` | Replace the whole plan with `shelter` when tau > value (needs `shelter` enabled) |
| `no_drill_below_battery` | `threshold` (%) | Block `drill` when battery < threshold |

Every block or rewrite emits a `GUARDRAIL_BLOCK` event with `details: {guardrail_id, action, reason}`.

### 3.4 Harness patch (C produces and applies)

Stored in the `patches` collection.

```json
{
  "base_version": 3,
  "rationale": "Stuck 5 sols in sand at sol 8 (seed 42). Probe before entering sand.",
  "source_incidents": ["<events _id>"],
  "ops": [
    {"op": "enable_tool", "tool": "probe_terrain"},
    {"op": "add_rule", "text": "Before moving onto sand, probe_terrain in that direction."},
    {"op": "add_guardrail", "type": "avoid_terrain", "params": {"terrain": "sand", "max_slip": 0.3}}
  ],
  "status": "accepted",
  "eval": {"seeds": [101, 102, 103], "baseline_score": 41.2, "candidate_score": 55.0, "per_seed": []},
  "result_version": 4,
  "created_at": "2026-09-26T19:05:00Z"
}
```

Patch `status` is one of `proposed`, `invalid`, `evaluating`, `accepted` or `rejected`.

**Op catalog:**

| Op | Fields |
| --- | --- |
| `enable_tool`, `disable_tool` | `tool` |
| `add_rule` | `text` |
| `remove_rule` | `id` |
| `add_guardrail` | `type`, `params` |
| `update_guardrail` | `id`, `params` |
| `remove_guardrail` | `id` |
| `set_context` | `field`, `value` |
| `set_param` | `field`, `value` |
| `edit_prompt` | `find`, `replace` |

### 3.5 Score (`contracts/scoring.py`, C writes)

One formula, used by the Engineer's evaluation and shown in the UI:

```python
def score(m: Metrics) -> float:
    return (m.science
            + 0.5 * m.sols_survived
            - 2 * m.stuck_sols
            - 5 * m.incidents
            - (50 if not m.alive else 0))
```

### 3.6 Constitution (immutable)

Neither the Engineer nor a patch can change these rules. B enforces C1 at runtime. C's patch validator enforces C2 and C3.

| ID | Rule | Enforced by |
| --- | --- | --- |
| C1 | No action may take predicted battery below 5% (uses `World.action_cost`) | B, guardrail engine |
| C2 | A patch may not disable `move`, set `max_actions_per_sol` above 8, or remove guardrail `g001` (it may only raise its threshold) | C, validator |
| C3 | A patch is accepted only if its mean held-out score beats the baseline by ≥ 2% and no held-out rover dies that survived under the baseline | C, evaluator |

The constitution is what stops the Engineer from gaming the score, for example by deleting safety guardrails to grab more science. It is also our answer when judges ask about safety.

---

## 4. Mission runner (B provides, C consumes)

Module `explorer/mission.py`:

```python
def run_mission(harness: HarnessConfig, seed: int, planet: str = "mars",
                max_sols: int = 30, mode: Literal["live", "eval"] = "live",
                sol_delay: float = 0.0) -> MissionResult: ...
```

- It must be safe to call from several threads at once, because C runs 3 seeds in parallel. Avoid global state.
- **`live` mode** writes `missions`, `telemetry`, `events`, `map_knowledge` and `memories`.
- **`eval` mode** writes `missions`, `events` and `map_knowledge` only. It skips telemetry and writes no memories, so evaluation never pollutes memory. It still reads `lesson` memories.
- `sol_delay` pauses between sols so the UI can animate during the live demo.

---

## 5. MongoDB Atlas (D provides)

Use the **hackathon Atlas sandbox cluster**; finalists are only eligible if they built on it. The database is `rover`.

| Collection | Written by | Read by | Key fields | Indexes |
| --- | --- | --- | --- | --- |
| `harness_versions` | C (v1 seeded by D) | B, D | `version`, `status`, `parent_version` | `{version: 1}` unique, `{status: 1}` |
| `patches` | C | D | `base_version`, `status`, `result_version` | `{created_at: -1}` |
| `missions` | B | C, D | `_id` (string), `harness_version`, `seed`, `planet`, `mode`, `metrics`, `score`, `world` (snapshot, live only), `started_at`, `ended_at` | `{harness_version: 1, mode: 1}` |
| `telemetry` | B | D | Time-series collection: `ts`, `meta: {mission_id}`, `sol`, `pos`, `battery`, `wheel_health`, `tau`, `actions`, `blocked` | Time-series (timeField `ts`, metaField `meta`) |
| `events` | B | C, D | `mission_id`, `mode`, `harness_version`, `sol`, `type`, `severity`, `pos`, `details`, `ts` | `{mission_id: 1, sol: 1}` |
| `memories` | B (incident, discovery, summary), C (lesson) | B | `kind`, `text`, `embedding`, `mission_id`, `sol`, `loc`, `harness_version`, `planet` | Atlas Vector Search index `memories_vec` on `embedding`, filter fields `kind`, `planet` |
| `map_knowledge` | B | B, D | `mission_id`, `loc: [x, y]`, `terrain`, `slip_probed`, `hazard`, `science_hint`, `first_seen_sol` | `{loc: "2d"}`, unique `{mission_id: 1, loc: 1}` |

`mission_id` format: `m_<unixtime>_<seed>_<mode>`.

### 5.1 Helpers in `db/` (D provides, B and C use)

```python
# db/client.py
def get_db() -> Database: ...                    # reads MONGODB_URI / MONGODB_DB
HARNESS, PATCHES, MISSIONS, TELEMETRY, EVENTS, MEMORIES, MAP = (...)  # collection names

# db/memory.py
def embed(text: str) -> list[float]: ...
def add_memory(kind: str, text: str, **fields) -> str: ...   # embeds and inserts
def search_memories(query: str, kinds: list[str], k: int, planet: str | None = None) -> list[dict]: ...
def hazards_near(mission_id: str, pos: tuple[int, int], radius: int) -> list[dict]: ...  # $geoNear

# db/harness.py
def get_active_harness() -> dict: ...
def get_harness(version: int) -> dict: ...

# db/watch.py
def watch_incidents(callback) -> None: ...
    # change stream on `events`: inserts where mode == "live" and severity in [major, critical]
```

### 5.2 Example incident event (C can use this as a fixture)

```json
{
  "_id": "66f5...",
  "mission_id": "m_1790445600_42_live",
  "mode": "live",
  "harness_version": 1,
  "sol": 8,
  "type": "STUCK",
  "severity": "major",
  "pos": [12, 20],
  "details": {"terrain": "sand", "slip": 0.8, "action": {"tool": "move", "args": {"direction": "E", "steps": 4}}},
  "ts": "2026-09-26T19:02:11Z"
}
```

---

## 6. UI (A)

`web/static/map.js` exports:

```js
// Draws terrain, fog of war, science, the rover and its path on a <canvas>.
export function drawPlanet(canvas, snapshot, {path, roverPos, knownTiles, events, showTruth}) {}
```

- `snapshot` follows section 2.4.
- `path` is `[[x, y], ...]` taken from telemetry.
- `knownTiles` is the list of `map_knowledge` docs.
- `events` are drawn as markers.
- `showTruth` reveals hidden slip and science for the replay view.

A's server exposes JSON under `/api/...`. A owns the endpoint list; see [workstreams/A-planet-sim.md](workstreams/A-planet-sim.md).
