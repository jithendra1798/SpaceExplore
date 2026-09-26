"""Change stream that wakes the Engineer (CONTRACTS §5.1)."""

from __future__ import annotations

import sys
import time
from typing import Callable

from pymongo.database import Database
from pymongo.errors import OperationFailure, PyMongoError

from contracts.models import INCIDENT_SEVERITIES
from db.client import EVENTS, get_db

INCIDENTS = [{"$match": {
    "operationType": "insert",
    "fullDocument.mode": "live",
    "fullDocument.severity": {"$in": list(INCIDENT_SEVERITIES)},
}}]
MAX_FAILURES = 5  # consecutive failures to open the stream before giving up


def watch_incidents(callback: Callable[[dict], None], db: Database | None = None) -> None:
    """Block forever, calling `callback(event_doc)` for each live major or critical event inserted.

    Only events inserted after the stream opens are delivered, so start it before the mission.
    Resumes after network errors; an exception in `callback` is logged and the stream carries on.
    """
    db = db if db is not None else get_db()
    token, failures = None, 0
    while True:
        try:
            with db[EVENTS].watch(INCIDENTS, resume_after=token) as stream:
                failures = 0
                print(f"[watch] change stream open on {db.name}.{EVENTS}", flush=True)
                for change in stream:
                    token = stream.resume_token
                    if not (doc := change.get("fullDocument")):
                        continue
                    print(f"[watch] change stream woke: {doc.get('type')} ({doc.get('severity')}) "
                          f"in {doc.get('mission_id')} sol {doc.get('sol')}", flush=True)
                    try:
                        callback(doc)
                    except Exception as e:
                        print(f"[watch] callback failed: {e!r}", file=sys.stderr)
            token = None  # the stream was invalidated (collection dropped or renamed): start a fresh one
        except PyMongoError as e:
            failures += 1
            if failures >= MAX_FAILURES:
                raise
            if isinstance(e, OperationFailure):  # e.g. resume token aged out: open a fresh stream
                token = None
            print(f"[watch] change stream error, reopening: {e}", file=sys.stderr)
            time.sleep(1)
