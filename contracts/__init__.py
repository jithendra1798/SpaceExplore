"""Shared contracts between workstreams. See docs/CONTRACTS.md."""

from __future__ import annotations

import json
from pathlib import Path

from contracts.models import HarnessConfig

HARNESS_V1_PATH = Path(__file__).with_name("harness_v1.json")


def load_harness_v1() -> HarnessConfig:
    return HarnessConfig.model_validate(json.loads(HARNESS_V1_PATH.read_text()))
