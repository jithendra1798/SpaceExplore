"""CLI: run one Explorer mission.

    uv run python -m explorer.run --version active --seed 42 --sols 30 --sol-delay 1
    uv run python -m explorer.run --offline --scripted --fake-world      # no keys, no Atlas
"""

from __future__ import annotations

import argparse
import json
import sys

from dotenv import load_dotenv

from contracts import load_harness_v1
from contracts.models import HarnessConfig
from explorer.agent import LLMPlanner, ScriptedPlanner
from explorer.mission import run_mission


def load_harness(version: str, harness_file: str | None, offline: bool) -> HarnessConfig:
    if harness_file:
        with open(harness_file) as f:
            return HarnessConfig.model_validate(json.load(f))
    if offline:
        return load_harness_v1()
    from db.client import HARNESS, get_db
    query = {"status": "active"} if version == "active" else {"version": int(version)}
    doc = get_db()[HARNESS].find_one(query, sort=[("version", -1)])
    if doc is None:
        print(f"[explorer] harness {version!r} not in Atlas; using contracts/harness_v1.json", file=sys.stderr)
        return load_harness_v1()
    return HarnessConfig.model_validate(doc)


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description="Run one Explorer mission.")
    p.add_argument("--version", default="active", help="'active' or a version number (read from Atlas)")
    p.add_argument("--harness-file", help="load the harness from a JSON file instead of Atlas")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--planet", default="mars")
    p.add_argument("--sols", type=int, default=30)
    p.add_argument("--mode", choices=["live", "eval"], default="live")
    p.add_argument("--sol-delay", type=float, default=0.0, help="seconds between sols (for the live UI)")
    p.add_argument("--offline", action="store_true", help="don't read or write Atlas")
    p.add_argument("--scripted", action="store_true", help="scripted planner instead of Claude (no API key)")
    p.add_argument("--fake-world", action="store_true", help="use explorer.fake_world instead of sim.world")
    p.add_argument("--model", help="override EXPLORER_MODEL")
    args = p.parse_args()

    harness = load_harness(args.version, args.harness_file, args.offline)
    planner = ScriptedPlanner() if args.scripted else LLMPlanner(args.model)
    print(f"[explorer] harness v{harness.version} tools={harness.enabled_tools()} seed={args.seed} mode={args.mode}")
    result = run_mission(harness, args.seed, args.planet, args.sols, args.mode, args.sol_delay,
                         log_to_db=not args.offline, planner=planner, fake_world=args.fake_world, verbose=True)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
