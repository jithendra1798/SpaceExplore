"""Smoke-test workstream D's Atlas helpers on the real cluster (the brief's "done when" checks).

    uv run python -m scripts.check_db

Checks Voyage embeddings, a $vectorSearch round trip, $geoNear, the Explorer calling both
through explorer.mission, and the incident change stream. Test documents are isolated
(mission_id "check_*", planet "__check__", which real missions never search) and deleted
afterwards. The change-stream test runs in a scratch database so a running Engineer never
sees its fake incident, and the Explorer check runs in eval mode for the same reason.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from typing import Callable

from dotenv import load_dotenv
from pymongo import UpdateOne
from pymongo.errors import DuplicateKeyError

from contracts import load_harness_v1
from contracts.models import utcnow
from db import memory
from db.client import EVENTS, MAP, MEMORIES, MISSIONS, get_client, get_db
from db.watch import watch_incidents

SCRATCH_DB = "rover_check"
CHECK_PLANET = "__check__"
RUN_ID = f"check_{uuid.uuid4().hex[:6]}"
failures: list[str] = []


def run(name: str, fn: Callable[[], str], needs_voyage: bool = False) -> None:
    if needs_voyage and not os.environ.get("VOYAGE_API_KEY"):
        print(f"SKIP  {name:<28} VOYAGE_API_KEY is empty in .env")
        return
    try:
        print(f"PASS  {name:<28} {fn()}")
    except Exception as e:
        failures.append(name)
        print(f"FAIL  {name:<28} {e!r}")


def check_embed() -> str:
    return f"{memory.EMBED_MODEL} -> {len(memory.embed('rover stuck in soft sand', input_type='query'))} dims"


def check_vector_search() -> str:
    add = lambda text, planet: memory.add_memory("lesson", text, planet=planet, mission_id=RUN_ID)
    sand = add("Probe sand before crossing it; slip above 0.3 trapped the rover for 8 sols.", CHECK_PLANET)
    storm = add("Shelter when a dust storm pushes tau above 2 to save battery.", CHECK_PLANET)
    icy = add("Ice sheets on the moon crack under heavy drilling.", "icy")
    deadline, ids = time.time() + 60, []
    while time.time() < deadline:  # new documents take a moment to reach the vector index
        hits = memory.search_memories("the rover got stuck in sand", ["lesson"], 10, CHECK_PLANET)
        ids = [str(h["_id"]) for h in hits]
        if sand in ids and storm in ids:
            break
        time.sleep(2)
    assert sand in ids and storm in ids, f"test memories not returned after 60 s (got {len(ids)} hits)"
    assert ids.index(sand) < ids.index(storm), "the storm lesson outranked the sand lesson"
    assert icy not in ids, "the planet filter let an icy memory through"
    return "sand lesson ranked above storm lesson; other planets filtered out"


def check_hazards() -> str:
    tiles = [((5, 5), "sand", True), ((6, 5), "rocks", True), ((5, 6), "regolith", False),
             ((7, 7), "crater_edge", True), ((9, 9), "sand", True)]
    # Same upsert as explorer.mission; these tiles share coordinates, which a unique index would reject.
    get_db()[MAP].bulk_write([UpdateOne({"mission_id": RUN_ID, "loc": [x, y]},
                                        {"$set": {"loc": [x, y], "terrain": t, "slip_probed": None, "hazard": h}},
                                        upsert=True) for (x, y), t, h in tiles], ordered=False)
    got = sorted(tuple(h["loc"]) for h in memory.hazards_near(RUN_ID, (5, 5), 3))
    assert got == [(5, 5), (6, 5), (7, 7)], f"got {got}"
    return "3 hazards within 3 tiles; the safe tile and the far one excluded"


def check_explorer() -> str:
    from explorer.agent import ScriptedPlanner
    from explorer.mission import run_mission

    stats = {"searches": 0, "recalled": 0, "geo": 0, "hazards": 0}
    real_search, real_hazards = memory.search_memories, memory.hazards_near

    def search(*a, **kw):
        hits = real_search(*a, **kw)
        stats["searches"] += 1
        stats["recalled"] += len(hits)
        return hits

    def hazards(*a, **kw):
        near = real_hazards(*a, **kw)
        stats["geo"] += 1
        stats["hazards"] += len(near)
        return near

    h = load_harness_v1()
    h.context_policy.memory_k, h.context_policy.hazards_within = 3, 3
    memory.search_memories, memory.hazards_near = search, hazards
    try:  # eval mode: no live incidents for the Engineer's watcher, no memories written
        result = run_mission(h, 42, planet=CHECK_PLANET, max_sols=12, mode="eval",
                             planner=ScriptedPlanner(), fake_world=True)
    finally:
        memory.search_memories, memory.hazards_near = real_search, real_hazards
        db = get_db()
        ids = [m["_id"] for m in db[MISSIONS].find({"planet": CHECK_PLANET}, {"_id": 1})]
        db[MISSIONS].delete_many({"_id": {"$in": ids}})
        db[EVENTS].delete_many({"mission_id": {"$in": ids}})
        db[MAP].delete_many({"mission_id": {"$in": ids}})
    sols = result.metrics.sols_survived
    assert stats["searches"] == sols and stats["recalled"] > 0, f"vector searches {stats}, sols {sols}"
    assert stats["geo"] == sols, f"$geoNear calls {stats}, sols {sols}"
    return (f"{sols} sols: {stats['searches']} vector searches recalled {stats['recalled']} memories, "
            f"{stats['geo']} $geoNear calls found {stats['hazards']} hazards")


def check_watch() -> str:
    scratch = get_client()[SCRATCH_DB]
    seen: list[dict] = []
    threading.Thread(target=watch_incidents, args=(seen.append,), kwargs={"db": scratch}, daemon=True).start()
    time.sleep(3)  # let the stream open
    base = {"mission_id": "check_watch", "harness_version": 1, "sol": 8, "pos": [12, 20], "details": {}}
    scratch[EVENTS].insert_many([
        {**base, "mode": "live", "type": "STUCK", "severity": "major", "ts": utcnow()},
        {**base, "mode": "live", "type": "GUARDRAIL_BLOCK", "severity": "minor", "ts": utcnow()},
        {**base, "mode": "eval", "type": "FALL", "severity": "critical", "ts": utcnow()},
    ])
    deadline = time.time() + 20
    while not seen and time.time() < deadline:
        time.sleep(0.5)
    time.sleep(2)  # anything that should have been filtered out would arrive by now
    assert [d["type"] for d in seen] == ["STUCK"], f"callback got {[d['type'] for d in seen]}"
    return "woke on the live STUCK only; minor and eval events filtered out"


def check_unique_claim() -> str:
    """Why map_knowledge's {mission_id, loc} index is not unique (CONTRACTS §5 asked for unique)."""
    coll = get_client()[SCRATCH_DB]["unique_loc_demo"]
    coll.create_index([("mission_id", 1), ("loc", 1)], unique=True)
    coll.insert_one({"mission_id": "m", "loc": [3, 5]})
    try:
        coll.insert_one({"mission_id": "m", "loc": [5, 7]})
    except DuplicateKeyError:
        return "confirmed: a unique {mission_id, loc} index rejects distinct tiles (3,5) and (5,7)"
    raise AssertionError("a unique index accepted (3,5) and (5,7); it could be made unique after all")


def main() -> None:
    load_dotenv()
    db = get_db()
    print(f"checking workstream D helpers on {db.name!r}\n")
    try:
        run("embed (Voyage)", check_embed, needs_voyage=True)
        run("$vectorSearch round trip", check_vector_search, needs_voyage=True)
        run("$geoNear hazards", check_hazards)
        run("Explorer uses both", check_explorer, needs_voyage=True)
        run("incident change stream", check_watch)
        run("map index must not be unique", check_unique_claim)
    finally:
        db[MEMORIES].delete_many({"mission_id": RUN_ID})
        db[MAP].delete_many({"mission_id": RUN_ID})
        get_client().drop_database(SCRATCH_DB)
    if failures:
        sys.exit(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    print("\nOK")


if __name__ == "__main__":
    main()
