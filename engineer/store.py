"""Atlas access for the Engineer: harness versions, patches, incidents and their telemetry.

Everything goes through `database()`, which is `db.client.get_db()` unless a test
or offline run swaps in another database with `use_db()`.
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from pymongo import DESCENDING

from contracts import load_harness_v1
from contracts.models import HarnessConfig, HarnessPatch, utcnow
from db.client import EVENTS, HARNESS, PATCHES, TELEMETRY, get_db

_override = None


def use_db(db) -> None:
    global _override
    _override = db


def database():
    return _override if _override is not None else get_db()


# --- Harness versions --------------------------------------------------------


def harness_doc(h: HarnessConfig, **extra: Any) -> dict:
    return {**h.model_dump(mode="python"), **extra}


def ensure_v1(d) -> None:
    """Seed v1 from contracts/harness_v1.json if the collection is empty (D04 fallback)."""
    if d[HARNESS].count_documents({}, limit=1) == 0:
        d[HARNESS].insert_one(harness_doc(load_harness_v1()))


def get_active(d) -> HarnessConfig:
    ensure_v1(d)
    doc = d[HARNESS].find_one({"status": "active"}, sort=[("version", DESCENDING)])
    if doc is None:
        raise RuntimeError("harness_versions has documents but none is active")
    return HarnessConfig.model_validate(doc)


def get_harness_raw(d, version: int) -> dict | None:
    return d[HARNESS].find_one({"version": version})


def next_version(d) -> int:
    doc = d[HARNESS].find_one({}, sort=[("version", DESCENDING)], projection={"version": 1})
    return (doc["version"] if doc else 0) + 1


def save_harness(d, h: HarnessConfig, **extra: Any) -> None:
    d[HARNESS].replace_one({"version": h.version}, harness_doc(h, **extra), upsert=True)


def set_harness_fields(d, version: int, **fields: Any) -> None:
    d[HARNESS].update_one({"version": version}, {"$set": fields})


def promote(d, new_version: int, old_version: int) -> None:
    # Activate first, then retire: readers sort by version, so there is never a moment with no active harness.
    d[HARNESS].update_one({"version": new_version}, {"$set": {"status": "active", "promoted_at": utcnow()}})
    d[HARNESS].update_one({"version": old_version}, {"$set": {"status": "retired", "retired_at": utcnow()}})


def lineage(d) -> list[dict]:
    return list(d[HARNESS].find({}, {"version": 1, "parent_version": 1, "status": 1, "rationale": 1,
                                     "eval.score": 1, "author": 1}).sort("version", 1))


# --- Patches -----------------------------------------------------------------


def insert_patch(d, patch: HarnessPatch, **extra: Any) -> str:
    return str(d[PATCHES].insert_one({**patch.model_dump(mode="python"), **extra}).inserted_id)


def update_patch(d, patch_id: str, **fields: Any) -> None:
    d[PATCHES].update_one({"_id": ObjectId(patch_id)}, {"$set": fields})


def recent_patches(d, n: int = 5) -> list[dict]:
    return list(d[PATCHES].find({}).sort("created_at", DESCENDING).limit(n))


# --- Incidents and their context ---------------------------------------------


def _oid(event_id: str):
    return ObjectId(event_id) if ObjectId.is_valid(event_id) else event_id


def get_event(d, event_id: str) -> dict | None:
    return d[EVENTS].find_one({"_id": _oid(event_id)})


def get_events(d, event_ids: list[str]) -> list[dict]:
    return list(d[EVENTS].find({"_id": {"$in": [_oid(i) for i in event_ids]}}))


def telemetry_before(d, mission_id: str, sol: int, n: int = 5) -> list[dict]:
    """The n sols up to and including `sol`, oldest first."""
    rows = d[TELEMETRY].find({"meta.mission_id": mission_id, "sol": {"$gte": sol - n + 1, "$lte": sol}},
                             {"_id": 0}).sort("sol", 1)
    return list(rows)


def mission_events(d, mission_id: str, upto_sol: int) -> list[dict]:
    return list(d[EVENTS].find({"mission_id": mission_id, "sol": {"$lte": upto_sol}},
                               {"_id": 0, "mission_id": 0, "ts": 0}).sort("sol", 1))
