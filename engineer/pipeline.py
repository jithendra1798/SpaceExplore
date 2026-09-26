"""One improvement step: incident -> diagnosis -> patch -> validate -> evaluate -> promote or reject.

Shared by the live loop (engineer/watch.py) and the offline loop (engineer/evolve.py).
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass

from contracts.constitution import HELD_OUT_SEEDS
from contracts.models import HarnessPatch, utcnow
from db.client import MEMORIES
from engineer import store
from engineer.agent import EngineerError, build_context
from engineer.evaluate import evaluate
from engineer.patch import apply_patch, describe_ops, validate_patch
from engineer.runners import Runner


@dataclass
class Outcome:
    status: str  # "invalid", "accepted", "rejected" or "error"
    patch_id: str | None
    base_version: int
    candidate_version: int | None = None
    reason: str = ""
    baseline_score: float | None = None
    candidate_score: float | None = None


def log(msg: str) -> None:
    print(f"[engineer] {msg}", flush=True)


def improve(
    d,
    event: dict,
    engineer,
    runner: Runner,
    runner_name: str,
    planet: str = "mars",
    max_sols: int = 30,
    seeds: list[int] = HELD_OUT_SEEDS,
) -> Outcome:
    base = store.get_active(d)
    log(f"incident {event.get('type')} sol {event.get('sol')} in {event.get('mission_id')} (active v{base.version})")

    ctx = build_context(d, event, base)
    try:
        patch: HarnessPatch = engineer.propose(ctx)
    except EngineerError as e:
        log(f"no patch: {e}")
        return Outcome("error", None, base.version, reason=str(e))
    log(f"diagnosis: {patch.diagnosis}")
    log(f"proposed ops: {describe_ops(patch)}")
    meta = {"engineer": engineer.name, "runner": runner_name, "incident_type": event.get("type"),
            "mission_id": event.get("mission_id")}

    errors = validate_patch(base, patch)
    if errors:
        reason = "; ".join(errors)
        patch.status, patch.reason = "invalid", reason
        pid = store.insert_patch(d, patch, **meta)
        log(f"INVALID: {reason}")
        return Outcome("invalid", pid, base.version, reason=reason)

    version = store.next_version(d)
    candidate = apply_patch(base, patch, version=version)
    store.save_harness(d, candidate)
    patch.status = "evaluating"
    pid = store.insert_patch(d, patch, candidate_version=version, **meta)
    log(f"evaluating v{version} against v{base.version} on seeds {seeds} ({runner_name} runner)")

    try:
        ok, reason, patch_eval, cand_eval = evaluate(d, base, candidate, runner, runner_name, seeds, planet, max_sols)
    except Exception as e:  # a crashed mission must not leave the patch stuck in "evaluating"
        traceback.print_exc(file=sys.stderr)
        # An infrastructure failure says nothing about the patch: flag it and free the version number.
        reason = f"evaluation failed: {e.__class__.__name__}: {e}"
        store.update_patch(d, pid, status="rejected", reason=reason, error=True, candidate_version=None)
        store.delete_harness(d, version)
        return Outcome("error", pid, base.version, None, reason=reason)

    eval_doc = patch_eval.model_dump(mode="python")
    store.set_harness_fields(d, version, eval=cand_eval.model_dump(mode="python"),
                             eval_key=_raw_eval_key(d, base.version))
    if ok:
        store.promote(d, version, base.version)
        store.update_patch(d, pid, status="accepted", reason=reason, eval=eval_doc, result_version=version)
        _write_lesson(d, patch, version, planet)
        log(f"ACCEPTED v{version}: {reason}")
    else:
        store.set_harness_fields(d, version, status="rejected")
        store.update_patch(d, pid, status="rejected", reason=reason, eval=eval_doc)
        log(f"REJECTED v{version}: {reason}")
    return Outcome("accepted" if ok else "rejected", pid, base.version, version, reason,
                   patch_eval.baseline_score, patch_eval.candidate_score)


def _raw_eval_key(d, base_version: int) -> str | None:
    # The candidate ran under the baseline's runner, planet and seeds, so it shares the baseline's cache key.
    return (store.get_harness_raw(d, base_version) or {}).get("eval_key")


def _write_lesson(d, patch: HarnessPatch, version: int, planet: str) -> None:
    text = patch.lesson or patch.rationale
    if not text:
        return
    # No planet: db.memory stores it as "any", so the lesson applies on every planet.
    fields = {"harness_version": version, "source": "engineer", "learned_on": planet,
              "source_incidents": patch.source_incidents}
    if store.is_overridden():
        d[MEMORIES].insert_one({"kind": "lesson", "text": text, "planet": "any", "created_at": utcnow(), **fields})
        return
    from explorer import memory  # embeds via D's db.memory.add_memory, falls back to a plain insert
    memory.remember(d, "lesson", text, **fields)
