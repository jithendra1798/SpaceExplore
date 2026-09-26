"""Mission runner (CONTRACTS §4): drives one rover through a planet with a given harness."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field

from pymongo import UpdateOne
from pymongo.errors import CollectionInvalid

from contracts.models import (
    INCIDENT_SEVERITIES, Action, EventDoc, HarnessConfig, MissionResult, Mode, Observation, SimEvent, utcnow,
)
from contracts.scoring import score
from db.client import EVENTS, MAP, MISSIONS, TELEMETRY, get_db
from explorer import memory
from explorer.agent import LLMPlanner, ScriptedPlanner
from explorer.context import build_sol_message, build_system_prompt
from explorer.guardrails import enforce

SIGNAL_RE = re.compile(r"\((\d+),\s*(\d+)\)\s*strength\s*([\d.]+)")


def ensure_telemetry(db) -> None:
    """Create `telemetry` as a time-series collection (CONTRACTS §5) if nobody has yet."""
    if db.list_collection_names(filter={"name": TELEMETRY}):
        return
    try:
        db.create_collection(TELEMETRY, timeseries={"timeField": "ts", "metaField": "meta", "granularity": "seconds"})
    except CollectionInvalid:  # another mission created it first
        pass


def make_world(seed: int, planet: str = "mars", fake: bool = False):
    if not fake:
        try:
            from sim.world import World
        except ModuleNotFoundError as e:
            if e.name not in ("sim", "sim.world"):
                raise
        else:
            return World(seed=seed, planet=planet)
    from explorer.fake_world import FakeWorld
    return FakeWorld(seed=seed, planet=planet)


@dataclass
class MissionState:
    known_terrain: dict[tuple[int, int], str] = field(default_factory=dict)
    probed_slip: dict[tuple[int, int], float] = field(default_factory=dict)
    hazards: dict[tuple[int, int], dict] = field(default_factory=dict)
    recent: list[str] = field(default_factory=list)
    last_signals: tuple[int, list[str]] | None = None
    drilled: set[tuple[int, int]] = field(default_factory=set)
    incident_ids: list[str] = field(default_factory=list)

    def see(self, obs: Observation, sol: int) -> list[UpdateOne]:
        ops = []
        for t in obs.local_tiles:
            loc = (t.x, t.y)
            if loc not in self.known_terrain:
                self.known_terrain[loc] = t.terrain
                self.hazards[loc] = {"loc": [t.x, t.y], "terrain": t.terrain, "slip_probed": None,
                                     "hazard": t.terrain in memory.HAZARD_TERRAIN, "first_seen_sol": sol}
                ops.append(loc)
        return ops

    def best_target(self) -> tuple[int, int] | None:
        if not self.last_signals:
            return None
        parsed = []
        for sig in self.last_signals[1]:
            if m := SIGNAL_RE.search(sig):
                x, y, s = m.groups()
                if (int(x), int(y)) not in self.drilled:
                    parsed.append((float(s), (int(x), int(y))))
        return max(parsed)[1] if parsed else None


def _describe(a: Action) -> str:
    args = " ".join(str(v) for v in a.args.values())
    return f"{a.tool} {args}".strip()


def run_mission(
    harness: HarnessConfig,
    seed: int,
    planet: str = "mars",
    max_sols: int = 30,
    mode: Mode = "live",
    sol_delay: float = 0.0,
    log_to_db: bool = True,
    planner: LLMPlanner | ScriptedPlanner | None = None,
    fake_world: bool = False,
    verbose: bool = False,
) -> MissionResult:
    """Thread-safe: no module-level state; Mongo and Anthropic clients are shared and thread-safe."""
    planner = planner or LLMPlanner()
    world = make_world(seed, planet, fake=fake_world)
    db = get_db() if log_to_db else None
    live = mode == "live"
    mission_id = f"m_{int(time.time())}_{seed}_{mode}_v{harness.version}_{uuid.uuid4().hex[:4]}"
    system = build_system_prompt(harness)
    policy = harness.context_policy
    state = MissionState()

    if db is not None:
        if live:
            ensure_telemetry(db)
        db[MISSIONS].insert_one({
            "_id": mission_id, "harness_version": harness.version, "seed": seed, "planet": planet,
            "mode": mode, "max_sols": max_sols, "status": "running", "started_at": utcnow(),
            "world_source": "fake" if type(world).__module__ == "explorer.fake_world" else "sim",
            "world": world.snapshot() if live else None,
        })

    for _ in range(max_sols):
        if not world.alive:
            break
        obs = world.observe()
        new_tiles = state.see(obs, obs.sol)

        situation = (f"tau {obs.tau:.1f} trend {obs.tau_trend:+.1f}, battery {obs.battery:.0f}%, "
                     f"stuck {obs.stuck}, terrain around: {sorted({t.terrain for t in obs.local_tiles})}")
        memories = memory.recall(db, situation, list(policy.memory_kinds), policy.memory_k, planet)
        hazards = memory.hazards_near(db, mission_id, obs.pos, policy.hazards_within, state.hazards)
        message = build_sol_message(obs, state.known_terrain, state.recent[-policy.recent_sols:] if policy.recent_sols else [],
                                    memories, hazards, state.last_signals, state.drilled)

        if isinstance(planner, ScriptedPlanner):
            plan = planner.plan(harness, system, message, obs=obs, target=state.best_target())
        else:
            plan = planner.plan(harness, system, message)
        checked = enforce(harness, obs, plan.actions, world.action_cost, state.known_terrain, state.probed_slip)
        result = world.step(checked.allowed or [Action(tool="wait")])
        after = result.observation

        # Learn from outcomes: probes, scans, newly seen tiles.
        new_tiles += state.see(after, result.sol)
        probed = []
        for o in result.executed:
            if o.action.tool == "probe_terrain" and "tile" in o.data:
                loc = tuple(o.data["tile"])
                state.probed_slip[loc] = o.data["slip"]
                if loc in state.hazards:
                    state.hazards[loc].update(slip_probed=o.data["slip"], hazard=o.data["slip"] > 0.3 or state.hazards[loc]["hazard"])
                probed.append(loc)
            if o.action.tool == "scan" and o.data.get("signals") is not None:
                state.last_signals = (result.sol, o.data["signals"])
            if o.action.tool == "drill":  # drilled here: never point the planner back at this site
                state.drilled.add(tuple(after.pos))

        blocks = [SimEvent(type="GUARDRAIL_BLOCK", severity="minor", sol=result.sol, pos=obs.pos,
                           details={"guardrail_id": b.guardrail_id, "action": b.action, "reason": b.reason,
                                    "rewritten_to": b.rewritten_to}) for b in checked.blocks]
        all_events = result.events + blocks
        outcomes = "; ".join(f"{_describe(o.action)} -> {o.message}" for o in result.executed)
        blocked = "; ".join(f"{b.guardrail_id} blocked {b.action.get('tool', 'plan')}: {b.reason}" for b in checked.blocks)
        state.recent.append(f"Sol {result.sol}: {outcomes or 'no actions'}"
                            + (f" | events: {', '.join(e.type for e in result.events)}" if result.events else "")
                            + (f" | {blocked}" if blocked else ""))

        if db is not None:
            if all_events:
                docs = [EventDoc(**e.model_dump(), mission_id=mission_id, mode=mode,
                                 harness_version=harness.version).model_dump() for e in all_events]
                ids = db[EVENTS].insert_many(docs).inserted_ids
                state.incident_ids += [str(i) for i, e in zip(ids, all_events) if e.severity in INCIDENT_SEVERITIES]
            map_ops = [UpdateOne({"mission_id": mission_id, "loc": list(loc)},
                                 {"$set": {k: v for k, v in state.hazards[loc].items() if k != "first_seen_sol"},
                                  "$setOnInsert": {"first_seen_sol": state.hazards[loc]["first_seen_sol"]}},
                                 upsert=True)
                       for loc in set(new_tiles + probed) if loc in state.hazards]
            if map_ops:
                db[MAP].bulk_write(map_ops, ordered=False)
            if live:
                db[TELEMETRY].insert_one({
                    "ts": utcnow(), "meta": {"mission_id": mission_id}, "sol": result.sol, "pos": list(after.pos),
                    "battery": after.battery, "wheel_health": after.wheel_health, "tau": obs.tau,
                    "stuck": after.stuck, "reasoning": plan.reasoning,
                    "planned": [a.model_dump() for a in plan.actions],
                    "actions": [o.model_dump() for o in result.executed],
                    "blocked": [b.__dict__ for b in checked.blocks],
                    "events": [e.type for e in all_events],
                })
                plan_text = ", ".join(_describe(a) for a in plan.actions)
                for e in result.events:
                    if e.severity in INCIDENT_SEVERITIES:
                        memory.remember(db, "incident",
                                        f"Sol {e.sol}: {e.type} at {e.pos} after plan [{plan_text}]. "
                                        f"Battery {obs.battery:.0f}%, tau {obs.tau:.1f}. Details: {e.details}",
                                        mission_id=mission_id, sol=e.sol, loc=list(e.pos), planet=planet,
                                        harness_version=harness.version)
                    elif e.type == "DISCOVERY":
                        memory.remember(db, "discovery", f"Sol {e.sol}: drilled at {e.pos} and found {e.details}.",
                                        mission_id=mission_id, sol=e.sol, loc=list(e.pos), planet=planet,
                                        harness_version=harness.version)
                if policy.summarize_every and result.sol > 0 and (result.sol + 1) % policy.summarize_every == 0:
                    m = world.metrics()
                    memory.remember(db, "summary",
                                    f"Mission {mission_id} through sol {result.sol}: science {m.science}, "
                                    f"distance {m.distance}, incidents {m.incidents}, stuck sols {m.stuck_sols}, "
                                    f"now at {after.pos} with {after.battery:.0f}% battery. Recent: {' / '.join(state.recent[-3:])}",
                                    mission_id=mission_id, sol=result.sol, loc=list(after.pos), planet=planet,
                                    harness_version=harness.version)

        if verbose:
            print(f"[sol {result.sol:>2}] pos {after.pos} bat {after.battery:5.1f}% tau {obs.tau:.1f} "
                  f"| {outcomes}" + (f" | EVENTS {[e.type for e in result.events]}" if result.events else "")
                  + (f" | BLOCKED {len(checked.blocks)}" if checked.blocks else ""))
            if plan.reasoning:
                print(f"         reasoning: {plan.reasoning[:160]}")
        if sol_delay:
            time.sleep(sol_delay)

    metrics = world.metrics()
    result = MissionResult(mission_id=mission_id, harness_version=harness.version, seed=seed, planet=planet,
                           mode=mode, metrics=metrics, score=score(metrics), incident_event_ids=state.incident_ids)
    if db is not None:
        db[MISSIONS].update_one({"_id": mission_id}, {"$set": {
            "status": "done", "ended_at": utcnow(), "metrics": metrics.model_dump(), "score": result.score,
            "incident_event_ids": state.incident_ids,
        }})
    return result
