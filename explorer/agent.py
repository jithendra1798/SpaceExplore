"""Explorer planners: one Claude call per sol, or a scripted fallback for offline runs."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from functools import lru_cache

import anthropic
from pydantic import ValidationError

from contracts.models import DIRECTIONS, Action, HarnessConfig, Observation

DEFAULT_MODEL = "claude-haiku-4-5"
# These models reject a forced tool_choice and sampling params; steer them from the prompt instead.
_NO_FORCED_TOOL = ("claude-opus-5-5", "claude-fable-5-1", "claude-mythos-5-1")


@dataclass
class Plan:
    reasoning: str
    actions: list[Action]
    dropped: list[dict]  # actions the model produced that failed validation


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()  # thread-safe; shared across missions


def _plan_tool(h: HarnessConfig) -> dict:
    return {
        "name": "submit_plan",
        "description": "Submit this sol's plan: your reasoning and the actions to execute in order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reasoning": {"type": "string", "description": "2-4 sentences: situation, risks, why this plan."},
                "actions": {
                    "type": "array",
                    "maxItems": h.params.max_actions_per_sol,
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string", "enum": h.enabled_tools()},
                            "direction": {"type": "string", "enum": list(DIRECTIONS)},
                            "steps": {"type": "integer", "minimum": 1, "maximum": 5},
                        },
                        "required": ["tool"],
                    },
                },
            },
            "required": ["reasoning", "actions"],
        },
    }


def _to_actions(raw: list, enabled: set[str]) -> tuple[list[Action], list[dict]]:
    actions, dropped = [], []
    for item in raw if isinstance(raw, list) else []:
        try:
            args = {k: item[k] for k in ("direction", "steps") if k in item}
            a = Action(tool=item["tool"], args=args)
            if a.tool in ("move", "probe_terrain") and a.args.get("direction") not in DIRECTIONS:
                raise ValueError("missing direction")
            if a.tool not in enabled:
                raise ValueError("tool not enabled")
            actions.append(a)
        except (KeyError, TypeError, ValueError, ValidationError):
            dropped.append(item if isinstance(item, dict) else {"raw": item})
    return actions, dropped


class LLMPlanner:
    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("EXPLORER_MODEL", DEFAULT_MODEL)

    def plan(self, h: HarnessConfig, system: str, message: str) -> Plan:
        kwargs: dict = {}
        if self.model.startswith(_NO_FORCED_TOOL):
            kwargs["tool_choice"] = {"type": "auto"}
        else:
            kwargs["tool_choice"] = {"type": "tool", "name": "submit_plan"}
            if "haiku" in self.model:
                kwargs["temperature"] = 0.0
        try:
            resp = _client().messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                tools=[_plan_tool(h)],
                messages=[{"role": "user", "content": message}],
                **kwargs,
            )
        except anthropic.APIError as e:  # SDK already retried 429/5xx; don't kill the mission
            print(f"[explorer] LLM error, waiting this sol: {e}", file=sys.stderr)
            return Plan(f"LLM error: {e.__class__.__name__}", [Action(tool="wait")], [])
        block = next((b for b in resp.content if b.type == "tool_use" and b.name == "submit_plan"), None)
        if block is None:
            return Plan("no plan returned", [Action(tool="wait")], [])
        data = block.input if isinstance(block.input, dict) else json.loads(block.input)
        actions, dropped = _to_actions(data.get("actions", []), set(h.enabled_tools()))
        return Plan(str(data.get("reasoning", "")), actions or [Action(tool="wait")], dropped)


class ScriptedPlanner:
    """Offline stand-in (no API key): scan once, then head for the strongest signal and drill."""

    def plan(self, h: HarnessConfig, system: str, message: str, obs: Observation | None = None,
             target: tuple[int, int] | None = None) -> Plan:
        if obs is None:
            return Plan("scripted: no observation", [Action(tool="wait")], [])
        if target is None:
            return Plan("scripted: scanning for targets", [Action(tool="scan")], [])
        if obs.pos == target:
            return Plan("scripted: on target", [Action(tool="drill")], [])
        dx = (target[0] > obs.pos[0]) - (target[0] < obs.pos[0])
        dy = (target[1] > obs.pos[1]) - (target[1] < obs.pos[1])
        direction = next(k for k, v in DIRECTIONS.items() if v == (dx, dy))
        steps = max(abs(target[0] - obs.pos[0]), abs(target[1] - obs.pos[1]))
        return Plan(f"scripted: heading {direction} to {target}",
                    [Action(tool="move", args={"direction": direction, "steps": min(steps, 5)})], [])
