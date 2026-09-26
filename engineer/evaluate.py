"""Held-out evaluation (rule C3) and promotion.

Baseline and candidate run on the same held-out seeds in parallel. The baseline's
per-seed results are cached on its `harness_versions.eval`, keyed by runner, planet
and sol count, so each later patch only pays for the candidate's missions.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from contracts.constitution import HELD_OUT_SEEDS, accept
from contracts.models import EvalResult, HarnessConfig, MissionResult, PatchEval, SeedResult
from contracts.scoring import mean
from engineer import store
from engineer.runners import Runner


def _eval_key(runner_name: str, planet: str, max_sols: int, seeds: list[int]) -> str:
    return f"{runner_name}|{planet}|{max_sols}|{','.join(map(str, seeds))}"


def _to_eval(results: list[MissionResult], seeds: list[int]) -> EvalResult:
    per = [SeedResult(seed=r.seed, score=r.score, metrics=r.metrics) for r in results]
    return EvalResult(seeds=seeds, score=round(mean([r.score for r in per]), 2), per_seed=per)


def run_seeds(pool: ThreadPoolExecutor, runner: Runner, h: HarnessConfig, seeds: list[int], planet: str,
              max_sols: int):
    return [pool.submit(runner, h, s, planet=planet, max_sols=max_sols, mode="eval") for s in seeds]


def evaluate(
    d,
    base: HarnessConfig,
    candidate: HarnessConfig,
    runner: Runner,
    runner_name: str,
    seeds: list[int] = HELD_OUT_SEEDS,
    planet: str = "mars",
    max_sols: int = 30,
) -> tuple[bool, str, PatchEval, EvalResult]:
    """Run the candidate (and the baseline if not cached) and apply C3."""
    key = _eval_key(runner_name, planet, max_sols, seeds)
    raw = store.get_harness_raw(d, base.version) or {}
    cached = base.eval if raw.get("eval_key") == key and base.eval and base.eval.per_seed else None

    with ThreadPoolExecutor(max_workers=len(seeds) * (1 if cached else 2)) as pool:
        cand_f = run_seeds(pool, runner, candidate, seeds, planet, max_sols)
        base_f = None if cached else run_seeds(pool, runner, base, seeds, planet, max_sols)
        cand = [f.result() for f in cand_f]
        base_eval = cached or _to_eval([f.result() for f in base_f], seeds)
    if not cached:
        store.set_harness_fields(d, base.version, eval=base_eval.model_dump(mode="python"), eval_key=key)
    cand_eval = _to_eval(cand, seeds)

    ok, reason = accept(base_eval.per_seed, cand_eval.per_seed)
    base_by_seed = {r.seed: r for r in base_eval.per_seed}
    per_seed = [{
        "seed": r.seed,
        "baseline": base_by_seed[r.seed].score, "candidate": r.score,
        "baseline_alive": base_by_seed[r.seed].metrics.alive, "candidate_alive": r.metrics.alive,
        "candidate_mission_id": m.mission_id,
        "candidate_metrics": r.metrics.model_dump(),
    } for r, m in zip(cand_eval.per_seed, cand)]
    patch_eval = PatchEval(seeds=seeds, baseline_score=base_eval.score, candidate_score=cand_eval.score,
                           per_seed=per_seed)
    return ok, reason, patch_eval, cand_eval
