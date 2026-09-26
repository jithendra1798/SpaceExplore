from concurrent.futures import ThreadPoolExecutor

from contracts import load_harness_v1
from contracts.models import Action, Guardrail, Observation, TileView
from explorer.agent import ScriptedPlanner
from explorer.guardrails import enforce
from explorer.mission import run_mission


def harness_v2():
    h = load_harness_v1()
    h.tools["probe_terrain"] = h.tools["shelter"] = True
    h.guardrails += [
        Guardrail(id="g002", type="avoid_terrain", params={"terrain": "sand", "max_slip": 0.3}, added_in=2),
        Guardrail(id="g003", type="shelter_when_tau_above", params={"tau": 2.0}, added_in=2),
    ]
    return h


def obs(pos, tau=0.5, battery=80.0):
    return Observation(sol=3, pos=pos, battery=battery, wheel_health=1, panel_dust=0, stuck=False, tau=tau,
                       tau_trend=0, local_tiles=[TileView(x=6, y=6, terrain="sand")])


def cost(a: Action) -> float:
    return {"move": 2.0 * a.args.get("steps", 1), "drill": 6.0}.get(a.tool, 1.0)


def move(direction, steps):
    return Action(tool="move", args={"direction": direction, "steps": steps})


KNOWN = {(6, 6): "sand"}


def test_unprobed_sand_is_blocked():
    r = enforce(harness_v2(), obs((5, 6)), [move("E", 3)], cost, KNOWN, {})
    assert r.allowed == [] and r.blocks[0].guardrail_id == "g002"


def test_move_is_clipped_before_sand():
    r = enforce(harness_v2(), obs((4, 6)), [move("E", 3)], cost, KNOWN, {})
    assert r.allowed == [move("E", 1)]


def test_probed_safe_sand_is_allowed():
    r = enforce(harness_v2(), obs((5, 6)), [move("E", 3)], cost, KNOWN, {(6, 6): 0.2})
    assert r.allowed == [move("E", 3)]


def test_storm_forces_shelter():
    r = enforce(harness_v2(), obs((5, 6), tau=3.5), [move("E", 1)], cost, KNOWN, {})
    assert [a.tool for a in r.allowed] == ["shelter"]


def test_constitution_battery_floor():
    r = enforce(harness_v2(), obs((5, 6), battery=10), [Action(tool="drill")], cost, KNOWN, {})
    assert r.allowed == [] and r.blocks[0].guardrail_id == "C1"


def test_disabled_tool_is_blocked():
    r = enforce(load_harness_v1(), obs((5, 6)), [Action(tool="shelter")], cost, KNOWN, {})
    assert r.allowed == [] and r.blocks[0].guardrail_id == "tools"


def test_parallel_eval_missions():
    h = harness_v2()
    with ThreadPoolExecutor(3) as ex:
        results = list(ex.map(lambda s: run_mission(h, s, max_sols=15, mode="eval", log_to_db=False,
                                                    planner=ScriptedPlanner(), fake_world=True), [101, 102, 103]))
    assert len({r.mission_id for r in results}) == 3
    assert all(r.metrics.sols_survived > 0 for r in results)
