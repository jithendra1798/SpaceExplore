"""Apply and validate harness patches (CONTRACTS §3.3, §3.4, rule C2).

`validate_patch` returns a list of human-readable errors; an empty list means the
patch may go to evaluation. Invalid patches are stored with status `invalid` so
the demo can show the rejection.
"""

from __future__ import annotations

from typing import Any, get_args

from pydantic import ValidationError

from contracts.constitution import check_patch
from contracts.models import (
    TOOLS, ContextPolicy, GuardrailType, HarnessConfig, HarnessParams, HarnessPatch, Terrain,
)

GUARDRAIL_PARAMS: dict[str, dict[str, type | tuple]] = {
    "min_battery_for_move": {"threshold": (int, float)},
    "avoid_terrain": {"terrain": str, "max_slip": (int, float)},
    "max_steps_per_move": {"n": int},
    "shelter_when_tau_above": {"tau": (int, float)},
    "no_drill_below_battery": {"threshold": (int, float)},
}
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "enable_tool": ("tool",), "disable_tool": ("tool",),
    "add_rule": ("text",), "remove_rule": ("id",),
    "add_guardrail": ("type", "params"), "update_guardrail": ("id", "params"), "remove_guardrail": ("id",),
    "set_context": ("field", "value"), "set_param": ("field", "value"),
    "edit_prompt": ("find", "replace"),
}
# Sanity bounds so a patch cannot blow up the prompt or the sol budget.
CONTEXT_BOUNDS = {"recent_sols": (0, 10), "memory_k": (0, 10), "hazards_within": (0, 10), "summarize_every": (0, 30)}
PARAM_BOUNDS = {"max_actions_per_sol": (1, 8), "max_steps_per_move": (1, 5)}
CONTENT_KEYS = ("system_prompt", "rules", "guardrails", "tools", "context_policy", "params")
TERRAINS = set(get_args(Terrain))
GUARDRAIL_TYPES = set(get_args(GuardrailType))


class PatchError(ValueError):
    pass


def _next_id(prefix: str, ids: list[str]) -> str:
    nums = [int(i[1:]) for i in ids if i.startswith(prefix) and i[1:].isdigit()]
    return f"{prefix}{max(nums, default=0) + 1:03d}"


def apply_patch(config: HarnessConfig, patch: HarnessPatch, version: int | None = None) -> HarnessConfig:
    """Return a new candidate config. Raises PatchError on a reference that does not resolve."""
    d = config.model_dump(mode="python")
    new_version = version if version is not None else config.version + 1
    rules: list[dict] = d["rules"]
    rails: list[dict] = d["guardrails"]

    for i, op in enumerate(patch.ops):
        where = f"op {i} ({op.op})"
        if op.op in ("enable_tool", "disable_tool"):
            d["tools"][op.tool] = op.op == "enable_tool"
        elif op.op == "add_rule":
            rules.append({"id": _next_id("r", [r["id"] for r in rules]), "text": op.text.strip(), "added_in": new_version})
        elif op.op == "remove_rule":
            if not any(r["id"] == op.id for r in rules):
                raise PatchError(f"{where}: no rule with id {op.id!r}")
            rules[:] = [r for r in rules if r["id"] != op.id]
        elif op.op == "add_guardrail":
            rails.append({"id": _next_id("g", [g["id"] for g in rails]), "type": op.type,
                          "params": dict(op.params or {}), "added_in": new_version})
        elif op.op == "update_guardrail":
            g = next((g for g in rails if g["id"] == op.id), None)
            if g is None:
                raise PatchError(f"{where}: no guardrail with id {op.id!r}")
            g["params"] = {**g["params"], **(op.params or {})}
        elif op.op == "remove_guardrail":
            if not any(g["id"] == op.id for g in rails):
                raise PatchError(f"{where}: no guardrail with id {op.id!r}")
            rails[:] = [g for g in rails if g["id"] != op.id]
        elif op.op == "set_context":
            d["context_policy"][op.field] = op.value
        elif op.op == "set_param":
            d["params"][op.field] = op.value
        elif op.op == "edit_prompt":
            if op.find not in d["system_prompt"]:
                raise PatchError(f"{where}: text to replace not found in system_prompt")
            d["system_prompt"] = d["system_prompt"].replace(op.find, op.replace, 1)

    d.update(version=new_version, parent_version=config.version, status="candidate", author="engineer",
             rationale=patch.rationale, source_incidents=list(patch.source_incidents), eval=None)
    d.pop("created_at", None)
    try:
        return HarnessConfig.model_validate(d)
    except ValidationError as e:
        raise PatchError(f"patched harness is not a valid config: {e.errors()[0]['msg']}") from e


