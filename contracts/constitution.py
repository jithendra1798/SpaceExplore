"""Immutable rules no patch can change (CONTRACTS §3.6).

C1 is enforced at runtime by the harness (explorer/guardrails.py).
C2 is checked by `check_patch`, C3 by `accept`; both are used by the Engineer.
"""

from __future__ import annotations

from contracts.models import HarnessConfig, HarnessPatch, SeedResult

BATTERY_FLOOR = 5.0  # C1: no action may take predicted battery below this (%)
MAX_ACTIONS_PER_SOL_CAP = 8  # C2
PROTECTED_GUARDRAIL = "g001"  # C2: may only be tightened, never removed or loosened
MIN_IMPROVEMENT = 0.02  # C3: candidate must beat baseline mean by >= 2%
HELD_OUT_SEEDS = [101, 102, 103]

CONSTITUTION: list[dict[str, str]] = [
    {"id": "C1", "text": f"No action may take predicted battery below {BATTERY_FLOOR:.0f}%."},
    {"id": "C2", "text": (
        "A patch may not disable `move`, set `max_actions_per_sol` above "
        f"{MAX_ACTIONS_PER_SOL_CAP}, or remove guardrail `{PROTECTED_GUARDRAIL}` "
        "(it may only raise its threshold)."
    )},
    {"id": "C3", "text": (
        "A patch is accepted only if its mean held-out score beats the baseline by at "
        f"least {MIN_IMPROVEMENT:.0%} and no held-out rover dies that survived under the baseline."
    )},
]


def constitution_text() -> str:
    return "\n".join(f"{c['id']}: {c['text']}" for c in CONSTITUTION)


def check_patch(base: HarnessConfig, patch: HarnessPatch) -> list[str]:
    """Return C2 violations; an empty list means the patch is constitutional."""
    violations: list[str] = []
    protected = next((g for g in base.guardrails if g.id == PROTECTED_GUARDRAIL), None)
    for i, op in enumerate(patch.ops):
        where = f"op {i} ({op.op})"
        if op.op == "disable_tool" and op.tool == "move":
            violations.append(f"{where}: C2 forbids disabling `move`")
        if op.op == "set_param" and op.field == "max_actions_per_sol":
            if not isinstance(op.value, int) or op.value > MAX_ACTIONS_PER_SOL_CAP:
                violations.append(f"{where}: C2 caps max_actions_per_sol at {MAX_ACTIONS_PER_SOL_CAP}")
        if op.op == "remove_guardrail" and op.id == PROTECTED_GUARDRAIL:
            violations.append(f"{where}: C2 forbids removing {PROTECTED_GUARDRAIL}")
        if op.op == "update_guardrail" and op.id == PROTECTED_GUARDRAIL and protected:
            new = (op.params or {}).get("threshold", protected.params.get("threshold"))
            if new is None or new < protected.params.get("threshold", 0):
                violations.append(f"{where}: C2 only allows raising the {PROTECTED_GUARDRAIL} threshold")
    return violations


def accept(baseline: list[SeedResult], candidate: list[SeedResult]) -> tuple[bool, str]:
    """C3: compare per-seed results on the same held-out seeds."""
    base_by_seed = {r.seed: r for r in baseline}
    for r in candidate:
        b = base_by_seed.get(r.seed)
        if b and b.metrics.alive and not r.metrics.alive:
            return False, f"C3: rover died on seed {r.seed} but survived under the baseline"
    b_mean = sum(r.score for r in baseline) / len(baseline)
    c_mean = sum(r.score for r in candidate) / len(candidate)
    needed = b_mean + MIN_IMPROVEMENT * abs(b_mean)
    if c_mean > needed and c_mean > b_mean:
        return True, f"C3: {c_mean:.1f} beats baseline {b_mean:.1f}"
    return False, f"C3: {c_mean:.1f} does not beat baseline {b_mean:.1f} by {MIN_IMPROVEMENT:.0%}"
