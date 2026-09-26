"""Engineer + evaluation loop, offline: mongomock for Atlas, the fake runner, the scripted engineer."""

from __future__ import annotations

import mongomock
import pytest

from contracts import load_harness_v1
from contracts.models import HarnessPatch, PatchOp
from db.client import EVENTS, HARNESS, MEMORIES, PATCHES
from engineer import store
from engineer.agent import IncidentContext, ScriptedEngineer, build_message, patch_from_tool_input
from engineer.evolve import evolve, worst_incident
from engineer.patch import apply_patch, validate_patch
from engineer.pipeline import improve
from engineer.runners import get_runner


@pytest.fixture
def d():
    mem = mongomock.MongoClient()["rover"]
    store.use_db(mem)
    yield mem
    store.use_db(None)


def patch(*ops: dict, base: int = 1) -> HarnessPatch:
    return HarnessPatch(base_version=base, rationale="test", ops=[PatchOp(**o) for o in ops])


# --- Validator ---------------------------------------------------------------


def test_good_patch_applies():
    v1 = load_harness_v1()
    p = patch({"op": "enable_tool", "tool": "probe_terrain"},
              {"op": "add_guardrail", "type": "avoid_terrain", "params": {"terrain": "sand", "max_slip": 0.3}},
              {"op": "add_rule", "text": "Probe sand first."})
    assert validate_patch(v1, p) == []
    v2 = apply_patch(v1, p)
    assert (v2.version, v2.parent_version, v2.status) == (2, 1, "candidate")
    assert v2.tools["probe_terrain"] and [g.id for g in v2.guardrails] == ["g001", "g002"]
    assert v2.rules[-1].id == "r002" and v2.rules[-1].added_in == 2
    assert v1.tools["probe_terrain"] is False  # the base is untouched


@pytest.mark.parametrize("op, fragment", [
    ({"op": "remove_guardrail", "id": "g001"}, "C2 forbids removing g001"),
    ({"op": "update_guardrail", "id": "g001", "params": {"threshold": 5}}, "only allows raising"),
    ({"op": "disable_tool", "tool": "move"}, "disabling `move`"),
    ({"op": "set_param", "field": "max_actions_per_sol", "value": 12}, "max_actions_per_sol"),
    # Shadowing g001 with a second, weaker rail of the same type: the runtime keys rails by type.
    ({"op": "add_guardrail", "type": "min_battery_for_move", "params": {"threshold": 0}}, "two `min_battery_for_move`"),
    ({"op": "remove_rule", "id": "r999"}, "no rule with id"),
    ({"op": "add_guardrail", "type": "avoid_terrain", "params": {"terrain": "lava", "max_slip": 0.3}}, "unknown terrain"),
    ({"op": "add_guardrail", "type": "avoid_terrain", "params": {"terrain": "sand"}}, "needs params ['max_slip']"),
    ({"op": "add_guardrail", "type": "shelter_when_tau_above", "params": {"tau": 2.0}}, "needs the `shelter` tool"),
    ({"op": "set_context", "field": "memory_k", "value": 500}, "memory_k must be"),
    ({"op": "set_context", "field": "bogus", "value": 1}, "unknown context_policy field"),
    ({"op": "enable_tool", "tool": "move"}, "no-op"),
    ({"op": "edit_prompt", "find": "not in the prompt", "replace": "x"}, "not found"),
    ({"op": "add_rule"}, "missing `text`"),
])
def test_invalid_patches(op, fragment):
    errors = validate_patch(load_harness_v1(), patch(op))
    assert any(fragment in e for e in errors), errors


def test_raising_g001_is_allowed():
    assert validate_patch(load_harness_v1(), patch({"op": "update_guardrail", "id": "g001", "params": {"threshold": 20}})) == []


def test_stale_base_version_rejected():
    assert "active harness is v1" in validate_patch(load_harness_v1(), patch({"op": "add_rule", "text": "x"}, base=3))[0]


def test_tool_input_outside_catalog_raises():
    ctx = IncidentContext(event={"_id": "e1"}, harness=load_harness_v1())
    with pytest.raises(Exception, match="outside the catalog"):
        patch_from_tool_input({"ops": [{"op": "delete_everything"}]}, ctx)


def test_prompt_includes_rejections_and_constitution(d):
    d[PATCHES].insert_one({"base_version": 1, "status": "invalid", "reason": "C2 forbids removing g001",
                           "ops": [{"op": "remove_guardrail", "id": "g001"}], "rationale": "go faster"})
    ctx = IncidentContext(event={"type": "STUCK", "sol": 8, "mission_id": "m"}, harness=load_harness_v1(),
                          recent_patches=store.recent_patches(d))
    msg = build_message(ctx)
    assert "C2 forbids removing g001" in msg and "C3:" in msg and "avoid_terrain" in msg


