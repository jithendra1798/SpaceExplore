"""Dry run: ask the Engineer for a patch on one incident and validate it. Writes nothing.

    uv run python -m engineer.propose --incident fixtures/incident.json          # v1 from contracts, no Atlas
    uv run python -m engineer.propose --event-id <events _id>                    # real incident + telemetry from Atlas
"""

from __future__ import annotations

import argparse
import json

from dotenv import load_dotenv

from contracts import load_harness_v1
from engineer import store
from engineer.agent import IncidentContext, build_context, build_message, get_engineer
from engineer.patch import apply_patch, describe_ops, validate_patch


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description="Propose and validate one harness patch without saving it.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--incident", help="incident event JSON file (uses harness v1, no Atlas)")
    src.add_argument("--event-id", help="an `events` _id in Atlas (uses the active harness)")
    p.add_argument("--engineer", choices=["llm", "scripted"], default="llm")
    p.add_argument("--show-prompt", action="store_true")
    args = p.parse_args()

    if args.incident:
        with open(args.incident) as f:
            ctx = IncidentContext(event=json.load(f), harness=load_harness_v1())
    else:
        d = store.database()
        event = store.get_event(d, args.event_id)
        if event is None:
            raise SystemExit(f"no event {args.event_id}")
        ctx = build_context(d, event, store.get_active(d))
    if args.show_prompt:
        print(build_message(ctx), "\n" + "-" * 80)

    patch = get_engineer(args.engineer).propose(ctx)
    print(f"diagnosis: {patch.diagnosis}\nrationale: {patch.rationale}\nlesson:    {patch.lesson}")
    print("ops:\n  " + "\n  ".join(describe_ops(patch)))
    errors = validate_patch(ctx.harness, patch)
    if errors:
        print("INVALID:\n  " + "\n  ".join(errors))
    else:
        cand = apply_patch(ctx.harness, patch)
        print(f"valid -> candidate v{cand.version}: tools {cand.enabled_tools()}, "
              f"guardrails {[(g.id, g.type, g.params) for g in cand.guardrails]}")


if __name__ == "__main__":
    main()
