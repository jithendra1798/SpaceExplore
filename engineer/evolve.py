"""Offline evolution loop: builds the lineage the demo shows (D17).

Each generation runs the active harness on a train seed (1-20), picks that mission's
worst incident, and runs one improvement step. Held-out seeds 101-103 decide acceptance.

    uv run python -m engineer.evolve --generations 6                      # real Explorer + Claude Engineer
    uv run python -m engineer.evolve --runner fake --engineer scripted    # no sim, no API key
    uv run python -m engineer.evolve --runner fake --engineer scripted --offline   # no Atlas either
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from contracts.constitution import HELD_OUT_SEEDS
from contracts.models import INCIDENT_SEVERITIES
from engineer import store
from engineer.agent import get_engineer
from engineer.pipeline import Outcome, improve, log
from engineer.runners import RUNNERS, get_runner

TRAIN_SEEDS = list(range(1, 21))
SEVERITY_RANK = {"critical": 0, "major": 1}


def worst_incident(events: list[dict]) -> dict | None:
    incidents = [e for e in events if e.get("severity") in INCIDENT_SEVERITIES]
    return min(incidents, key=lambda e: (SEVERITY_RANK[e["severity"]], e.get("sol", 0)), default=None)


def evolve(d, generations: int, runner_name: str, engineer_name: str, planet: str = "mars",
           max_sols: int = 30, train_seeds: list[int] = TRAIN_SEEDS) -> list[Outcome]:
    runner, engineer = get_runner(runner_name), get_engineer(engineer_name)
    outcomes: list[Outcome] = []
    for g in range(generations):
        base = store.get_active(d)
        seed = train_seeds[g % len(train_seeds)]
        log(f"--- generation {g + 1}/{generations}: v{base.version} on train seed {seed}")
        result = runner(base, seed, planet=planet, max_sols=max_sols, mode="live")
        event = worst_incident(store.get_events(d, result.incident_event_ids))
        if event is None:
            log(f"no incidents on seed {seed} (score {result.score:.1f}); next seed")
            continue
        outcomes.append(improve(d, event, engineer, runner, runner_name, planet, max_sols, HELD_OUT_SEEDS))
    return outcomes


def print_lineage(d) -> None:
    print("\nversion  parent  status     held-out  rationale")
    for h in store.lineage(d):
        score = (h.get("eval") or {}).get("score")
        print(f"v{h['version']:<7} {str(h.get('parent_version') or '-'):<7} {h['status']:<10} "
              f"{'' if score is None else f'{score:.1f}':<9} {h.get('rationale', '')[:70]}")


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description="Evolve the Explorer's harness over several generations.")
    p.add_argument("--generations", type=int, default=6)
    p.add_argument("--runner", choices=RUNNERS, default="real")
    p.add_argument("--engineer", choices=["llm", "scripted"], default="llm")
    p.add_argument("--planet", default="mars")
    p.add_argument("--sols", type=int, default=30)
    p.add_argument("--offline", action="store_true", help="use an in-memory database (mongomock) instead of Atlas")
    args = p.parse_args()

    if args.offline:
        import mongomock
        mem = mongomock.MongoClient()["rover"]
        store.use_db(mem)
        if args.runner != "fake":  # point B's runner at the same in-memory database
            import explorer.mission
            explorer.mission.get_db = lambda: mem
    d = store.database()
    evolve(d, args.generations, args.runner, args.engineer, args.planet, args.sols)
    print_lineage(d)


if __name__ == "__main__":
    main()
