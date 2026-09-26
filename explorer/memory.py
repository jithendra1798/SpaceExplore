"""Explorer memory access.

Uses workstream D's `db.memory` helpers (Atlas Vector Search, $geoNear) when they
exist; until then (D07, D08) falls back to plain queries and the in-mission map.
"""

from __future__ import annotations

import sys

from contracts.models import utcnow
from db.client import MEMORIES

HAZARD_TERRAIN = {"sand", "rocks", "crater_edge", "ice"}
_warned: set[str] = set()


def _warn_once(what: str, e: Exception) -> None:
    if what not in _warned:
        _warned.add(what)
        print(f"[explorer] {what} failed ({e}); falling back for the rest of this run", file=sys.stderr)


def _helpers():
    try:
        import db.memory as m
    except ModuleNotFoundError as e:
        if e.name != "db.memory":
            raise
        return None
    return m


def recall(db, query: str, kinds: list[str], k: int, planet: str) -> list[dict]:
    if k <= 0 or db is None:
        return []
    m = _helpers()
    if m is not None:
        try:
            return m.search_memories(query, kinds, k, planet)
        except Exception as e:  # vector index not ready yet; fall back
            _warn_once("search_memories", e)
    cursor = db[MEMORIES].find({"kind": {"$in": kinds}}, {"embedding": 0}).sort("created_at", -1).limit(k)
    return list(cursor)


def remember(db, kind: str, text: str, **fields) -> None:
    if db is None:
        return
    m = _helpers()
    if m is not None:
        try:
            m.add_memory(kind, text, **fields)
            return
        except Exception as e:
            _warn_once("add_memory", e)
    db[MEMORIES].insert_one({"kind": kind, "text": text, "created_at": utcnow(), **fields})


def hazards_near(db, mission_id: str, pos: tuple[int, int], radius: int, local: dict[tuple[int, int], dict]) -> list[dict]:
    if radius <= 0:
        return []
    m = _helpers()
    if m is not None and db is not None:
        try:
            return m.hazards_near(mission_id, pos, radius)
        except Exception as e:
            _warn_once("hazards_near", e)
    near = [h for (x, y), h in local.items()
            if h.get("hazard") and max(abs(x - pos[0]), abs(y - pos[1])) <= radius]
    return sorted(near, key=lambda h: max(abs(h["loc"][0] - pos[0]), abs(h["loc"][1] - pos[1])))[:10]
