"""The Engineer: reads an incident and proposes a typed harness patch.

One Claude call with a single tool, `propose_patch`. The patch is not trusted: it is
validated (engineer/patch.py) and then evaluated on held-out seeds (engineer/evaluate.py).
`ScriptedEngineer` is an offline stand-in for tests and no-key runs; it is never the demo.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import get_args

import anthropic
from pydantic import ValidationError

from contracts.constitution import HELD_OUT_SEEDS, PROTECTED_GUARDRAIL, constitution_text
from contracts.models import TOOLS, GuardrailType, HarnessConfig, HarnessPatch, PatchOp, PatchOpName
from engineer.patch import GUARDRAIL_PARAMS, describe_ops
from explorer.context import GUARDRAIL_DOCS, TOOL_DOCS

DEFAULT_MODEL = "claude-sonnet-5"
MAX_OPS = 4


class EngineerError(RuntimeError):
    pass


@dataclass
class IncidentContext:
    """Everything the Engineer sees about one incident."""

    event: dict
    harness: HarnessConfig
    telemetry: list[dict] = field(default_factory=list)
    mission_events: list[dict] = field(default_factory=list)
    recent_patches: list[dict] = field(default_factory=list)


def build_context(d, event: dict, harness: HarnessConfig) -> IncidentContext:
    from engineer import store
    sol = int(event.get("sol", 0))
    return IncidentContext(
        event=event,
        harness=harness,
        telemetry=store.telemetry_before(d, event["mission_id"], sol, n=5),
        mission_events=store.mission_events(d, event["mission_id"], sol),
        recent_patches=store.recent_patches(d, n=5),
    )


# --- Prompt ------------------------------------------------------------------

SYSTEM = f"""You are the Engineer for an autonomous planetary rover. You never drive the rover. You improve the harness it runs on: its rules, guardrails, enabled tools, context policy and parameters.

The rover (the Explorer) is an LLM that plans each sol from its harness. When it has an incident, you get the incident, the telemetry and reasoning before it, and the current harness. You then propose one small, targeted patch.

How your patch is judged:
- A validator checks it against the op catalog, the guardrail catalog and the constitution. Invalid patches are rejected and shown to the whole team.
- It is then run on held-out seeds {HELD_OUT_SEEDS} that you never see. It is kept only if the mean score beats the current harness by at least 2% and no rover dies that survived before.
- Score = science + 0.5 * sols_survived - 2 * stuck_sols - 5 * incidents - 50 if the rover died.

What makes a good patch:
- Fix the root cause of this incident in a way that generalises to other maps, not just this one.
- Prefer guardrails enforced in code over prompt rules when a failure is safety-critical; rules alone are advice the Explorer can ignore.
- A guardrail that depends on a tool (avoid_terrain needs probe_terrain to ever let the rover cross; shelter_when_tau_above needs shelter) must come with that tool enabled.
- Keep it small: at most {MAX_OPS} ops, so the evaluation can tell what helped.
- Read the recent patches. Do not resubmit a patch that was rejected or invalid; learn from its reason.
- Loosening safety to gain science is gaming the score. The constitution forbids it and the evaluator punishes deaths.

Always answer by calling the propose_patch tool exactly once."""

OP_CATALOG = """- enable_tool / disable_tool: {"tool"}
- add_rule: {"text"}   (ids are assigned for you)
- remove_rule: {"id"}
- add_guardrail: {"type", "params"}   (one guardrail per type; use update_guardrail to change an existing one)
- update_guardrail: {"id", "params"}   (params are merged into the existing ones)
- remove_guardrail: {"id"}
- set_context: {"field", "value"}   fields: recent_sols (0-10), memory_k (0-10), memory_kinds (list of incident|discovery|lesson|summary), hazards_within (0-10 tiles), summarize_every (0-30 sols)
- set_param: {"field", "value"}   fields: max_actions_per_sol (1-8), max_steps_per_move (1-5)
- edit_prompt: {"find", "replace"}   (exact substring of system_prompt)"""


def _guardrail_catalog() -> str:
    return "\n".join(f"- {t}: params {sorted(GUARDRAIL_PARAMS[t])}. " + GUARDRAIL_DOCS[t].format(
        **{p: f"<{p}>" for p in GUARDRAIL_PARAMS[t]}) for t in get_args(GuardrailType))


def _fmt_telemetry(rows: list[dict]) -> str:
    if not rows:
        return "(no telemetry recorded for this mission)"
    lines = []
    for r in rows:
        acts = "; ".join(f"{a['action']['tool']} {a['action'].get('args', {})} -> {a.get('message', '')}"
                         for a in r.get("actions", []) if isinstance(a, dict) and "action" in a)
        blocked = "; ".join(f"{b.get('guardrail_id')}: {b.get('reason')}" for b in r.get("blocked", []))
        lines.append(
            f"Sol {r.get('sol')}: pos {r.get('pos')} battery {r.get('battery', 0):.0f}% tau {r.get('tau', 0):.1f} "
            f"stuck {r.get('stuck')} events {r.get('events', [])}\n"
            f"  reasoning: {r.get('reasoning', '')}\n"
            f"  planned: {[(p.get('tool'), p.get('args', {})) for p in r.get('planned', [])]}\n"
            f"  executed: {acts or '(none)'}" + (f"\n  blocked: {blocked}" if blocked else ""))
    return "\n".join(lines)


def _fmt_patches(patches: list[dict]) -> str:
    if not patches:
        return "(none yet)"
    out = []
    for p in patches:
        ev = p.get("eval") or {}
        scores = (f" baseline {ev['baseline_score']:.1f} -> candidate {ev['candidate_score']:.1f}"
                  if ev.get("baseline_score") is not None else "")
        out.append(f"- on v{p.get('base_version')}: {p.get('status')}{scores}. ops {describe_ops(p)}. "
                   f"rationale: {p.get('rationale', '')}" + (f" REASON: {p['reason']}" if p.get("reason") else ""))
        for s in ev.get("per_seed", []):
            if s.get("candidate_incidents") or s.get("candidate_alive") is False:
                out.append(f"    held-out seed {s['seed']}: baseline {s['baseline']:.1f} -> candidate {s['candidate']:.1f}"
                           f"{' (DIED)' if not s.get('candidate_alive', True) else ''}; incidents: "
                           + "; ".join(s.get("candidate_incidents", [])[:6]))
    return "\n".join(out)


def build_message(ctx: IncidentContext) -> str:
    h = ctx.harness.model_dump(mode="json", exclude={"eval", "created_at", "source_incidents"})
    event = {k: v for k, v in ctx.event.items() if k not in ("_id", "ts")}
    tools = "\n".join(f"- {TOOL_DOCS[t]}" for t in TOOLS)
    return f"""CONSTITUTION (immutable; you cannot change it):
{constitution_text()}
Guardrail {PROTECTED_GUARDRAIL} may only have its threshold raised.

