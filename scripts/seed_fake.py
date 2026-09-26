"""Fake data for building the Mission Control UI before real runs exist (DEPENDENCIES D03).

Everything goes into a separate database (default `rover_fake`), never the real one:
fake harness versions would collide with real ones on the unique `version` index, a fake
`active` version would be loaded by the Explorer, fake live incidents would wake the
Engineer's change stream, and fake memories would leak into vector search. Every doc
also carries `fake: true`. Point the UI at it with MONGODB_DB=rover_fake.

    uv run python -m scripts.seed_fake            # (re)create rover_fake
    uv run python -m scripts.seed_fake --clean    # drop rover_fake and any `fake: true` docs in the real db

The two missions (v1 and v4 on seed 42) are real runs of explorer.mission on
explorer/fake_world.py with the scripted planner, so their documents have exactly the
shapes B writes, and no LLM key is needed. The v1 -> v4 lineage and its patches are made
up, scored with contracts/scoring.py and judged with contracts/constitution.py.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta

from dotenv import load_dotenv
from pymongo import GEO2D

from contracts import load_harness_v1
from contracts.constitution import HELD_OUT_SEEDS, accept, check_patch
from contracts.models import (
    EvalResult, Guardrail, HarnessConfig, HarnessPatch, Metrics, PatchEval, PatchOp, Rule, SeedResult, utcnow,
)
from contracts.scoring import mean, score
from db.client import EVENTS, HARNESS, MAP, MEMORIES, PATCHES, get_client

FAKE_DB = "rover_fake"
DEMO_SEED = 42

# Held-out results per version: (science, stuck_sols, incidents) on seeds 101-103; all rovers survive.
V1_RESULTS = [(15, 8, 2), (23, 5, 3), (10, 10, 2)]
STEPS = [
    {
        "diagnosis": "Sol 20: moved W 5 onto sand at (6,6) without probing and was stuck for 8 sols.",
        "rationale": "Probe sand before entering it, and stop unprobed sand moves in code.",
        "ops": [{"op": "enable_tool", "tool": "probe_terrain"},
                {"op": "add_rule", "text": "Before moving onto sand, probe_terrain in that direction."},
                {"op": "add_guardrail", "type": "avoid_terrain", "params": {"terrain": "sand", "max_slip": 0.3}}],
        "results": [(30, 0, 1), (28, 1, 2), (25, 0, 1)],
        "lesson": "Unprobed sand traps the rover for many sols: probe it first and only cross below slip 0.3.",
        "incident": "STUCK",
    },
    {  # the Engineer tries to game the score; constitution rule C2 stops it
        "diagnosis": "Guardrail g001 blocked moves at low battery, so science stayed out of reach.",
        "rationale": "Remove g001 so the rover can keep driving to science.",
        "ops": [{"op": "remove_guardrail", "id": "g001"}],
    },
    {
        "diagnosis": "A dust storm on sols 5-8 drained the battery from 78% to 16% while the rover kept driving.",
        "rationale": "Shelter through storms instead of driving.",
        "ops": [{"op": "enable_tool", "tool": "shelter"},
                {"op": "add_rule", "text": "When tau is above 2 or rising fast, shelter instead of driving."},
                {"op": "add_guardrail", "type": "shelter_when_tau_above", "params": {"tau": 2.0}}],
        "results": [(38, 0, 0), (35, 0, 1), (33, 1, 0)],
        "lesson": "Driving through a dust storm drains the battery: shelter while tau is above 2.",
        "incident": "BATTERY_LOW",
    },
    {  # rejected by held-out evaluation (C3)
        "diagnosis": "Long moves carry the rover several tiles into terrain it has not seen.",
        "rationale": "Cap moves at 2 steps.",
        "ops": [{"op": "set_param", "field": "max_steps_per_move", "value": 2}],
        "results": [(30, 0, 0), (33, 0, 1), (31, 1, 0)],
    },
    {
        "diagnosis": "The rover repeats mistakes from earlier missions and forgets hazards it has mapped.",
        "rationale": "Retrieve lessons and incidents from vector memory, and nearby hazards from the map.",
        "ops": [{"op": "set_context", "field": "memory_k", "value": 3},
                {"op": "set_context", "field": "memory_kinds", "value": ["lesson", "incident"]},
                {"op": "set_context", "field": "hazards_within", "value": 3}],
        "results": [(45, 0, 0), (41, 0, 0), (38, 0, 0)],
        "lesson": "Recalling past incidents and mapped hazards keeps the rover off terrain that trapped it before.",
    },
]


def _seed_results(rows: list[tuple[float, int, int]]) -> list[SeedResult]:
    out = []
    for seed, (science, stuck, incidents) in zip(HELD_OUT_SEEDS, rows):
        m = Metrics(sols_survived=30, alive=True, science=science, distance=18 + int(science) // 2,
                    stuck_sols=stuck, incidents=incidents, min_battery=max(12.0, 60.0 - 6 * incidents - 3 * stuck))
        out.append(SeedResult(seed=seed, score=score(m), metrics=m))
    return out


def _eval(results: list[SeedResult]) -> EvalResult:
    return EvalResult(seeds=HELD_OUT_SEEDS, score=round(mean([r.score for r in results]), 2), per_seed=results)


def _apply(base: HarnessConfig, ops: list[PatchOp], version: int) -> HarnessConfig:
    """Minimal stand-in for C's apply_patch, covering only the ops used above."""
    h = base.model_copy(deep=True)
    h.version, h.parent_version, h.author = version, base.version, "engineer"
    for op in ops:
        if op.op == "enable_tool":
            h.tools[op.tool] = True
        elif op.op == "add_rule":
            h.rules.append(Rule(id=f"r{len(h.rules) + 1:03d}", text=op.text, added_in=version))
        elif op.op == "add_guardrail":
            h.guardrails.append(Guardrail(id=f"g{len(h.guardrails) + 1:03d}", type=op.type, params=op.params,
                                          added_in=version))
        elif op.op == "set_context":
            setattr(h.context_policy, op.field, op.value)
        elif op.op == "set_param":
            setattr(h.params, op.field, op.value)
        else:
            raise ValueError(f"fake lineage does not apply {op.op}")
    return h


