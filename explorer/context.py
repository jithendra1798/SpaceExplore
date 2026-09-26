"""Builds the Explorer's prompt from the harness config (CONTRACTS §3.2).

The system prompt depends only on the harness version, so it stays stable for a
whole mission. Everything that changes per sol goes in the user message.
"""

from __future__ import annotations

from contracts.constitution import BATTERY_FLOOR
from contracts.models import HarnessConfig, Observation

TOOL_DOCS = {
    "move": "move(direction N|NE|E|SE|S|SW|W|NW, steps 1-5): drive; 2% battery per step. Sand can trap you; rocks wear wheels; crater edges must be entered 1 step at a time.",
    "probe_terrain": "probe_terrain(direction): measure slip of the adjacent tile; 1%. Slip above ~0.3 means you will likely get stuck.",
    "scan": "scan(): list anomaly signals within 5 tiles; 3%. About 30% of signals are false.",
    "drill": "drill(): collect science on the current tile; 6%.",
    "shelter": "shelter(): low-power mode for the rest of the sol; night heater costs 4% instead of 12%. Ends your plan for the sol.",
    "wait": "wait(): do nothing.",
}

TERRAIN_GLYPH = {"bedrock": "B", "regolith": ".", "sand": "S", "rocks": "K", "crater_edge": "C", "ice": "I"}

GUARDRAIL_DOCS = {
    "min_battery_for_move": "No moves while battery < {threshold}%.",
    "avoid_terrain": "Moves onto {terrain} are stopped unless that tile was probed with slip <= {max_slip}. Probe first, move next sol.",
    "max_steps_per_move": "Moves are clipped to {n} steps.",
    "shelter_when_tau_above": "When tau > {tau} your plan is replaced by shelter.",
    "no_drill_below_battery": "No drilling while battery < {threshold}%.",
}


def build_system_prompt(h: HarnessConfig) -> str:
    rules = "\n".join(f"{i}. {r.text}" for i, r in enumerate(h.rules, 1)) or "(none)"
    rails = "\n".join(f"- [{g.id}] " + GUARDRAIL_DOCS[g.type].format(**g.params) for g in h.guardrails) or "(none)"
    tools = "\n".join(f"- {TOOL_DOCS[t]}" for t in h.enabled_tools())
    return f"""{h.system_prompt}

Coordinates: x grows east, y grows south (N is y-1). Solar panels charge ~21% on a clear sol (tau 0.5) and almost nothing when tau > 2; the night heater costs 12%.

Tools you may use this mission:
{tools}

Mission rules (harness v{h.version}):
{rules}

Guardrails enforced by the runtime (violating actions are dropped):
{rails}
- [C1] No action may take battery below {BATTERY_FLOOR:.0f}%.

Every sol, call submit_plan exactly once with your reasoning and up to {h.params.max_actions_per_sol} actions, executed in order."""


def render_local_map(obs: Observation, known: dict[tuple[int, int], str]) -> str:
    px, py = obs.pos
    xs = range(px - 3, px + 4)
    lines = ["     " + " ".join(f"{x:>2}" for x in xs)]
    for y in range(py - 3, py + 4):
        row = []
        for x in xs:
            row.append(" @" if (x, y) == obs.pos else f" {TERRAIN_GLYPH.get(known.get((x, y), ''), '?')}")
        lines.append(f"y={y:>2} " + "".join(f"{c:>3}" for c in row))
    return "\n".join(lines) + "\nLegend: @ rover, . regolith, B bedrock, S sand, K rocks, C crater edge, I ice, ? unknown"


def build_sol_message(
    obs: Observation,
    known_terrain: dict[tuple[int, int], str],
    recent: list[str],
    memories: list[dict],
    hazards: list[dict],
    last_signals: tuple[int, list[str]] | None,
    drilled: set[tuple[int, int]] = frozenset(),
) -> str:
    parts = [
        f"SOL {obs.sol}",
        f"Position {obs.pos}. Battery {obs.battery:.0f}%. Wheels {obs.wheel_health:.2f}. "
        f"Panel dust {obs.panel_dust:.2f}. Stuck: {'YES' if obs.stuck else 'no'}.",
        f"Weather: tau {obs.tau:.1f} (change since yesterday {obs.tau_trend:+.1f}).",
        "Local terrain:\n" + render_local_map(obs, known_terrain),
    ]
    fresh = lambda sigs: [s for s in sigs if not any(f"({x},{y})" in s.replace(" ", "") for x, y in drilled)]
    if last_signals and fresh(last_signals[1]):
        parts.append(f"Anomaly signals from scan on sol {last_signals[0]}: " + "; ".join(fresh(last_signals[1])))
    elif fresh(obs.signals):
        parts.append("Anomaly signals: " + "; ".join(fresh(obs.signals)))
    if drilled:
        parts.append("Already drilled (nothing left there): " + ", ".join(f"({x},{y})" for x, y in sorted(drilled)))
    if recent:
        parts.append("Recent sols:\n" + "\n".join(f"- {r}" for r in recent))
    if hazards:
        parts.append("Known hazards nearby:\n" + "\n".join(
            f"- ({h['loc'][0]},{h['loc'][1]}) {h.get('terrain')}"
            + (f" slip {h['slip_probed']:.2f}" if h.get("slip_probed") is not None else "")
            for h in hazards))
    if memories:
        parts.append("Relevant memories:\n" + "\n".join(f"- [{m.get('kind')}] {m.get('text')}" for m in memories))
    parts.append("Call submit_plan now.")
    return "\n\n".join(parts)
