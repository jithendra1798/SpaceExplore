"""Guardrail engine (CONTRACTS §3.3) plus constitution rule C1 (§3.6).

Guardrails run in code before the sim executes a plan. The plan is walked in
order with a predicted battery and position, so each action is checked against
the state it would actually run in. Blocked or rewritten actions become
GUARDRAIL_BLOCK events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from contracts.constitution import BATTERY_FLOOR
from contracts.models import DIRECTIONS, Action, HarnessConfig, Observation


@dataclass
class Block:
    guardrail_id: str
    action: dict
    reason: str
    rewritten_to: dict | None = None


@dataclass
class GuardrailResult:
    allowed: list[Action]
    blocks: list[Block] = field(default_factory=list)


def enforce(
    harness: HarnessConfig,
    obs: Observation,
    plan: list[Action],
    action_cost: Callable[[Action], float],
    known_terrain: dict[tuple[int, int], str],
    probed_slip: dict[tuple[int, int], float],
) -> GuardrailResult:
    blocks: list[Block] = []
    enabled = set(harness.enabled_tools())
    rails = {g.type: g for g in harness.guardrails}

    # Harness-level caps.
    cap = harness.params.max_actions_per_sol
    for a in plan[cap:]:
        blocks.append(Block("params.max_actions_per_sol", a.model_dump(), f"plan longer than {cap} actions"))
    plan = plan[:cap]

    # Whole-plan rewrite: shelter in a storm.
    g = rails.get("shelter_when_tau_above")
    if g and "shelter" in enabled and obs.tau > float(g.params.get("tau", 2.0)):
        if [a.tool for a in plan] != ["shelter"]:
            blocks.append(Block(g.id, {"plan": [a.model_dump() for a in plan]},
                                f"tau {obs.tau} > {g.params.get('tau')}: sheltering instead",
                                rewritten_to={"tool": "shelter", "args": {}}))
        return GuardrailResult([Action(tool="shelter")], blocks)

    battery, pos = obs.battery, obs.pos
    max_steps = harness.params.max_steps_per_move
    if "max_steps_per_move" in rails:
        max_steps = min(max_steps, int(rails["max_steps_per_move"].params.get("n", max_steps)))
    allowed: list[Action] = []

    for a in plan:
        dump = a.model_dump()
        if a.tool not in enabled:
            blocks.append(Block("tools", dump, f"tool `{a.tool}` is not enabled"))
            continue

        if a.tool == "move":
            steps = int(a.args.get("steps", 1))
            if steps > max_steps:
                gid = rails["max_steps_per_move"].id if "max_steps_per_move" in rails else "params.max_steps_per_move"
                a = Action(tool="move", args={**a.args, "steps": max_steps})
                blocks.append(Block(gid, dump, f"steps clipped {steps} -> {max_steps}", rewritten_to=a.model_dump()))
                steps = max_steps
            g = rails.get("min_battery_for_move")
            if g and battery < float(g.params.get("threshold", 0)):
                blocks.append(Block(g.id, dump, f"battery {battery:.0f}% < {g.params['threshold']}%"))
                continue
            g = rails.get("avoid_terrain")
            if g and not obs.stuck:
                safe_steps, reason = _safe_steps(pos, a, steps, g.params, known_terrain, probed_slip)
                if safe_steps < steps:
                    if safe_steps == 0:
                        blocks.append(Block(g.id, dump, reason))
                        continue
                    a = Action(tool="move", args={**a.args, "steps": safe_steps})
                    blocks.append(Block(g.id, dump, reason, rewritten_to=a.model_dump()))
                    steps = safe_steps

        if a.tool == "drill":
            g = rails.get("no_drill_below_battery")
            if g and battery < float(g.params.get("threshold", 0)):
                blocks.append(Block(g.id, dump, f"battery {battery:.0f}% < {g.params['threshold']}%"))
                continue

        cost = action_cost(a)
        if battery - cost < BATTERY_FLOOR:  # C1, cannot be patched away
            blocks.append(Block("C1", a.model_dump(), f"would take battery to {battery - cost:.0f}% (< {BATTERY_FLOOR:.0f}%)"))
            continue

        battery -= cost
        if a.tool == "move" and not obs.stuck:
            dx, dy = DIRECTIONS[a.args.get("direction", "N")]
            pos = (pos[0] + dx * steps, pos[1] + dy * steps)
        allowed.append(a)

    return GuardrailResult(allowed, blocks)


def _safe_steps(pos, a: Action, steps: int, params: dict, known_terrain, probed_slip) -> tuple[int, str]:
    """How many steps of a move avoid unprobed / too-slippery tiles of the guarded terrain."""
    terrain = params.get("terrain", "sand")
    max_slip = float(params.get("max_slip", 0.3))
    dx, dy = DIRECTIONS[a.args.get("direction", "N")]
    x, y = pos
    for i in range(steps):
        x, y = x + dx, y + dy
        if known_terrain.get((x, y)) != terrain:
            continue
        slip = probed_slip.get((x, y))
        if slip is None:
            return i, f"({x},{y}) is {terrain} and not probed; probe_terrain first"
        if slip > max_slip:
            return i, f"({x},{y}) {terrain} slip {slip:.2f} > {max_slip}"
    return steps, ""