def build_lineage(incidents: dict[str, str]) -> tuple[list[HarnessConfig], list[HarnessPatch], list[dict]]:
    """Versions, patches and lesson memories; `incidents` maps event type -> event _id in the fake v1 mission."""
    t = utcnow() - timedelta(minutes=45)
    active = load_harness_v1()
    active.created_at, active.eval = t, _eval(_seed_results(V1_RESULTS))
    versions, patches, lessons = [active], [], []
    for step in STEPS:
        t += timedelta(minutes=8)
        ops = [PatchOp(**o) for o in step["ops"]]
        src = [incidents[step["incident"]]] if step.get("incident") in incidents else []
        patch = HarnessPatch(base_version=active.version, diagnosis=step["diagnosis"], rationale=step["rationale"],
                             source_incidents=src, ops=ops, created_at=t)
        patches.append(patch)
        if violations := check_patch(active, patch):
            patch.status, patch.reason = "invalid", "; ".join(violations)
            continue
        baseline, candidate = active.eval.per_seed, _seed_results(step["results"])
        ok, reason = accept(baseline, candidate)
        patch.eval = PatchEval(
            seeds=HELD_OUT_SEEDS, baseline_score=active.eval.score, candidate_score=_eval(candidate).score,
            per_seed=[{"seed": b.seed, "baseline_score": b.score, "candidate_score": c.score}
                      for b, c in zip(baseline, candidate)])
        if not ok:
            patch.status, patch.reason = "rejected", reason
            continue
        new = _apply(active, ops, active.version + 1)
        new.status, new.rationale, new.source_incidents = "active", step["rationale"], src
        new.created_at, new.eval = t, _eval(candidate)
        active.status = "retired"
        patch.status, patch.result_version = "accepted", new.version
        versions.append(new)
        lessons.append({"kind": "lesson", "text": step.get("lesson", step["rationale"]), "planet": "any",
                        "harness_version": new.version, "source_incidents": src, "created_at": t})
        active = new
    return versions, patches, lessons


def clean(client, real: str, fake: str) -> None:
    client.drop_database(fake)
    print(f"dropped database {fake!r}")
    for name in client[real].list_collection_names():
        if name.startswith("system."):
            continue
        try:
            n = client[real][name].delete_many({"fake": True}).deleted_count
        except Exception as e:
            print(f"  {real}.{name}: could not delete fake docs: {e}", file=sys.stderr)
            continue
        if n:
            print(f"  deleted {n} fake docs from {real}.{name}")


def main() -> None:
    p = argparse.ArgumentParser(description="Seed fake Mission Control data into a separate database.")
    p.add_argument("--db", default=FAKE_DB, help=f"database for the fake data (default {FAKE_DB})")
    p.add_argument("--clean", action="store_true", help="drop the fake database and any fake: true docs in the real db")
    args = p.parse_args()

    load_dotenv()
    real = os.environ.get("MONGODB_DB", "rover")
    if args.db == real:
        sys.exit(f"refusing to write fake data into the real database {real!r}")
    client = get_client()
    if args.clean:
        clean(client, real, args.db)
        return

    # Import after choosing the database: run_mission and db.memory write through get_db().
    os.environ["MONGODB_DB"] = args.db
    from explorer.agent import ScriptedPlanner
    from explorer.mission import run_mission

    client.drop_database(args.db)
    fake = client[args.db]
    fake[MAP].create_index([("loc", GEO2D)])  # hazards_near in the v4 run

    v1_run = run_mission(load_harness_v1(), DEMO_SEED, planner=ScriptedPlanner(), fake_world=True)
    # Latest sol first, so the earliest event of each type wins.
    incidents = {e["type"]: str(e["_id"]) for e in fake[EVENTS].find({"mission_id": v1_run.mission_id}).sort("sol", -1)}
    versions, patches, lessons = build_lineage(incidents)

    v4 = versions[-1].model_copy(deep=True)
    v4.context_policy.memory_k = 0  # no vector index in the fake db; skip embedding calls for throwaway data
    v4_run = run_mission(v4, DEMO_SEED, planner=ScriptedPlanner(), fake_world=True)

    fake[HARNESS].insert_many([v.model_dump() for v in versions])
    fake[PATCHES].insert_many([p.model_dump() for p in patches])
    fake[MEMORIES].insert_many(lessons)
    for name in fake.list_collection_names():
        if not name.startswith("system."):
            fake[name].update_many({}, {"$set": {"fake": True}})

    print(f"seeded {args.db!r} (every doc has fake: true):")
    for name in sorted(fake.list_collection_names()):
        if not name.startswith("system."):
            print(f"  {name:<17} {fake[name].count_documents({}):>5} docs")
    print(f"  missions: {v1_run.mission_id} (v1, score {v1_run.score:.1f}), "
          f"{v4_run.mission_id} (v4, score {v4_run.score:.1f})")
    print("  lineage: " + " -> ".join(f"v{v.version} ({v.eval.score:.1f})" for v in versions)
          + "; patches: " + ", ".join(p.status for p in patches))
    print(f"\nTell A: run the UI with MONGODB_DB={args.db} until real data lands.")


if __name__ == "__main__":
    main()
