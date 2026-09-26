"""The one score formula (CONTRACTS §3.5). Used by evaluation and shown in the UI."""

from __future__ import annotations

from contracts.models import Metrics

DEATH_PENALTY = 50.0


def score(m: Metrics) -> float:
    return (
        m.science
        + 0.5 * m.sols_survived
        - 2 * m.stuck_sols
        - 5 * m.incidents
        - (DEATH_PENALTY if not m.alive else 0.0)
    )


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
