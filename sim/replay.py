"""Export a deterministic policy run for the static map preview."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from sim.models import model_dump
from sim.policies import run_policy
from sim.world import World


def make_replay(seed: int = 42, policy: str = "greedy", planet: str = "mars", sols: int = 30) -> dict[str, Any]:
    initial_world = World(seed=seed, planet=planet)
    snapshot = initial_world.snapshot()
    initial_observation = initial_world.observe()
    run = run_policy(seed=seed, policy=policy, planet=planet, max_sols=sols)

    known: dict[str, dict[str, Any]] = {}
    path = [[initial_observation.pos[0], initial_observation.pos[1]]]
    accumulated_events: list[dict[str, Any]] = []

    def remember(tiles: list[Any]) -> None:
        for tile in tiles:
            known[f"{tile.x},{tile.y}"] = {"x": tile.x, "y": tile.y, "terrain": tile.terrain}

    remember(initial_observation.local_tiles)
    frames: list[dict[str, Any]] = [{
        "sol": 0,
        "pos": list(initial_observation.pos),
        "battery": initial_observation.battery,
        "wheel_health": initial_observation.wheel_health,
        "panel_dust": initial_observation.panel_dust,
        "tau": initial_observation.tau,
        "tau_trend": initial_observation.tau_trend,
        "stuck": initial_observation.stuck,
        "path": [list(point) for point in path],
        "known_tiles": list(known.values()),
        "events": [],
        "actions": [],
        "signals": [],
    }]

    for result in run.results:
        observation = result.observation
        remember(observation.local_tiles)
        for event in result.events:
            event_data = model_dump(event)
            event_data["pos"] = list(event.pos)
            accumulated_events.append(event_data)
            if event.type == "DISCOVERY":
                key = f"{event.pos[0]},{event.pos[1]}"
                if key in known:
                    known[key]["science_hint"] = True

        # Keep every tile driven through, so drifts and detours show their shape.
        for outcome in result.executed:
            for point in outcome.data.get("trail", []) or []:
                if list(point) != path[-1]:
                    path.append(list(point))
        pos = [observation.pos[0], observation.pos[1]]
        if pos != path[-1]:
            path.append(pos)
        actions = [
            {
                "tool": str(outcome.action.tool),
                "args": dict(outcome.action.args),
                "ok": outcome.ok,
                "message": outcome.message,
            }
            for outcome in result.executed
        ]
        frames.append({
            "sol": result.sol,
            "pos": pos,
            "battery": observation.battery,
            "wheel_health": observation.wheel_health,
            "panel_dust": observation.panel_dust,
            "tau": observation.tau,
            "tau_trend": observation.tau_trend,
            "stuck": observation.stuck,
            "path": [list(point) for point in path],
            "known_tiles": list(known.values()),
            "events": list(accumulated_events),
            "actions": actions,
            "signals": list(observation.signals),
        })

    return {
        "seed": seed,
        "planet": planet,
        "policy": policy,
        "max_sols": sols,
        "snapshot": snapshot,
        "frames": frames,
        "metrics": model_dump(run.metrics),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--planet", choices=("mars", "icy"), default="mars")
    parser.add_argument("--policy", choices=("random", "greedy", "careful"), default="greedy")
    parser.add_argument("--sols", type=int, default=30)
    parser.add_argument("--output", type=Path, default=Path("fixtures/replay_seed42_greedy.json"))
    args = parser.parse_args(argv)

    replay = make_replay(args.seed, args.policy, args.planet, args.sols)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(replay, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(replay['frames'])} replay frames to {args.output}")
    print(f"Final metrics: {replay['metrics']}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