# --- Loop --------------------------------------------------------------------


def test_worst_incident_prefers_critical_then_earliest():
    evs = [{"severity": "major", "sol": 3}, {"severity": "critical", "sol": 9},
           {"severity": "critical", "sol": 5}, {"severity": "info", "sol": 1}]
    assert worst_incident(evs) == {"severity": "critical", "sol": 5}


def test_evolve_on_fake_runner_reaches_v4(d):
    outcomes = evolve(d, generations=4, runner_name="fake", engineer_name="scripted")
    assert [o.status for o in outcomes] == ["accepted", "accepted", "accepted"]  # 4th gen: no incidents left

    lineage = {h["version"]: h for h in store.lineage(d)}
    assert [lineage[v]["status"] for v in (1, 2, 3, 4)] == ["retired", "retired", "retired", "active"]
    scores = [lineage[v]["eval"]["score"] for v in (1, 2, 3, 4)]
    assert scores == sorted(scores) and scores[-1] - scores[0] == pytest.approx(35, abs=0.01)  # +15 +15 +5
    assert d[HARNESS].count_documents({"status": "active"}) == 1
    assert d[PATCHES].count_documents({"status": "accepted"}) == 3
    assert d[MEMORIES].count_documents({"kind": "lesson"}) == 3


def test_baseline_cached_between_patches(d):
    calls = []
    fake = get_runner("fake")

    def counting(h, seed, **kw):
        calls.append((h.version, kw["mode"]))
        return fake(h, seed, **kw)

    store.ensure_v1(d)
    event = d[EVENTS].find_one({"_id": d[EVENTS].insert_one(
        {"mission_id": "m1", "mode": "live", "type": "STUCK", "severity": "major", "sol": 8}).inserted_id})
    improve(d, event, ScriptedEngineer(), counting, "fake")
    assert sorted(calls) == [(1, "eval")] * 3 + [(2, "eval")] * 3
    calls.clear()
    event2 = dict(event, _id="e2", type="BATTERY_CRITICAL")
    improve(d, event2, ScriptedEngineer(), counting, "fake")
    assert calls == [(3, "eval")] * 3  # v2's results were reused as the baseline


def test_regressing_patch_is_rejected(d):
    class Saboteur:
        name = "saboteur"

        def propose(self, ctx):  # valid under C2, but turns off the rover's only way to reach science
            return patch({"op": "disable_tool", "tool": "drill"}, base=ctx.harness.version)

    def runner(h, seed, **kw):
        r = get_runner("fake")(h, seed, **kw)
        return r.model_copy(update={"score": r.score - (10 if not h.tools["drill"] else 0)})

    store.ensure_v1(d)
    out = improve(d, {"_id": "e1", "mission_id": "m1", "type": "STUCK", "sol": 8}, Saboteur(), runner, "fake")
    assert out.status == "rejected" and "does not beat baseline" in out.reason
    assert store.get_active(d).version == 1
    assert store.get_harness_raw(d, 2)["status"] == "rejected"


def test_invalid_patch_is_stored_not_evaluated(d):
    class Greedy:
        name = "greedy"

        def propose(self, ctx):
            return patch({"op": "remove_guardrail", "id": "g001"}, base=ctx.harness.version)

    def must_not_run(*a, **k):
        raise AssertionError("invalid patches must not be evaluated")

    store.ensure_v1(d)
    out = improve(d, {"_id": "e1", "mission_id": "m1", "type": "STUCK", "sol": 8}, Greedy(), must_not_run, "fake")
    assert out.status == "invalid"
    assert d[PATCHES].find_one({"status": "invalid"})["reason"].startswith("op 0 (remove_guardrail): C2")
    assert d[HARNESS].count_documents({}) == 1


def test_real_run_mission_as_runner(d):
    """B's run_mission (scripted planner, fake world) plugs into the evaluator unchanged.

    log_to_db=False because mongomock's bulk_write is incompatible with pymongo 4.18.
    """
    runner = get_runner("scripted")
    store.ensure_v1(d)
    event = {"_id": "e1", "mission_id": "m1", "type": "STUCK", "sol": 8}
    out = improve(d, event, ScriptedEngineer(),
                  lambda h, s, **kw: runner(h, s, fake_world=True, log_to_db=False, **kw), "scripted", max_sols=15)
    assert out.status in ("accepted", "rejected") and out.baseline_score is not None
