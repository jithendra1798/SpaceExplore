"""Model bridge for the simulator.

Workstream B owns ``contracts.models``.  The simulator can be developed and
run before that module lands, so it uses small dataclass equivalents in the
meantime and transparently switches to the shared Pydantic models later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

try:  # pragma: no cover - exercised after Workstream B lands
    from contracts.models import (  # type: ignore[import-not-found]
        Action,
        ActionOutcome,
        Metrics,
        Observation,
        SimEvent,
        StepResult,
        TileView,
    )
except ImportError:
    Tool = Literal["move", "probe_terrain", "scan", "drill", "shelter", "wait"]
    Severity = Literal["info", "minor", "major", "critical"]

    @dataclass(slots=True)
    class TileView:
        x: int
        y: int
        terrain: str

    @dataclass(slots=True)
    class Observation:
        sol: int
        pos: tuple[int, int]
        battery: float
        wheel_health: float
        panel_dust: float
        stuck: bool
        tau: float
        tau_trend: float
        local_tiles: list[TileView]
        signals: list[str] = field(default_factory=list)

    @dataclass(slots=True)
    class Action:
        tool: Tool
        args: dict[str, Any] = field(default_factory=dict)

    @dataclass(slots=True)
    class ActionOutcome:
        action: Action
        ok: bool
        message: str
        data: dict[str, Any] = field(default_factory=dict)

    @dataclass(slots=True)
    class SimEvent:
        type: str
        severity: Severity
        sol: int
        pos: tuple[int, int]
        details: dict[str, Any] = field(default_factory=dict)

    @dataclass(slots=True)
    class StepResult:
        sol: int
        executed: list[ActionOutcome]
        events: list[SimEvent]
        observation: Observation
        alive: bool
        science_gained: float

    @dataclass(slots=True)
    class Metrics:
        sols_survived: int
        alive: bool
        science: float
        distance: int
        stuck_sols: int
        incidents: int
        min_battery: float


def model_dump(value: Any) -> dict[str, Any]:
    """Return a plain mapping for either a Pydantic model or local dataclass."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"Cannot serialize model of type {type(value).__name__}")


__all__ = [
    "Action",
    "ActionOutcome",
    "Metrics",
    "Observation",
    "SimEvent",
    "StepResult",
    "TileView",
    "model_dump",
]
