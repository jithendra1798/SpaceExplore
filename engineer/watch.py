"""Live loop: every new live incident gets diagnosed, patched, evaluated and maybe promoted.

Uses D's change-stream helper `db.watch.watch_incidents` when it exists (D09), and
polls `events` every 5 s until then. One patch is in flight at a time; incidents that
arrive meanwhile, or come from a mission already handled, are skipped.

    uv run python -m engineer.watch
"""

from __future__ import annotations

import argparse
import threading
import time

from dotenv import load_dotenv
from pymongo import DESCENDING

from contracts.models import INCIDENT_SEVERITIES
from db.client import EVENTS
from engineer import store
from engineer.agent import get_engineer
from engineer.pipeline import improve, log
from engineer.runners import RUNNERS, get_runner

POLL_SECONDS = 5.0


class Debouncer:
    def __init__(self, handle):
        self.handle = handle
        self.busy = threading.Lock()
        self.seen_missions: set[str] = set()

    def __call__(self, event: dict) -> None:
        mission = event.get("mission_id", "")
        if mission in self.seen_missions:
            return
        if not self.busy.acquire(blocking=False):
            log(f"busy; skipping {event.get('type')} from {mission}")
            return
        self.seen_missions.add(mission)
        threading.Thread(target=self._run, args=(event,), daemon=True).start()

    def _run(self, event: dict) -> None:
        try:
            self.handle(event)
        finally:
            self.busy.release()


def poll_incidents(d, callback, interval: float = POLL_SECONDS) -> None:
    query = {"mode": "live", "severity": {"$in": list(INCIDENT_SEVERITIES)}}
    last = d[EVENTS].find_one(query, sort=[("_id", DESCENDING)], projection={"_id": 1})
    last_id = last["_id"] if last else None
    while True:
        q = dict(query, **({"_id": {"$gt": last_id}} if last_id else {}))
        for e in d[EVENTS].find(q).sort("_id", 1):
            last_id = e["_id"]
            callback(e)
        time.sleep(interval)


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description="Watch live incidents and improve the harness.")
    p.add_argument("--runner", choices=RUNNERS, default="real")
    p.add_argument("--engineer", choices=["llm", "scripted"], default="llm")
    p.add_argument("--planet", default="mars")
    p.add_argument("--sols", type=int, default=30)
    args = p.parse_args()

    d = store.database()
    store.ensure_v1(d)
    runner, engineer = get_runner(args.runner), get_engineer(args.engineer)
    on_incident = Debouncer(lambda e: improve(d, e, engineer, runner, args.runner, args.planet, args.sols))

    try:
        from db.watch import watch_incidents
    except ModuleNotFoundError as e:
        if e.name not in ("db.watch",):
            raise
        log(f"db.watch not available yet; polling events every {POLL_SECONDS:.0f}s")
        poll_incidents(d, on_incident)
    else:
        log("watching incidents via change stream")
        watch_incidents(on_incident)


if __name__ == "__main__":
    main()