def _check_op_fields(i: int, op, base: HarnessConfig) -> list[str]:
    where = f"op {i} ({op.op})"
    errors = [f"{where}: missing `{f}`" for f in REQUIRED_FIELDS[op.op] if getattr(op, f) is None]
    if errors:
        return errors
    if op.op in ("enable_tool", "disable_tool") and op.tool not in TOOLS:
        errors.append(f"{where}: unknown tool {op.tool!r}")
    if op.op == "add_rule" and not op.text.strip():
        errors.append(f"{where}: empty rule text")
    if op.op in ("add_guardrail", "update_guardrail"):
        gtype = op.type if op.op == "add_guardrail" else next((g.type for g in base.guardrails if g.id == op.id), None)
        if gtype is None:
            return errors  # unresolved id; apply_patch reports it
        if gtype not in GUARDRAIL_TYPES:
            return errors + [f"{where}: {gtype!r} is not in the guardrail catalog"]
        allowed = GUARDRAIL_PARAMS[gtype]
        params = op.params or {}
        if extra := set(params) - set(allowed):
            errors.append(f"{where}: {gtype} does not take params {sorted(extra)}")
        if op.op == "add_guardrail" and (missing := set(allowed) - set(params)):
            errors.append(f"{where}: {gtype} needs params {sorted(missing)}")
        for k, v in params.items():
            if k in allowed and (isinstance(v, bool) or not isinstance(v, allowed[k])):
                errors.append(f"{where}: param `{k}` has the wrong type ({type(v).__name__})")
        if "terrain" in params and params["terrain"] not in TERRAINS:
            errors.append(f"{where}: unknown terrain {params['terrain']!r}")
        if "max_slip" in params and isinstance(params["max_slip"], (int, float)) and not 0 <= params["max_slip"] <= 1:
            errors.append(f"{where}: max_slip must be between 0 and 1")
    if op.op == "set_context":
        if op.field not in ContextPolicy.model_fields:
            errors.append(f"{where}: unknown context_policy field {op.field!r}")
        elif op.field in CONTEXT_BOUNDS and not _in_bounds(op.value, CONTEXT_BOUNDS[op.field]):
            errors.append(f"{where}: {op.field} must be an integer in {CONTEXT_BOUNDS[op.field]}")
    if op.op == "set_param":
        if op.field not in HarnessParams.model_fields:
            errors.append(f"{where}: unknown params field {op.field!r}")
        elif not _in_bounds(op.value, PARAM_BOUNDS[op.field]):
            errors.append(f"{where}: {op.field} must be an integer in {PARAM_BOUNDS[op.field]}")
    return errors


def _in_bounds(value: Any, bounds: tuple[int, int]) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and bounds[0] <= value <= bounds[1]


def _content(h: HarnessConfig) -> dict:
    d = h.model_dump(mode="json", include=set(CONTENT_KEYS))
    for item in d["rules"] + d["guardrails"]:
        item.pop("added_in", None)
    return d


def validate_patch(base: HarnessConfig, patch: HarnessPatch) -> list[str]:
    if not patch.ops:
        return ["patch has no ops"]
    if patch.base_version != base.version:
        return [f"patch targets v{patch.base_version} but the active harness is v{base.version}"]

    errors: list[str] = []
    for i, op in enumerate(patch.ops):
        errors += _check_op_fields(i, op, base)
    errors += check_patch(base, patch)  # C2
    if errors:
        return errors

    try:
        candidate = apply_patch(base, patch)
    except PatchError as e:
        return [str(e)]

    # The runtime keys guardrails by type, so a second rail of the same type silently replaces
    # the first. That would let a patch shadow g001 with a lower threshold, so forbid it.
    types = [g.type for g in candidate.guardrails]
    for t in sorted({t for t in types if types.count(t) > 1}):
        errors.append(f"two `{t}` guardrails; use update_guardrail on the existing one instead")
    if any(g.type == "shelter_when_tau_above" for g in candidate.guardrails) and not candidate.tools.get("shelter"):
        errors.append("shelter_when_tau_above needs the `shelter` tool enabled")
    if _content(candidate) == _content(base):
        errors.append("patch is a no-op: the harness would not change")
    return errors


def describe_ops(patch: HarnessPatch | dict) -> list[str]:
    ops = patch.ops if isinstance(patch, HarnessPatch) else patch.get("ops", [])
    out = []
    for op in ops:
        o = op.model_dump(exclude_none=True) if hasattr(op, "model_dump") else {k: v for k, v in op.items() if v is not None}
        name = o.pop("op")
        out.append(f"{name}(" + ", ".join(f"{k}={v!r}" for k, v in o.items()) + ")")
    return out
