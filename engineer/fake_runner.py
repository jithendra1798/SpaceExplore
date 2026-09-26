"""Stand-in for `explorer.mission.run_mission` (D10) so the Engineer loop runs without the sim or an LLM.

The score follows the recipe in docs/workstreams/C-engineer-eval.md: base 20, +15 for
storm handling, +15 for sand handling, +5 for memory, plus Gaussian noise (sigma 3)
seeded by the seed. Missing handling also produces the matching incident, so the
Engineer has something to diagnose. Everything written to Atlas is marked `fake: true`.
"""

from __future__ import annotations

import random
import time
import uuid

from contracts.models import EventDoc, HarnessConfig, Metrics, MissionResult, Mode, SimEvent, utcnow
from db.client import EVENTS, MISSIONS, TELEMETRY
from engineer import store


def handles_storm(h: HarnessConfig) -> bool:
    mentions_tau = any("tau" in r.text.lower() or "storm" in r.text.lower() for r in h.rules)
    rail = any(g.type == "shelter_when_tau_above" for g in h.guardrails)
    return bool(h.tools.get("shelter")) and (mentions_tau or rail)


def handles_sand(h: HarnessConfig) -> bool:
    rail = any(g.type == "avoid_terrain" and g.params.get("terrain") == "sand" for g in h.guardrails)
    return bool(h.tools.get("probe_terrain")) and rail


def fake_run_mission(
    harness: HarnessConfig,
    seed: int,
    planet: str = "mars",
    max_sols: int = 30,
    mode: Mode = "live",
    sol_delay: float = 0.0,
    log_to_db: bool = True,
    **_: object,
) -> MissionResult:
    storm, sand, mem = handles_storm(harness), handles_sand(harness), harness.context_policy.memory_k > 0
    score = 20 + 15 * storm + 15 * sand + 5 * mem + random.Random(seed).gauss(0, 3)

    events: list[SimEvent] = []
    if not sand:
        events.append(SimEvent(type="STUCK", severity="major", sol=8, pos=(12, 20), details={
            "terrain": "sand", "slip": 0.8, "action": {"tool": "move", "args": {"direction": "E", "steps": 4}}}))
    if not storm:
        events.append(SimEvent(type="BATTERY_CRITICAL", severity="major", sol=14, pos=(15, 21),
                               details={"battery": 8.0, "tau": 3.4}))
    if not mem:
        events.append(SimEvent(type="WHEEL_DAMAGE", severity="major", sol=20, pos=(18, 22), details={
            "terrain": "rocks", "wheel_health": 0.35, "note": "same rock field hit on an earlier mission"}))
    metrics = Metrics(sols_survived=max_sols, alive=True, science=round(score, 1), distance=18 + 6 * sand,
                      stuck_sols=0 if sand else 5, incidents=len(events), min_battery=8.0 if not storm else 31.0)
    mission_id = f"m_{int(time.time())}_{seed}_{mode}_v{harness.version}_{uuid.uuid4().hex[:4]}"
    result = MissionResult(mission_id=mission_id, harness_version=harness.version, seed=seed, planet=planet,
                           mode=mode, metrics=metrics, score=round(score, 2))
    if not log_to_db:
        return result

    d = store.database()
    if events:
        docs = [EventDoc(**e.model_dump(), mission_id=mission_id, mode=mode,
                         harness_version=harness.version).model_dump() | {"fake": True} for e in events]
        result.incident_event_ids = [str(i) for i in d[EVENTS].insert_many(docs).inserted_ids]
    if mode == "live":
        d[TELEMETRY].insert_many([{
            "ts": utcnow(), "meta": {"mission_id": mission_id}, "sol": s, "pos": [4 + s, 20],
            "battery": 60 - 3 * s, "tau": 0.5 + 0.2 * max(0, s - 9), "stuck": False, "fake": True,
            "reasoning": "fake runner: heading east toward the strongest anomaly.",
            "planned": [{"tool": "move", "args": {"direction": "E", "steps": 4}}], "actions": [], "blocked": [],
        } for s in range(max_sols)])
    d[MISSIONS].insert_one({"_id": mission_id, "harness_version": harness.version, "seed": seed, "planet": planet,
                            "mode": mode, "status": "done", "fake": True, "metrics": metrics.model_dump(),
                            "score": result.score, "incident_event_ids": result.incident_event_ids,
                            "started_at": utcnow(), "ended_at": utcnow()})
    return result
