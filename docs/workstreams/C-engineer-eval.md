# Workstream C: Engineer Agent and Evaluation Loop

**Owner:** _(name)_ · **Folders:** `engineer/`, `contracts/scoring.py`, `contracts/constitution.py`

You build the part the judges care about most: the agent that rewrites the Explorer's harness. It reads an incident and proposes a typed patch. The patch is validated against the constitution, then tested against the current version on held-out seeds, and kept only if it really scores better.

## What you provide

| To | What | Contract | By |
| --- | --- | --- | --- |
| A, B, D | `contracts/scoring.py` (`score()`) and `contracts/constitution.py` | CONTRACTS §3.5, §3.6 | 2:20 |
| B | New active harness versions in `harness_versions` | CONTRACTS §3.2 | 3:45 |
| D | `patches` docs with evaluation results, and lineage data | CONTRACTS §3.4 | 3:45 |
| D | Pre-run evolution: 5+ versions for the demo | | 4:15 |

## What you consume, and your stubs

| Need | From | Stub until delivered |
| --- | --- | --- |
| `run_mission()` (D10) | B, by 3:30 | `engineer/fake_runner.py` (below) |
| Incident events (D11) | B, by 3:30 | The example event in CONTRACTS §5.2 |
| `watch_incidents()` (D09) | D, by 3:15 | Poll `events` every 5 s |
| Harness v1 in Atlas (D04) | D, by 2:30 | Load the JSON from CONTRACTS §3.2 |
| Tuned seeds (D16) | A, by 3:45 | Any seeds |

**The fake runner** lets you build and test the whole loop before B is ready. It returns a `MissionResult` whose score depends on the config:

- Start from a base score of 20.
- Add 15 if `shelter` is enabled and there is a rule or guardrail about tau.
- Add 15 if `probe_terrain` is enabled and there is an `avoid_terrain` sand guardrail.
- Add 5 if `memory_k > 0`.
- Add Gaussian noise (σ = 3) seeded by `seed`.

## Build order

1. **2:05–2:20 · Contracts.** Write `contracts/scoring.py` and `contracts/constitution.py`, then push. **This delivers D05.** Then write `engineer/fake_runner.py`.
2. **2:20–2:50 · Patches.** `engineer/patch.py`:
   - `apply_patch(config, patch)` returns a new config with `version + 1`, the parent set, and status `candidate`.
   - `validate_patch()` checks:
     - the schema
     - that every referenced rule or guardrail ID exists
     - guardrail types against the catalog (§3.3)
     - the constitution rule C2
     - that the patch is not a no-op
   - Invalid patches are stored with status `invalid` and the reason, so the demo can show a rejection.
3. **2:50–3:20 · The Engineer.** `engineer/agent.py`:
   - It is one LLM call using `ENGINEER_MODEL`, with a single tool `propose_patch(diagnosis, rationale, ops)` whose ops follow the catalog in §3.4.
   - Its input:
     - the incident event
     - the 5 sols of telemetry before the incident, including the Explorer's reasoning
     - the current harness config
     - the last 5 patches, **including rejected ones**, so it does not repeat failures
     - the constitution, stated as rules it cannot change
4. **3:20–3:45 · Evaluation.** `engineer/evaluate.py`:
   - Run baseline and candidate on seeds 101, 102 and 103 with `max_sols=30` and `mode="eval"`, in 6 threads.
   - Apply rule C3.
   - Cache the baseline's per-seed scores on its `harness_versions.eval`, so each patch only runs the 3 candidate missions.
   - On accept:
     - set the new version `active` and the old one `retired`
     - store the patch as `accepted`
     - write a `lesson` memory: one sentence on what was learned
   - On reject: store the patch as `rejected` with the scores.
   - Swap `fake_runner` for B's `run_mission`. **This delivers D13 and D14.**
5. **3:45–3:50 · Loops.**
   - `engineer/watch.py` is the live loop: `watch_incidents()`, then diagnose, patch, evaluate and promote. Debounce it: one patch in flight at a time, and ignore incidents from a mission that is already being handled.
   - `engineer/evolve.py` is the offline loop. For each generation, run the active harness on a train seed, pick its worst incident, then patch, evaluate and promote.
6. **3:50 · Start the pre-run** so the demo has a real lineage: `python -m engineer.evolve --generations 6`. **This delivers D17.**

## Rules that matter

- **Held-out seeds only for evaluation.** Train seeds (1–20) find incidents; seeds 101–103 decide acceptance. Never mix them. That separation is what makes "it improved" believable.
- **Expect the Engineer to try to game the score.** It may try to delete `g001` to reach science faster. The validator must reject that visibly, and the demo should show one such rejection.
- **Budget.** One patch costs 3 candidate missions × 30 sols = 90 Explorer calls, plus 1 Engineer call. Six generations take about 15 minutes if the runs are parallel. Start the pre-run on time.
- **Stable scoring.** Set temperature 0 for both agents. If the scores are still noisy, use 5 eval seeds.

## Done when

- [ ] On the fake runner, the loop takes v1 to v4 with accepted patches that match the fake scoring
- [ ] On the real runner, a live incident on seed 42 produces a patch, an evaluation and a new active version within about 3 minutes
- [ ] At least one patch is visibly rejected, by the validator or by the evaluation
- [ ] The pre-run lineage has 5+ versions with rising held-out scores

## Stretch

- Transfer: evaluate the evolved harness on the icy moon (seed 7), and let the Engineer adapt it.
