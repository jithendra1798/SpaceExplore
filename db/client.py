"""Atlas connection and collection names (CONTRACTS §5). Owned by workstream D."""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import find_dotenv, load_dotenv
from pymongo import MongoClient
from pymongo.database import Database

HARNESS = "harness_versions"
PATCHES = "patches"
MISSIONS = "missions"
TELEMETRY = "telemetry"
EVENTS = "events"
MEMORIES = "memories"
MAP = "map_knowledge"


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    # MongoClient is thread-safe; share one per process.
    load_dotenv(find_dotenv(usecwd=True))
    return MongoClient(os.environ["MONGODB_URI"], appname="spaceexplore")


def get_db() -> Database:
    load_dotenv(find_dotenv(usecwd=True))
    return get_client()[os.environ.get("MONGODB_DB", "rover")]
