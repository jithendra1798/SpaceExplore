"""Shared data models. Source of truth: docs/CONTRACTS.md §3.

Additive changes only after 2:15 PM; log every change in docs/DEPENDENCIES.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Tool = Literal["move", "probe_terrain", "scan", "drill", "shelter", "wait"]
TOOLS: tuple[str, ...] = ("move", "probe_terrain", "scan", "drill", "shelter", "wait")

Direction = Literal["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
# Grid is row-major terrain[y][x]; north is y - 1 (up on screen).
DIRECTIONS: dict[str, tuple[int, int]] = {
    "N": (0, -1), "NE": (1, -1), "E": (1, 0), "SE": (1, 1),
    "S": (0, 1), "SW": (-1, 1), "W": (-1, 0), "NW": (-1, -1),
}

Terrain = Literal["bedrock", "regolith", "sand", "rocks", "crater_edge", "ice"]
Severity = Literal["info", "minor", "major", "critical"]
Mode = Literal["live", "eval"]
MemoryKind = Literal["incident", "discovery", "lesson", "summary"]
HarnessStatus = Literal["candidate", "active", "retired", "rejected"]
PatchStatus = Literal["proposed", "invalid", "evaluating", "accepted", "rejected"]
GuardrailType = Literal[
    "min_battery_for_move",
    "avoid_terrain",
    "max_steps_per_move",
    "shelter_when_tau_above",
    "no_drill_below_battery",
]

# Event types (CONTRACTS §2.3). GUARDRAIL_BLOCK is emitted by the harness runtime, not the sim.
EVENT_TYPES: tuple[str, ...] = (
    "STUCK", "FREED", "FALL", "WHEEL_DAMAGE", "BATTERY_LOW", "BATTERY_CRITICAL",
    "STORM_ONSET", "STORM_END", "DISCOVERY", "DEATH", "GUARDRAIL_BLOCK",
    "NAV_DRIFT", "GROUND_UPLINK",
)
INCIDENT_SEVERITIES: tuple[str, ...] = ("major", "critical")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Simulator I/O (§2) ------------------------------------------------------


class TileView(BaseModel):
    x: int
    y: int
    terrain: Terrain


class Observation(BaseModel):
    sol: int
    pos: tuple[int, int]
    battery: float  # 0-100 (%)
    wheel_health: float  # 0-1
    panel_dust: float  # 0-1
    stuck: bool
    tau: float  # dust opacity: ~0.5 clear, >2 storm
    tau_trend: float  # tau change vs previous sol (storm warning)
    local_tiles: list[TileView]  # radius 3; slip and science are hidden
    signals: list[str] = []  # e.g. "anomaly at (18,22) strength 0.7"


class Action(BaseModel):
    tool: Tool
    args: dict[str, Any] = {}


class ActionOutcome(BaseModel):
    action: Action
    ok: bool
    message: str
    data: dict[str, Any] = {}  # e.g. {"slip": 0.78} from probe_terrain


class SimEvent(BaseModel):
    type: str
    severity: Severity
    sol: int
    pos: tuple[int, int]
    details: dict[str, Any] = {}


class StepResult(BaseModel):
    sol: int
    executed: list[ActionOutcome]
    events: list[SimEvent]
    observation: Observation  # state after the sol ends
    alive: bool
    science_gained: float


class Metrics(BaseModel):
    sols_survived: int
    alive: bool
    science: float
    distance: int
    stuck_sols: int
    incidents: int  # count of major + critical events
    min_battery: float


class MissionResult(BaseModel):
    mission_id: str
    harness_version: int
    seed: int
    planet: str
    mode: Mode
    metrics: Metrics
    score: float
    incident_event_ids: list[str] = []


# --- Harness config (§3.2, §3.3) ---------------------------------------------


class Rule(BaseModel):
    id: str
    text: str
    added_in: int


class Guardrail(BaseModel):
    id: str
    type: GuardrailType
    params: dict[str, Any]
    added_in: int


class ContextPolicy(BaseModel):
    recent_sols: int = 3
    memory_k: int = 0
    memory_kinds: list[MemoryKind] = ["lesson"]
    hazards_within: int = 0
    summarize_every: int = 0


class HarnessParams(BaseModel):
    max_actions_per_sol: int = 5
    max_steps_per_move: int = 5


class SeedResult(BaseModel):
    seed: int
    score: float
    metrics: Metrics


class EvalResult(BaseModel):
    seeds: list[int]
    score: float
    per_seed: list[SeedResult] = []


class HarnessConfig(BaseModel):
    """One document in `harness_versions`."""

    model_config = ConfigDict(extra="ignore")  # tolerate Mongo's _id

    version: int
    parent_version: int | None = None
    status: HarnessStatus = "candidate"
    author: Literal["human", "engineer"] = "engineer"
    created_at: datetime = Field(default_factory=utcnow)
    rationale: str = ""
    source_incidents: list[str] = []
    system_prompt: str
    rules: list[Rule] = []
    guardrails: list[Guardrail] = []
    tools: dict[Tool, bool]
    context_policy: ContextPolicy = ContextPolicy()
    params: HarnessParams = HarnessParams()
    eval: EvalResult | None = None

    def enabled_tools(self) -> list[str]:
        return [t for t in TOOLS if self.tools.get(t, False)]


# --- Harness patch (§3.4) ----------------------------------------------------

PatchOpName = Literal[
    "enable_tool", "disable_tool",
    "add_rule", "remove_rule",
    "add_guardrail", "update_guardrail", "remove_guardrail",
    "set_context", "set_param", "edit_prompt",
]


class PatchOp(BaseModel):
    op: PatchOpName
    tool: Tool | None = None  # enable_tool, disable_tool
    text: str | None = None  # add_rule
    id: str | None = None  # remove_rule, update_guardrail, remove_guardrail
    type: GuardrailType | None = None  # add_guardrail
    params: dict[str, Any] | None = None  # add_guardrail, update_guardrail
    field: str | None = None  # set_context, set_param
    value: Any = None  # set_context, set_param
    find: str | None = None  # edit_prompt
    replace: str | None = None  # edit_prompt


class PatchEval(BaseModel):
    seeds: list[int]
    baseline_score: float
    candidate_score: float
    per_seed: list[dict[str, Any]] = []


class HarnessPatch(BaseModel):
    """One document in `patches`."""

    model_config = ConfigDict(extra="ignore")

    base_version: int
    diagnosis: str = ""
    rationale: str
    lesson: str = ""  # one sentence written to `memories` (kind "lesson") if the patch is accepted
    source_incidents: list[str] = []
    ops: list[PatchOp]
    status: PatchStatus = "proposed"
    reason: str | None = None  # why it was invalid or rejected
    eval: PatchEval | None = None
    result_version: int | None = None
    created_at: datetime = Field(default_factory=utcnow)


# --- Stored documents (§5) ---------------------------------------------------


class EventDoc(SimEvent):
    """One document in `events`."""

    model_config = ConfigDict(extra="ignore")

    mission_id: str
    mode: Mode
    harness_version: int
    ts: datetime = Field(default_factory=utcnow)
