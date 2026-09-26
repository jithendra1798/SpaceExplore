"""Mission runners the Engineer can evaluate with. All share `run_mission`'s signature (CONTRACTS §4)."""

from __future__ import annotations

from functools import partial
from typing import Callable

from contracts.models import MissionResult

Runner = Callable[..., MissionResult]
RUNNERS = ("real", "scripted", "fake")


def get_runner(name: str) -> Runner:
    if name == "fake":
        from engineer.fake_runner import fake_run_mission
        return fake_run_mission
    from explorer.agent import LLMPlanner, ScriptedPlanner
    from explorer.mission import run_mission
    if name == "real":
        return partial(run_mission, planner=LLMPlanner())
    if name == "scripted":  # B's runtime and guardrails with a scripted planner: no API key needed
        return partial(run_mission, planner=ScriptedPlanner())
    raise ValueError(f"unknown runner {name!r}; pick one of {RUNNERS}")
