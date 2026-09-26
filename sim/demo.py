"""Console replay for the simulator's observation-only reference policies."""

from __future__ import annotations

import argparse
from typing import Iterable

from sim.policies import PolicyRun, run_policy
from sim.seeds import DEMO_SEED, HELD_OUT_SEEDS


def _describe_run(run: PolicyRun, max_sols: int, show_sols: bool = True) -> None:
    print(f"\n=== {run.policy.upper()} · seed {run.seed} · {run.planet} ===")
    if show_sols:
        for result in run.results:
            position = result.observation.pos
            actions = ", ".join(
                f"{item.action.tool}{_args(item.action.args)}{' ✓' if item.ok else ' ·'}"
                for item in result.executed
            ) or "no action"
            event_text = ", ".join(f"{event.type}@{event.pos}" for event in result.events)
            print(
                f"sol {result.sol:02d} pos={position!s:>9} battery={result.observation.battery:5.1f}% "
                f"wheel={result.observation.wheel_health:.2f} actions=[{actions}]"
                + (f" events=[{event_text}]" if event_text else "")
            )
    metrics = run.metrics
    print(
        "metrics: "
        f"alive={metrics.alive}, sols={metrics.sols_survived}, science={metrics.science:.1f}, "
        f"distance={metrics.distance}, stuck_sols={metrics.stuck_sols}, "
        f"incidents={metrics.incidents}, min_battery={metrics.min_battery:.1f}%"
    )


def _args(args: dict) -> str:
    if not args:
        return ""
    return "(" + ",".join(f"{key}={value}" for key, value in args.items()) + ")"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEMO_SEED)
    parser.add_argument("--planet", choices=("mars", "icy"), default="mars")
    parser.add_argument("--policy", choices=("random", "greedy", "careful", "all"), default="greedy")
    parser.add_argument("--sols", type=int, default=30)
    parser.add_argument("--size", type=int, default=40)
    parser.add_argument("--eval-seeds", action="store_true", help="print greedy/careful scores on seeds 101–103")
    parser.add_argument("--quiet", action="store_true", help="show only mission summaries")
    args = parser.parse_args(argv)

    if args.eval_seeds:
        for seed in HELD_OUT_SEEDS:
            for policy in ("greedy", "careful"):
                run = run_policy(seed, policy, planet=args.planet, size=args.size, max_sols=args.sols)
                _describe_run(run, args.sols, show_sols=False)
        return 0

    policy_names = ("random", "greedy", "careful") if args.policy == "all" else (args.policy,)
    for name in policy_names:
        run = run_policy(args.seed, name, planet=args.planet, size=args.size, max_sols=args.sols)
        _describe_run(run, args.sols, show_sols=not args.quiet)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
