"""Run one improvement step on a specific incident already in Atlas.

    uv run python -m engineer.step --event-id <events _id>
    uv run python -m engineer.step --latest              # newest live major/critical incident
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv
from pymongo import DESCENDING

from contracts.models import INCIDENT_SEVERITIES
from db.client import EVENTS
from engineer import store
from engineer.agent import get_engineer
from engineer.evolve import print_lineage
from engineer.pipeline import improve
from engineer.runners import RUNNERS, get_runner


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description="Diagnose, patch and evaluate one incident.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--event-id")
    src.add_argument("--latest", action="store_true")
    p.add_argument("--runner", choices=RUNNERS, default="real")
    p.add_argument("--engineer", choices=["llm", "scripted"], default="llm")
    p.add_argument("--planet", default="mars")
    p.add_argument("--sols", type=int, default=30)
    args = p.parse_args()

    d = store.database()
    if args.latest:
        event = d[EVENTS].find_one({"mode": "live", "severity": {"$in": list(INCIDENT_SEVERITIES)}},
                                   sort=[("_id", DESCENDING)])
    else:
        event = store.get_event(d, args.event_id)
    if event is None:
        raise SystemExit("incident not found")
    out = improve(d, event, get_engineer(args.engineer), get_runner(args.runner), args.runner, args.planet, args.sols)
    print(out)
    print_lineage(d)


if __name__ == "__main__":
    main()
