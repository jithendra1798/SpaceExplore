"""Explorer memory on Atlas (CONTRACTS §5.1): Voyage embeddings, $vectorSearch and $geoNear.

explorer/memory.py imports this module lazily and only falls back when it is missing,
so importing it must never fail: the Voyage client is created on first use.
"""

from __future__ import annotations

import math
import os
import sys
from functools import lru_cache

from dotenv import load_dotenv

from contracts.models import utcnow
from db.client import MAP, MEMORIES, get_db

# scripts/setup_db.py builds the vector index for this model's output size; change both together.
EMBED_MODEL = "voyage-3.5-lite"
EMBED_DIM = 1024
VECTOR_INDEX = "memories_vec"
ANY_PLANET = "any"  # memories stored without a planet (e.g. lessons) match every planet
MAX_HAZARDS = 10


@lru_cache(maxsize=1)
def _voyage():
    import voyageai  # deferred so a broken install can't break importing this module

    load_dotenv()
    return voyageai.Client(max_retries=3, base_url=os.environ.get("VOYAGE_BASE_URL") or None)


def embed(text: str, input_type: str = "document") -> list[float]:
    """Embed one text: input_type "document" for stored memories, "query" for searches."""
    vec = _voyage().embed([text], model=EMBED_MODEL, input_type=input_type).embeddings[0]
    if len(vec) != EMBED_DIM:
        raise ValueError(f"{EMBED_MODEL} returned {len(vec)} dims but {VECTOR_INDEX} expects {EMBED_DIM}")
    return vec


@lru_cache(maxsize=4096)
def _query_vector(query: str) -> tuple[float, ...]:
    # Parallel eval missions on the same seeds repeat situations; embed each one once.
    return tuple(embed(query, input_type="query"))


def add_memory(kind: str, text: str, **fields) -> str:
    """Embed and insert one memory; `fields` usually has mission_id, sol, loc, planet, harness_version.

    If embedding fails the memory is still stored, without `embedding`: the UI and the
    Explorer's recency fallback still see it, but vector search does not.
    """
    doc = {"kind": kind, "text": text, "created_at": utcnow(), **fields}
    doc["planet"] = doc.get("planet") or ANY_PLANET
    try:
        doc["embedding"] = embed(text)
    except Exception as e:
        print(f"[db.memory] embed failed, storing {kind} memory without embedding: {e}", file=sys.stderr)
    return str(get_db()[MEMORIES].insert_one(doc).inserted_id)


def search_memories(query: str, kinds: list[str], k: int, planet: str | None = None) -> list[dict]:
    """The k memories of the given kinds closest to `query` ($vectorSearch on memories_vec)."""
    if k <= 0 or not kinds:
        return []
    filters: list[dict] = [{"kind": {"$in": list(kinds)}}]
    if planet:
        filters.append({"planet": {"$in": [planet, ANY_PLANET]}})
    pipeline = [
        {"$vectorSearch": {
            "index": VECTOR_INDEX, "path": "embedding", "queryVector": list(_query_vector(query)),
            "numCandidates": max(100, 20 * k), "limit": k, "filter": {"$and": filters},
        }},
        {"$project": {"embedding": 0, "score": {"$meta": "vectorSearchScore"}}},
    ]
    return list(get_db()[MEMORIES].aggregate(pipeline))


def hazards_near(mission_id: str, pos: tuple[int, int], radius: int) -> list[dict]:
    """Known hazards within `radius` tiles of `pos` in this mission, nearest first ($geoNear).

    The radius is a square, like the Explorer's local fallback. $geoNear on the flat 2d
    index measures straight-line distance in tiles, so search the circle around the
    square and trim its corners.
    """
    if radius <= 0:
        return []
    px, py = pos
    pipeline = [
        {"$geoNear": {
            "near": [px, py], "key": "loc", "distanceField": "distance", "spherical": False,
            "maxDistance": radius * math.sqrt(2) + 0.01,
            "query": {"mission_id": mission_id, "hazard": True},
        }},
        {"$project": {"_id": 0, "loc": 1, "terrain": 1, "slip_probed": 1, "hazard": 1, "distance": 1}},
    ]
    near = [h for h in get_db()[MAP].aggregate(pipeline)
            if max(abs(h["loc"][0] - px), abs(h["loc"][1] - py)) <= radius]
    return near[:MAX_HAZARDS]
