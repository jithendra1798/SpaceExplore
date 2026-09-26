"""Create the Atlas collections and indexes (CONTRACTS §5) and seed harness v1. Safe to re-run.

    uv run python -m scripts.setup_db            # create whatever is missing, then report
    uv run python -m scripts.setup_db --check    # report only; also a quick connection test
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from pymongo import ASCENDING, DESCENDING, GEO2D, IndexModel
from pymongo.database import Database
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

from contracts import load_harness_v1
from db.client import EVENTS, HARNESS, MAP, MEMORIES, MISSIONS, PATCHES, TELEMETRY, get_client, get_db
from db.memory import EMBED_DIM, EMBED_MODEL, VECTOR_INDEX

COLLECTIONS = (HARNESS, PATCHES, MISSIONS, TELEMETRY, EVENTS, MEMORIES, MAP)
TIMESERIES = {"timeField": "ts", "metaField": "meta", "granularity": "seconds"}
INDEXES: dict[str, list[IndexModel]] = {
    HARNESS: [IndexModel([("version", ASCENDING)], unique=True), IndexModel([("status", ASCENDING)])],
    PATCHES: [IndexModel([("created_at", DESCENDING)])],
    MISSIONS: [IndexModel([("harness_version", ASCENDING), ("mode", ASCENDING)])],
    EVENTS: [IndexModel([("mission_id", ASCENDING), ("sol", ASCENDING)])],
    # Not unique: loc is an array, so a unique index would compare x and y values separately
    # and reject distinct tiles that share a coordinate. B upserts each tile from one thread.
    MAP: [IndexModel([("loc", GEO2D)]), IndexModel([("mission_id", ASCENDING), ("loc", ASCENDING)])],
}
VECTOR_DEFINITION = {"fields": [
    {"type": "vector", "path": "embedding", "numDimensions": EMBED_DIM, "similarity": "cosine"},
    {"type": "filter", "path": "kind"},
    {"type": "filter", "path": "planet"},
]}


def _vector_fields(definition: dict) -> set[tuple]:
    return {(f.get("type"), f.get("path"), f.get("numDimensions"), f.get("similarity"))
            for f in definition.get("fields", [])}


def _vector_index(db: Database) -> dict | None:
    return next(db[MEMORIES].list_search_indexes(VECTOR_INDEX), None)


def ensure_telemetry(db: Database, recreate: bool) -> list[str]:
    info = next(db.list_collections(filter={"name": TELEMETRY}), None)
    if info is not None and "timeseries" not in info.get("options", {}):
        n = db[TELEMETRY].estimated_document_count()
        if n and not recreate:
            return [f"{TELEMETRY} is a normal collection with {n} docs, written before setup. "
                    "Re-run with --recreate-telemetry to drop it and recreate it as time-series."]
        db.drop_collection(TELEMETRY)
        print(f"dropped normal collection {TELEMETRY} ({n} docs)")
        info = None
    if info is None:
        db.create_collection(TELEMETRY, timeseries=TIMESERIES)
        print(f"created {TELEMETRY} as a time-series collection")
    return []


def ensure_indexes(db: Database) -> list[str]:
    problems = []
    for name, models in INDEXES.items():
        try:
            db[name].create_indexes(models)
        except OperationFailure as e:
            problems.append(f"{name} indexes: {e}")
    return problems


def ensure_vector_index(db: Database, wait_s: int) -> list[str]:
    if MEMORIES not in db.list_collection_names():
        db.create_collection(MEMORIES)
    try:
        existing = _vector_index(db)
        if existing is None:
            db[MEMORIES].create_search_index(SearchIndexModel(VECTOR_DEFINITION, name=VECTOR_INDEX, type="vectorSearch"))
            print(f"creating vector index {VECTOR_INDEX} ({EMBED_DIM} dims)")
        elif _vector_fields(existing.get("latestDefinition", {})) != _vector_fields(VECTOR_DEFINITION):
            db[MEMORIES].update_search_index(VECTOR_INDEX, VECTOR_DEFINITION)
            print(f"updating vector index {VECTOR_INDEX} to {EMBED_DIM} dims")
    except OperationFailure as e:
        return [f"could not create {VECTOR_INDEX}: {e}\n  Create it in the Atlas UI (Atlas Search > Create Search "
                f"Index > Vector Search > JSON editor) on {db.name}.{MEMORIES} with:\n"
                f"  {json.dumps(VECTOR_DEFINITION)}"]
    deadline = time.time() + wait_s
    while (idx := _vector_index(db)) and not idx.get("queryable") and time.time() < deadline:
        print(f"  waiting for {VECTOR_INDEX}: {idx.get('status')}")
        time.sleep(5)
    return []


def seed_harness_v1(db: Database) -> None:
    # $setOnInsert: re-running must never reset v1 to active once the Engineer has promoted v2+.
    res = db[HARNESS].update_one({"version": 1}, {"$setOnInsert": load_harness_v1().model_dump()}, upsert=True)
    print("seeded harness v1" if res.upserted_id is not None else "harness v1 already seeded; left as is")


def report(db: Database) -> list[str]:
    problems = []
    names = set(db.list_collection_names())
    for name in COLLECTIONS:
        if name not in names:
            problems.append(f"missing collection {name}")
            continue
        idx = [i["name"] + (" (unique)" if i.get("unique") else "") for i in db[name].list_indexes()
               if i["name"] != "_id_"]
        print(f"  {name:<17} {db[name].estimated_document_count():>6} docs   {', '.join(idx) or '-'}")

    ts = next(db.list_collections(filter={"name": TELEMETRY}), None)
    if ts is not None and "timeseries" not in ts.get("options", {}):
        problems.append(f"{TELEMETRY} is not a time-series collection")
    if MEMORIES in names:
        vi = _vector_index(db)
        if vi is None:
            problems.append(f"missing vector index {VECTOR_INDEX}")
        else:
            dims = next((f.get("numDimensions") for f in vi.get("latestDefinition", {}).get("fields", [])
                         if f.get("type") == "vector"), None)
            print(f"  {VECTOR_INDEX:<17} {vi.get('status')}, queryable={vi.get('queryable')}, {dims} dims "
                  f"(db/memory.py embeds with {EMBED_MODEL}, {EMBED_DIM} dims)")
            if dims != EMBED_DIM:
                problems.append(f"{VECTOR_INDEX} has {dims} dims but db/memory.py embeds {EMBED_DIM}")
            elif not vi.get("queryable"):
                problems.append(f"{VECTOR_INDEX} is not queryable yet; re-run --check in a minute")

    active = [d["version"] for d in db[HARNESS].find({"status": "active"}, {"version": 1})]
    print(f"  active harness    {', '.join(f'v{v}' for v in active) or 'none'}")
    if db[HARNESS].count_documents({"version": 1}) == 0:
        problems.append("harness v1 not seeded")
    if len(active) != 1:
        problems.append(f"expected exactly one active harness version, found {len(active)}")
    return problems


def main() -> None:
    p = argparse.ArgumentParser(description="Create the Atlas collections and indexes, and seed harness v1.")
    p.add_argument("--check", action="store_true", help="report only; change nothing")
    p.add_argument("--recreate-telemetry", action="store_true",
                   help="drop a telemetry collection that is not time-series and recreate it")
    p.add_argument("--wait", type=int, default=180, help="seconds to wait for the vector index to be queryable")
    args = p.parse_args()

    db = get_db()
    client = get_client()
    version = client.server_info()["version"]  # connects, or fails fast with a clear error
    host = next(iter(client.nodes), ("?", 0))[0]
    print(f"connected to {host}, MongoDB {version}, database {db.name!r}")

    problems: list[str] = []
    if not args.check:
        problems += ensure_telemetry(db, args.recreate_telemetry)
        problems += ensure_indexes(db)
        problems += ensure_vector_index(db, args.wait)
        seed_harness_v1(db)
    print(f"\n{db.name}:")
    problems += report(db)
    if problems:
        print("\nPROBLEMS:\n" + "\n".join(f"- {p}" for p in problems))
        sys.exit(1)
    print("\nOK: Atlas is set up.")


if __name__ == "__main__":
    main()