OP CATALOG:
{OP_CATALOG}

GUARDRAIL CATALOG:
{_guardrail_catalog()}

ROVER TOOLS (enabled or not):
{tools}

CURRENT HARNESS (v{ctx.harness.version}):
{json.dumps(h, indent=1)}

INCIDENT:
{json.dumps(event, default=str)}

TELEMETRY FOR THE 5 SOLS UP TO THE INCIDENT (with the Explorer's own reasoning):
{_fmt_telemetry(ctx.telemetry)}

ALL EVENTS IN THIS MISSION SO FAR:
{json.dumps(ctx.mission_events, default=str) if ctx.mission_events else "(none)"}

RECENT PATCHES (newest first, including rejected and invalid ones):
{_fmt_patches(ctx.recent_patches)}

Diagnose the root cause, then call propose_patch."""


def _propose_tool() -> dict:
    return {
        "name": "propose_patch",
        "description": "Propose one harness patch that fixes the root cause of the incident.",
        "input_schema": {
            "type": "object",
            "properties": {
                "diagnosis": {"type": "string", "description": "What went wrong and why, citing sols and telemetry."},
                "rationale": {"type": "string", "description": "Why these ops fix it and why they will not hurt other maps."},
                "lesson": {"type": "string", "description": "One sentence the rover should remember on future missions."},
                "ops": {
                    "type": "array", "minItems": 1, "maxItems": MAX_OPS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": list(get_args(PatchOpName))},
                            "tool": {"type": "string", "enum": list(TOOLS)},
                            "text": {"type": "string"},
                            "id": {"type": "string", "description": "rule or guardrail id, e.g. r001, g002"},
                            "type": {"type": "string", "enum": list(get_args(GuardrailType))},
                            "params": {"type": "object"},
                            "field": {"type": "string"},
                            "value": {"description": "new value for set_context / set_param"},
                            "find": {"type": "string"},
                            "replace": {"type": "string"},
                        },
                        "required": ["op"],
                    },
                },
            },
            "required": ["diagnosis", "rationale", "lesson", "ops"],
        },
    }


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


class LLMEngineer:
    name = "llm"

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("ENGINEER_MODEL", DEFAULT_MODEL)

    def propose(self, ctx: IncidentContext, attempts: int = 2) -> HarnessPatch:
        for attempt in range(1, attempts + 1):
            try:
                patch = self._propose_once(ctx)
            except EngineerError:
                if attempt == attempts:
                    raise
                continue
            if patch.ops or attempt == attempts:
                return patch  # an empty patch is left for the validator to reject visibly
        raise AssertionError("unreachable")

    def _propose_once(self, ctx: IncidentContext) -> HarnessPatch:
        # tool_choice "auto" plus the system instruction: newer models reject forced tool use,
        # and Sonnet 5 rejects temperature, so neither is sent.
        resp = _client().messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM,
            tools=[_propose_tool()],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": build_message(ctx)}],
        )
        if resp.stop_reason == "refusal":
            raise EngineerError("engineer model refused the request")
        block = next((b for b in resp.content if b.type == "tool_use" and b.name == "propose_patch"), None)
        if block is None:
            text = " ".join(b.text for b in resp.content if b.type == "text")[:300]
            raise EngineerError(f"engineer did not call propose_patch (stop_reason {resp.stop_reason}): {text}")
        data = block.input if isinstance(block.input, dict) else json.loads(block.input)
        return patch_from_tool_input(data, ctx)


_LEAKED_PARAM = re.compile(r"</(\w+)>\s*<parameter name=\"(\w+)\">")


def _repair_leaked_fields(data: dict) -> dict:
    """The model sometimes writes the next tool parameter inside a string, e.g.
    diagnosis = '... </diagnosis>\\n<parameter name="rationale">...'. Split it back out."""
    data = dict(data)
    for key in ("diagnosis", "rationale", "lesson"):
        text = data.get(key)
        if not isinstance(text, str) or not _LEAKED_PARAM.search(text):
            continue
        parts = _LEAKED_PARAM.split(text)  # [value, close, next_key, next_value, close, next_key, ...]
        data[key] = parts[0].strip()
        for i in range(2, len(parts) - 1, 3):
            nxt, value = parts[i], re.sub(r"</\w+>\s*$", "", parts[i + 1]).strip()
            if nxt in ("diagnosis", "rationale", "lesson") and not str(data.get(nxt) or "").strip():
                data[nxt] = value
            elif nxt == "ops" and not data.get("ops"):
                try:
                    data["ops"] = json.loads(value)
                except json.JSONDecodeError:
                    pass
    if isinstance(data.get("ops"), str):  # ops sent as a JSON string instead of an array
        try:
            data["ops"] = json.loads(data["ops"])
        except json.JSONDecodeError:
            pass
    return data


def patch_from_tool_input(data: dict, ctx: IncidentContext) -> HarnessPatch:
    data = _repair_leaked_fields(data)
    ops, bad = [], []
    for raw in data.get("ops", []):
        try:
            ops.append(PatchOp.model_validate({k: v for k, v in raw.items() if v is not None}))
        except (ValidationError, AttributeError) as e:
            bad.append(f"{raw}: {e}")
    if bad:
        raise EngineerError("engineer produced ops outside the catalog: " + " | ".join(bad))
    event_id = str(ctx.event.get("_id", ""))
    return HarnessPatch(
        base_version=ctx.harness.version,
        diagnosis=str(data.get("diagnosis", "")),
        rationale=str(data.get("rationale", "")),
        lesson=str(data.get("lesson", "")),
        source_incidents=[event_id] if event_id else [],
        ops=ops,
    )


# --- Offline stand-in ---------------------------------------------------------

_SAND_FIX = {
    "diagnosis": "The rover drove into unprobed sand and got stuck.",
    "rationale": "Probe sand before entering it, and enforce it in code.",
    "lesson": "Probe sand before driving onto it; slip above 0.3 traps the rover.",
    "ops": [{"op": "enable_tool", "tool": "probe_terrain"},
            {"op": "add_rule", "text": "Before moving onto sand, probe_terrain in that direction."},
            {"op": "add_guardrail", "type": "avoid_terrain", "params": {"terrain": "sand", "max_slip": 0.3}}],
}
_STORM_FIX = {
    "diagnosis": "Battery collapsed during a dust storm because the rover kept driving.",
    "rationale": "Shelter when tau is high; solar gain is near zero above tau 2.",
    "lesson": "When tau rises above 2, shelter until the storm passes.",
    "ops": [{"op": "enable_tool", "tool": "shelter"},
            {"op": "add_rule", "text": "If tau is above 1.5 and rising, shelter for the sol."},
            {"op": "add_guardrail", "type": "shelter_when_tau_above", "params": {"tau": 2.0}}],
}
_MEMORY_FIX = {
    "diagnosis": "The rover repeats mistakes it has already made on earlier missions.",
    "rationale": "Retrieve lessons and past incidents into the prompt.",
    "lesson": "Past incidents and lessons are worth reading before planning.",
    "ops": [{"op": "set_context", "field": "memory_k", "value": 3},
            {"op": "set_context", "field": "memory_kinds", "value": ["lesson", "incident"]}],
}


class ScriptedEngineer:
    """Maps incident types to canned fixes, skipping any the harness already has. Offline only."""

    name = "scripted"

    def propose(self, ctx: IncidentContext) -> HarnessPatch:
        h = ctx.harness
        etype = ctx.event.get("type", "")
        order = ([_STORM_FIX, _SAND_FIX] if etype in ("BATTERY_CRITICAL", "BATTERY_LOW", "DEATH", "STORM_ONSET")
                 else [_SAND_FIX, _STORM_FIX])
        have = {"probe_terrain": h.tools.get("probe_terrain"), "shelter": h.tools.get("shelter")}
        for fix in order + [_MEMORY_FIX]:
            first = fix["ops"][0]
            if first["op"] == "enable_tool" and have.get(first["tool"]):
                continue
            if fix is _MEMORY_FIX and h.context_policy.memory_k > 0:
                continue
            return patch_from_tool_input(fix, ctx)
        raise EngineerError("scripted engineer has no fix left to try")


def get_engineer(name: str):
    if name == "llm":
        return LLMEngineer()
    if name == "scripted":
        return ScriptedEngineer()
    raise ValueError(f"unknown engineer {name!r}; pick llm or scripted")
