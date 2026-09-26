"""Harness getters (CONTRACTS §5.1)."""

from __future__ import annotations

from contracts import load_harness_v1
from db.client import HARNESS, get_db


def get_active_harness() -> dict:
    """The active harness version; contracts/harness_v1.json until one is in Atlas."""
    doc = get_db()[HARNESS].find_one({"status": "active"}, {"_id": 0}, sort=[("version", -1)])
    return doc if doc is not None else load_harness_v1().model_dump()


def get_harness(version: int) -> dict:
    doc = get_db()[HARNESS].find_one({"version": version}, {"_id": 0})
    if doc is not None:
        return doc
    if version == 1:
        return load_harness_v1().model_dump()
    raise LookupError(f"harness v{version} is not in {HARNESS}")
