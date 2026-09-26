"""Read-only Mission Control API and static UI.

Atlas is queried only. When Atlas is unavailable or has no real mission data,
the server serves an explicitly labeled local preview generated from the
deterministic simulator replay.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from web.demo_data import preview_bundle

LOG = logging.getLogger("spaceexplore.web")
ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="SpaceExplore Mission Control", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _plain(value: Any) -> Any:
    """Make BSON, Pydantic, and Python values safe for JSON responses."""
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="python"))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _database() -> tuple[Any | None, str | None]:
    try:
        from db.client import get_db

        return get_db(), None
    except Exception as exc:  # Atlas is optional for the local preview.
        LOG.info("Atlas unavailable; serving local preview (%s)", type(exc).__name__)
        return None, f"Atlas unavailable ({type(exc).__name__})"


def _find_many(db: Any, collection: str, query: dict[str, Any], sort: list[tuple[str, int]] | None, limit: int) -> list[dict[str, Any]]:
    safe_query = {**query, "fake": {"$ne": True}}
    cursor = db[collection].find(safe_query)
    if sort:
        cursor = cursor.sort(sort)
    return [_plain(item) for item in cursor.limit(limit)]


def _find_one(db: Any, collection: str, query: dict[str, Any]) -> dict[str, Any] | None:
    return _plain(db[collection].find_one({**query, "fake": {"$ne": True}}))


def _missions() -> tuple[list[dict[str, Any]], str, str | None]:
    db, error = _database()
    if db is not None:
        try:
            from db.client import MISSIONS

            docs = _find_many(db, MISSIONS, {}, [("started_at", -1)], 100)
            if docs:
                return docs, "atlas", None
        except Exception as exc:
            error = f"Atlas query failed ({type(exc).__name__})"
            LOG.warning("Mission query failed", exc_info=True)
    return [preview_bundle()["mission"]], "preview", error or "No real missions found in Atlas"


def _mission(mission_id: str) -> tuple[dict[str, Any] | None, str, str | None]:
    db, error = _database()
    if db is not None:
        try:
            from db.client import MISSIONS

            keys: list[Any] = [mission_id]
            try:
                from bson import ObjectId

                if ObjectId.is_valid(mission_id):
                    keys.append(ObjectId(mission_id))
            except ImportError:
                pass
            item = _find_one(db, MISSIONS, {"$or": [{"_id": key} for key in keys] + [{"mission_id": mission_id}]})
            if item is not None:
                return item, "atlas", None
        except Exception as exc:
            error = f"Atlas query failed ({type(exc).__name__})"
            LOG.warning("Mission lookup failed", exc_info=True)
    demo = preview_bundle()["mission"]
    if mission_id == demo["_id"]:
        return demo, "preview", error or "Local deterministic replay"
    return None, "atlas" if error is None else "preview", error


def _mission_collection(mission_id: str, name: str, fixture_key: str) -> tuple[list[dict[str, Any]], str, str | None]:
    mission, source, note = _mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    if source == "preview":
        return preview_bundle()[fixture_key], source, note

    db, error = _database()
    if db is None:
        return [], "atlas", error
    try:
        docs = _find_many(
            db,
            name,
            {"$or": [{"mission_id": mission_id}, {"meta.mission_id": mission_id}]},
            [("sol", 1)],
            10_000,
        )
        return docs, "atlas", None
    except Exception as exc:
        LOG.warning("Mission collection query failed (%s)", name, exc_info=True)
        return [], "atlas", f"Atlas query failed ({type(exc).__name__})"


def _harness_data() -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, str | None]:
    db, error = _database()
    if db is not None:
        try:
            from db.client import HARNESS, PATCHES

            versions = _find_many(db, HARNESS, {}, [("version", 1)], 500)
            patches = _find_many(db, PATCHES, {}, [("created_at", 1)], 1000)
            if versions:
                return versions, patches, "atlas", None
        except Exception as exc:
            LOG.warning("Harness lineage query failed", exc_info=True)
            error = f"Atlas query failed ({type(exc).__name__})"
    demo = preview_bundle()
    return demo["versions"], demo["patches"], "preview", error or "No real harness lineage found in Atlas"


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    ignored = {"_id", "created_at", "eval", "status", "author"}
    changes = {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in sorted((set(before) | set(after)) - ignored)
        if key not in ignored and before.get(key) != after.get(key)
    }
    return changes


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/missions")
def list_missions(limit: int = Query(default=50, ge=1, le=100)) -> dict[str, Any]:
    items, source, note = _missions()
    return {"items": items[:limit], "source": source, "preview": source == "preview", "note": note}


@app.get("/api/missions/{mission_id}")
def get_mission(mission_id: str) -> dict[str, Any]:
    mission, source, note = _mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    snapshot = mission.get("world") or mission.get("snapshot")
    return {
        "mission": mission,
        "snapshot": snapshot,
        "source": source,
        "preview": source == "preview",
        "note": note,
    }


@app.get("/api/missions/{mission_id}/telemetry")
def get_telemetry(mission_id: str) -> dict[str, Any]:
    items, source, note = _mission_collection(mission_id, "telemetry", "telemetry")
    return {"items": items, "source": source, "preview": source == "preview", "note": note}


@app.get("/api/missions/{mission_id}/events")
def get_events(mission_id: str) -> dict[str, Any]:
    items, source, note = _mission_collection(mission_id, "events", "events")
    return {"items": items, "source": source, "preview": source == "preview", "note": note}


@app.get("/api/missions/{mission_id}/map")
def get_map_knowledge(mission_id: str) -> dict[str, Any]:
    items, source, note = _mission_collection(mission_id, "map_knowledge", "map_knowledge")
    return {"items": items, "source": source, "preview": source == "preview", "note": note}


@app.get("/api/missions/{mission_id}/replay")
def get_replay(mission_id: str) -> dict[str, Any]:
    """Return replay-ready frames for the UI, preserving known-map fog of war."""
    mission, source, note = _mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    if source == "preview":
        replay_path = ROOT / "fixtures" / "replay_seed42_greedy.json"
        replay = json.loads(replay_path.read_text(encoding="utf-8"))
        incident_ids = {
            (event["type"], int(event["sol"]), json.dumps(event.get("pos"))): event["_id"]
            for event in preview_bundle()["events"]
        }
        for frame in replay["frames"]:
            for event in frame["events"]:
                event["_id"] = incident_ids.get(
                    (event["type"], int(event["sol"]), json.dumps(event.get("pos")))
                )
        return {**replay, "source": source, "preview": True, "note": note}

    telemetry, _, _ = _mission_collection(mission_id, "telemetry", "telemetry")
    events, _, _ = _mission_collection(mission_id, "events", "events")
    knowledge, _, _ = _mission_collection(mission_id, "map_knowledge", "map_knowledge")
    telemetry.sort(key=lambda item: int(item.get("sol", 0)))
    events.sort(key=lambda item: int(item.get("sol", 0)))
    knowledge.sort(key=lambda item: int(item.get("first_seen_sol", 0)))

    snapshot = mission.get("world") or mission.get("snapshot") or {}
    size = int(snapshot.get("size") or max(
        [int((item.get("loc") or [0, 0])[axis]) + 1 for item in knowledge for axis in (0, 1)] or [40]
    ))
    terrain = snapshot.get("terrain")
    if not terrain:
        terrain = [["unknown" for _ in range(size)] for _ in range(size)]
    snapshot = {**snapshot, "size": size, "terrain": terrain}
    origin = snapshot.get("start") or (telemetry[0].get("pos") if telemetry else [0, 0])
    initial = telemetry[0] if telemetry else {}
    has_initial_state = bool(telemetry and int(telemetry[0].get("sol", 0)) == 0)
    initial_state = {
        "sol": 0, "pos": initial.get("pos", origin) if has_initial_state else origin, "battery": initial.get("battery", 100),
        "wheel_health": initial.get("wheel_health", 1), "panel_dust": initial.get("panel_dust", 0),
        "tau": initial.get("tau", 0.5), "tau_trend": initial.get("tau_trend", 0),
        "stuck": initial.get("stuck", False), "actions": [], "signals": [],
    }
    states = [initial_state] + [item for item in telemetry if int(item.get("sol", 0)) > 0]
    path: list[Any] = []
    accumulated: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    for state in states:
        sol = int(state.get("sol", 0))
        position = state.get("pos") or origin
        if not path or position != path[-1]:
            path.append(position)
        accumulated.extend(event for event in events if int(event.get("sol", 0)) == sol)
        tiles = [item for item in knowledge if int(item.get("first_seen_sol", 0)) <= sol]
        actions = state.get("actions") or state.get("planned") or []
        frames.append({
            **initial_state, **state, "sol": sol, "pos": position,
            "path": list(path), "known_tiles": tiles, "events": list(accumulated),
            "actions": actions,
        })
    return {
        "seed": mission.get("seed"), "planet": mission.get("planet", "mars"),
        "policy": "recorded mission", "max_sols": int(mission.get("max_sols") or max((f["sol"] for f in frames), default=0)),
        "snapshot": snapshot, "frames": frames, "metrics": mission.get("metrics", {}),
        "source": source, "preview": False, "note": note,
    }


@app.get("/api/harness")
def get_harness_lineage() -> dict[str, Any]:
    versions, patches, source, note = _harness_data()
    return {"versions": versions, "patches": patches, "source": source, "preview": source == "preview", "note": note}


@app.get("/api/harness/{from_version}/diff/{to_version}")
def get_harness_diff(from_version: int, to_version: int) -> dict[str, Any]:
    versions, _, source, note = _harness_data()
    before = next((version for version in versions if version.get("version") == from_version), None)
    after = next((version for version in versions if version.get("version") == to_version), None)
    if before is None or after is None:
        raise HTTPException(status_code=404, detail="Harness version not found")
    return {
        "from_version": from_version,
        "to_version": to_version,
        "changes": _diff(before, after),
        "source": source,
        "preview": source == "preview",
        "note": note,
    }


@app.get("/api/patches")
def get_patches(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    _, patches, source, note = _harness_data()
    return {"items": patches[:limit], "source": source, "preview": source == "preview", "note": note}


async def _change_stream():
    yield "event: status\ndata: {\"state\":\"connected\"}\n\n"
    db, error = _database()
    if db is None:
        yield f"event: status\ndata: {json.dumps({'state': 'preview', 'note': error})}\n\n"
        while True:
            await asyncio.sleep(15)
            yield ": keepalive\n\n"

    pipeline = [{"$match": {
        "operationType": {"$in": ["insert", "update", "replace"]},
        "ns.coll": {"$in": ["events", "patches"]},
        "fullDocument.fake": {"$ne": True},
    }}]
    try:
        stream = db.watch(pipeline, full_document="updateLookup", max_await_time_ms=1000)
        with stream:
            while True:
                change = await asyncio.to_thread(stream.try_next)
                if change:
                    yield f"event: change\ndata: {json.dumps(_plain(change), separators=(',', ':'))}\n\n"
                else:
                    await asyncio.sleep(0.4)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOG.info("Atlas change stream unavailable (%s)", type(exc).__name__)
        yield f"event: status\ndata: {json.dumps({'state': 'preview', 'note': f'Change stream unavailable ({type(exc).__name__})'})}\n\n"
        while True:
            await asyncio.sleep(15)
            yield ": keepalive\n\n"


@app.get("/api/live")
async def live_events() -> StreamingResponse:
    return StreamingResponse(
        _change_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
