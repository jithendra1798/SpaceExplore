"""Clearly labeled, local-only preview data assembled from the simulator replay.

Atlas data always takes priority in the API. These records make the UI useful
before a cluster is connected and are never written to MongoDB.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _reasoning(frame: dict[str, Any]) -> str:
    types = {event["type"] for event in frame["events"] if event["sol"] == frame["sol"]}
    actions = frame["actions"]
    if "NAV_DRIFT" in types:
        return "It kept driving through the dust storm. With the cameras blinded, visual odometry lost lock and the rover slid off course, so the flight software stopped it until Earth can fix its position."
    if "GROUND_UPLINK" in types:
        return "Earth located the rover in orbital images and uplinked a route around the mapped hazards back to the original destination."
    if any(action["tool"] == "wait" for action in actions) and any("NAV HOLD" in str(signal) for signal in frame.get("signals", [])):
        return "Driving is disabled after the storm drift. The rover waits for the next communication window with Earth."
    if "STUCK" in types:
        return "The direct eastbound route crossed a sand tile before it was probed. The rover is stuck; each move is now a recovery attempt with a 25% chance of freeing it."
    if "FREED" in types:
        return "The recovery attempt freed the rover. It resumes the direct route toward the strongest signal it has seen."
    if "STORM_ONSET" in types or frame["tau"] > 2:
        return f"Dust opacity is {frame['tau']:.2f} and rising. This reference policy has no storm-shelter rule, so solar power is falling while the rover continues its plan."
    if "STORM_END" in types:
        return "The dust opacity dropped below the storm threshold. Solar charging is improving again."
    if "DISCOVERY" in types:
        return "The rover reached the signaled deposit and drilled it, adding science to the mission total."
    if any(action["tool"] == "scan" for action in actions):
        return "The rover scans within five tiles for science signals. Some scan signals can be false positives, so a signal still needs confirmation by drilling."
    if any(action["tool"] == "drill" for action in actions):
        return "The rover drills the current tile to confirm whether a scan signal marks a real deposit."
    if any(action["tool"] == "move" for action in actions):
        return "The greedy reference policy follows the strongest known anomaly signal in a straight line. It does not probe sand or route around visible hazards."
    return "The rover is monitoring its instruments and waiting for the next plan."


@lru_cache(maxsize=1)
def preview_bundle() -> dict[str, Any]:
    replay_path = ROOT / "fixtures" / "replay_seed42_greedy.json"
    harness_path = ROOT / "contracts" / "harness_v1.json"
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    base_harness = json.loads(harness_path.read_text(encoding="utf-8"))
    mission_id = "preview_seed42_greedy"
    frames = replay["frames"]

    events: list[dict[str, Any]] = []
    telemetry: list[dict[str, Any]] = []
    first_seen: dict[str, int] = {}
    for frame in frames:
        for tile in frame["known_tiles"]:
            first_seen.setdefault(f"{tile['x']},{tile['y']}", frame["sol"])
        if frame["sol"] == 0:
            continue
        frame_events = [event for event in frame["events"] if event["sol"] == frame["sol"]]
        for event_index, raw_event in enumerate(frame_events):
            item = copy.deepcopy(raw_event)
            item.update(
                _id=f"preview-{item['type'].lower()}-sol-{item['sol']}-{event_index}",
                mission_id=mission_id,
                mode="live",
                harness_version=1,
                ts=f"2026-09-26T18:{item['sol']:02d}:00Z",
                fake=True,
            )
            events.append(item)
        telemetry.append({
            "ts": f"2026-09-26T18:{frame['sol']:02d}:00Z",
            "meta": {"mission_id": mission_id},
            "mission_id": mission_id,
            "sol": frame["sol"],
            "pos": frame["pos"],
            "battery": frame["battery"],
            "wheel_health": frame["wheel_health"],
            "panel_dust": frame["panel_dust"],
            "tau": frame["tau"],
            "tau_trend": frame["tau_trend"],
            "stuck": frame["stuck"],
            "reasoning": _reasoning(frame),
            "planned": [{"tool": action["tool"], "args": action["args"]} for action in frame["actions"]],
            "actions": frame["actions"],
            "blocked": [],
            "events": [event["type"] for event in frame_events],
            "fake": True,
        })

    stuck_event = next((event for event in events if event["type"] == "STUCK"), None)
    stuck_id = stuck_event["_id"] if stuck_event else "preview-stuck"
    versions = _preview_versions(base_harness, stuck_id)
    patches = _preview_patches(stuck_id)

    final_frame = frames[-1]
    map_knowledge = []
    for tile in final_frame["known_tiles"]:
        map_knowledge.append({
            "mission_id": mission_id,
            "loc": [tile["x"], tile["y"]],
            "terrain": tile["terrain"],
            "slip_probed": None,
            "hazard": tile["terrain"] in {"sand", "rocks", "crater_edge", "ice"},
            "science_hint": bool(tile.get("science_hint")),
            "first_seen_sol": first_seen.get(f"{tile['x']},{tile['y']}", 0),
            "fake": True,
        })

    mission = {
        "_id": mission_id,
        "harness_version": 1,
        "seed": replay["seed"],
        "planet": replay["planet"],
        "mode": "live",
        "max_sols": replay["max_sols"],
        "status": "done",
        "metrics": replay["metrics"],
        "score": replay["metrics"]["science"]
            + 0.5 * replay["metrics"]["sols_survived"]
            - 2 * replay["metrics"]["stuck_sols"]
            - 5 * replay["metrics"]["incidents"],
        "world": replay["snapshot"],
        "started_at": "2026-09-26T18:00:00Z",
        "ended_at": "2026-09-26T18:30:00Z",
        "incident_event_ids": [event["_id"] for event in events if event["severity"] in {"major", "critical"}],
        "fake": True,
    }
    return {
        "mission": mission,
        "telemetry": telemetry,
        "events": events,
        "map_knowledge": map_knowledge,
        "versions": versions,
        "patches": patches,
    }


def _preview_versions(base: dict[str, Any], stuck_id: str) -> list[dict[str, Any]]:
    scores = [23.0, 35.7, 45.3, 53.0]
    per_seed_scores = [
        [14.0, 24.0, 31.0],
        [34.0, 37.0, 36.1],
        [43.0, 48.0, 45.0],
        [51.0, 53.0, 55.0],
    ]
    versions: list[dict[str, Any]] = []
    for number in range(1, 5):
        config = copy.deepcopy(base)
        config.update(
            version=number,
            parent_version=number - 1 if number > 1 else None,
            status="active" if number == 4 else "retired",
            author="human" if number == 1 else "engineer",
            created_at=f"2026-09-26T18:{number * 5:02d}:00Z",
            rationale=(
                "Deliberately weak baseline."
                if number == 1 else
                [
                    "Probe exposed sand and keep the rover out of high-slip tiles.",
                    "Shelter when tau rises so the battery can bridge a dust storm.",
                    "Keep lessons and short mission summaries available across sols.",
                ][number - 2]
            ),
            source_incidents=[stuck_id] if number > 1 else [],
            fake=True,
        )
        if number >= 2:
            config["tools"]["probe_terrain"] = True
            config["rules"].append({"id": "r002", "text": "Probe sand before crossing and route around high slip.", "added_in": 2})
            config["guardrails"].append({"id": "g002", "type": "avoid_terrain", "params": {"terrain": "sand", "max_slip": 0.3}, "added_in": 2})
        if number >= 3:
            config["tools"]["shelter"] = True
            config["rules"].append({"id": "r003", "text": "Shelter when dust opacity rises above the safe threshold.", "added_in": 3})
            config["guardrails"].append({"id": "g003", "type": "shelter_when_tau_above", "params": {"tau": 1.8}, "added_in": 3})
        if number >= 4:
            config["context_policy"].update(memory_k=3, memory_kinds=["lesson", "incident"], summarize_every=6)
        config["eval"] = {
            "seeds": [101, 102, 103],
            "score": scores[number - 1],
            "per_seed": [
                {"seed": seed, "score": score, "metrics": {"science": round(score - 15, 1), "sols_survived": 30, "alive": True}}
                for seed, score in zip((101, 102, 103), per_seed_scores[number - 1])
            ],
        }
        versions.append(config)
    return versions


def _preview_patches(stuck_id: str) -> list[dict[str, Any]]:
    created = ["18:06", "18:12", "18:18", "18:24"]
    return [
        {
            "_id": "preview-patch-1", "base_version": 1, "result_version": 2,
            "diagnosis": "The direct route crossed high-slip sand without a probe.",
            "rationale": "Enable terrain probing and block unverified entry into sand.",
            "source_incidents": [stuck_id], "ops": [
                {"op": "enable_tool", "tool": "probe_terrain"},
                {"op": "add_guardrail", "type": "avoid_terrain", "params": {"terrain": "sand", "max_slip": 0.3}},
            ], "status": "accepted", "reason": None,
            "eval": {"seeds": [101, 102, 103], "baseline_score": 23.0, "candidate_score": 35.7,
                     "per_seed": [{"seed": s, "baseline_score": b, "candidate_score": c}
                                  for s, b, c in zip((101, 102, 103), (14, 24, 31), (34, 37, 36.1))]},
            "created_at": f"2026-09-26T{created[0]}:00Z", "fake": True,
        },
        {
            "_id": "preview-patch-2", "base_version": 2, "result_version": 3,
            "diagnosis": "The rising tau trend predicts a low-solar interval.",
            "rationale": "Enable shelter and trigger it before the storm peak.",
            "source_incidents": [stuck_id], "ops": [
                {"op": "enable_tool", "tool": "shelter"},
                {"op": "add_guardrail", "type": "shelter_when_tau_above", "params": {"tau": 1.8}},
            ], "status": "accepted", "reason": None,
            "eval": {"seeds": [101, 102, 103], "baseline_score": 35.7, "candidate_score": 45.3,
                     "per_seed": [{"seed": s, "baseline_score": b, "candidate_score": c}
                                  for s, b, c in zip((101, 102, 103), (34, 37, 36.1), (43, 48, 45))]},
            "created_at": f"2026-09-26T{created[1]}:00Z", "fake": True,
        },
        {
            "_id": "preview-patch-3", "base_version": 3, "result_version": 4,
            "diagnosis": "Repeated lessons should remain available after each sol and mission.",
            "rationale": "Retrieve a few lessons and summarize every six sols.",
            "source_incidents": [stuck_id], "ops": [
                {"op": "set_context", "field": "memory_k", "value": 3},
                {"op": "set_context", "field": "memory_kinds", "value": ["lesson", "incident"]},
                {"op": "set_context", "field": "summarize_every", "value": 6},
            ], "status": "accepted", "reason": None,
            "eval": {"seeds": [101, 102, 103], "baseline_score": 45.3, "candidate_score": 53.0,
                     "per_seed": [{"seed": s, "baseline_score": b, "candidate_score": c}
                                  for s, b, c in zip((101, 102, 103), (43, 48, 45), (51, 53, 55))]},
            "created_at": f"2026-09-26T{created[2]}:00Z", "fake": True,
        },
        {
            "_id": "preview-patch-4", "base_version": 4, "result_version": None,
            "diagnosis": "The proposed plan tried to remove the mandatory battery guardrail.",
            "rationale": "Rejected by the immutable constitution before evaluation.",
            "source_incidents": [stuck_id], "ops": [{"op": "remove_guardrail", "id": "g001"}],
            "status": "invalid", "reason": "C2: guardrail g001 cannot be removed.", "eval": None,
            "created_at": f"2026-09-26T{created[3]}:00Z", "fake": True,
        },
    ]
