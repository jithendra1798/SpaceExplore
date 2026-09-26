"""Small observation-only policies for tuning and replaying worlds.

These policies are not meant to replace the Explorer LLM. They give the team
repeatable baselines: ``greedy`` makes a direct beeline, while ``careful`` uses
local terrain, probes hazards, and shelters on storm warnings.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from sim.models import Action, Metrics, Observation, StepResult
from sim.world import DIRECTIONS, World

_SIGNAL_RE = re.compile(r"(?:possible )?anomaly at \((-?\d+),\s*(-?\d+)\) strength ([0-9.]+)")
_UPLINK_RE = re.compile(r"UPLINK from Earth: .*?: ([A-Z,]+)$")


class Policy(Protocol):
    name: str

    def actions(self, observation: Observation) -> list[Action]: ...

    def learn(self, result: StepResult) -> None: ...


class _SignalPolicy:
    def __init__(self) -> None:
        self.targets: dict[tuple[int, int], float] = {}
        self.last_scan_sol = -10
        self.position_visits: dict[tuple[int, int], int] = {}
        # After a storm drift the world holds the rover until Earth uplinks a
        # route. Both baselines obey the ground: wait, then drive that route.
        self.holding = False
        self.ground_route: list[str] = []

    def _ground_plan(self, observation: Observation, idle: str = "wait") -> list[Action] | None:
        if self.holding:
            return [Action(tool=idle, args={})]
        if not self.ground_route or observation.stuck:
            return None
        actions: list[Action] = []
        budget = 5
        while self.ground_route and budget > 0:
            direction = self.ground_route[0]
            steps = 0
            while self.ground_route and self.ground_route[0] == direction and budget > 0:
                self.ground_route.pop(0)
                steps += 1
                budget -= 1
            actions.append(Action(tool="move", args={"direction": direction, "steps": steps}))
        return actions

    def learn(self, result: StepResult) -> None:
        signals = [str(signal) for signal in result.observation.signals]
        self.holding = any(signal.startswith("NAV HOLD") for signal in signals)
        for signal in signals:
            match = _UPLINK_RE.search(signal)
            if match:
                self.ground_route = [item for item in match[1].split(",") if item in DIRECTIONS]

        for outcome in result.executed:
            if outcome.action.tool == "scan":
                self.last_scan_sol = result.sol
                for signal in outcome.data.get("signals", []):
                    match = _SIGNAL_RE.search(str(signal))
                    if match:
                        x, y, strength = int(match[1]), int(match[2]), float(match[3])
                        self.targets[(x, y)] = max(strength, self.targets.get((x, y), 0.0))
            elif outcome.action.tool == "drill":
                # A failed drill is useful evidence too: forget that ghost.
                self.targets.pop(result.observation.pos, None)

        pos = result.observation.pos
        self.position_visits[pos] = self.position_visits.get(pos, 0) + 1

    def _target(self, observation: Observation) -> tuple[int, int] | None:
        if not self.targets:
            return None
        x, y = observation.pos
        return max(
            self.targets,
            key=lambda target: self.targets[target] - 0.025 * math.hypot(target[0] - x, target[1] - y),
        )


class RandomPolicy(_SignalPolicy):
    """Unstructured control policy with a seed-local action stream."""

    name = "random"

    def __init__(self, seed: int):
        super().__init__()
        self.rng = random.Random(seed * 31 + 17)

    def actions(self, observation: Observation) -> list[Action]:
        if observation.stuck:
            return [Action(tool="move", args={"direction": self.rng.choice(tuple(DIRECTIONS)), "steps": 1})]
        tool = self.rng.choices(
            ("move", "scan", "drill", "probe_terrain", "shelter", "wait"),
            weights=(0.62, 0.13, 0.08, 0.07, 0.04, 0.06),
        )[0]
        if tool == "move":
            return [Action(tool=tool, args={"direction": self.rng.choice(tuple(DIRECTIONS)), "steps": self.rng.randint(1, 3)})]
        if tool == "probe_terrain":
            return [Action(tool=tool, args={"direction": self.rng.choice(tuple(DIRECTIONS))})]
        return [Action(tool=tool, args={})]


class GreedyPolicy(_SignalPolicy):
    """Head directly toward the strongest known signal, without hazard checks."""

    name = "greedy"

    def actions(self, observation: Observation) -> list[Action]:
        if self.holding:
            return [Action(tool="wait", args={})]
        if observation.stuck:
            return [Action(tool="move", args={"direction": "E", "steps": 1})]
        ground = self._ground_plan(observation)
        if ground:
            return ground

        target = self._target(observation)
        if target == observation.pos:
            return [Action(tool="drill", args={})]
        if target is None:
            if observation.sol - self.last_scan_sol >= 3:
                return [Action(tool="scan", args={})]
            # A measured one-tile sweep avoids spending a full battery bar on
            # blind exploration while still walking directly into the trap.
            return [Action(tool="move", args={"direction": "E", "steps": 1})]

        direction = _direction_toward(observation.pos, target)
        remaining = max(abs(target[0] - observation.pos[0]), abs(target[1] - observation.pos[1]))
        return [Action(tool="move", args={"direction": direction, "steps": min(5, remaining)})]


class CarefulPolicy(_SignalPolicy):
    """Probe exposed hazards, route around high-risk terrain, and shelter early."""

    name = "careful"
    max_slip = 0.3

    def __init__(self) -> None:
        super().__init__()
        self.probed_slip: dict[tuple[int, int], float] = {}
        self.energy_deficit_mode = False

    def learn(self, result: StepResult) -> None:
        super().learn(result)
        # Detect cold/low-light presets from the first few energy budgets. The
        # policy only sees telemetry, so it does not need privileged planet
        # metadata to learn that shelter should be the default.
        if result.sol <= 3 and result.observation.battery < 98.0:
            self.energy_deficit_mode = True
        for outcome in result.executed:
            if outcome.action.tool == "probe_terrain" and outcome.ok:
                data = outcome.data
                self.probed_slip[(int(data["x"]), int(data["y"]))] = float(data["slip"])

    def actions(self, observation: Observation) -> list[Action]:
        if self.holding:
            return [Action(tool="shelter", args={})]
        if observation.stuck:
            # The sim gives each failed move a 25% chance to free the rover.
            prefix = [Action(tool="shelter", args={})] if self.energy_deficit_mode else []
            return prefix + [Action(tool="move", args={"direction": "E", "steps": 1})]

        if observation.tau > 1.8 or (observation.tau_trend > 0.6 and observation.tau > 1.2):
            return [Action(tool="shelter", args={})]
        ground = self._ground_plan(observation, idle="shelter")
        if ground:
            return ground

        # A cold or persistently low-light world is recognized from early
        # battery telemetry. Keep harvesting power while still making progress.
        prefix = [Action(tool="shelter", args={})] if self.energy_deficit_mode else []

        target = self._target(observation)
        if target == observation.pos:
            return prefix + [Action(tool="drill", args={})]
        if target is None:
            if observation.sol - self.last_scan_sol >= 3:
                return prefix + [Action(tool="scan", args={})]
            target = (observation.pos[0] + 6, observation.pos[1])

        local = {(tile.x, tile.y): tile.terrain for tile in observation.local_tiles}
        x, y = observation.pos
        candidate_scores: list[tuple[float, str, tuple[int, int]]] = []
        for direction, (dx, dy) in DIRECTIONS.items():
            nx, ny = x + dx, y + dy
            terrain = local.get((nx, ny), "unknown")
            penalty = {
                "sand": 18.0,
                "ice": 14.0,
                "crater_edge": 24.0,
                "rocks": 1.2,
                "unknown": 0.6,
            }.get(terrain, 0.0)
            measured = self.probed_slip.get((nx, ny))
            if terrain in {"sand", "ice"} and measured is not None:
                penalty = 5.0 if measured <= self.max_slip else 28.0
            visits = self.position_visits.get((nx, ny), 0)
            goal_distance = math.hypot(target[0] - nx, target[1] - ny)
            candidate_scores.append((goal_distance + penalty + visits * 1.5, direction, (nx, ny)))

        candidate_scores.sort()
        if not candidate_scores:
            return prefix + [Action(tool="wait", args={})]

        _, direction, next_pos = candidate_scores[0]
        terrain = local.get(next_pos)
        actions: list[Action] = []

        # Probe the tempting direct route even when the safer route wins. This
        # builds useful, durable evidence without making the rover gamble.
        direct = _direction_toward(observation.pos, target)
        ddx, ddy = DIRECTIONS[direct]
        direct_pos = (x + ddx, y + ddy)
        if local.get(direct_pos) in {"sand", "ice"} and direct_pos not in self.probed_slip:
            actions.append(Action(tool="probe_terrain", args={"direction": direct}))

        # A previously low probe can authorize a measured crossing. Probe again
        # this sol so the runtime guardrail can enforce its same-sol condition.
        if terrain in {"sand", "ice"}:
            if self.probed_slip.get(next_pos, 1.0) > self.max_slip:
                alternate = next((item for item in candidate_scores[1:] if local.get(item[2]) not in {"sand", "ice", "crater_edge"}), None)
                if alternate is not None:
                    _, direction, next_pos = alternate
                else:
                    return prefix + (actions or [Action(tool="probe_terrain", args={"direction": direction})])
            else:
                actions.append(Action(tool="probe_terrain", args={"direction": direction}))

        actions.append(Action(tool="move", args={"direction": direction, "steps": 1}))
        return prefix + actions


@dataclass(slots=True)
class PolicyRun:
    seed: int
    planet: str
    policy: str
    metrics: Metrics
    results: list[StepResult] = field(default_factory=list)
    world: World | None = None


def make_policy(name: str, seed: int) -> Policy:
    policies: dict[str, Any] = {
        "random": lambda: RandomPolicy(seed),
        "greedy": GreedyPolicy,
        "careful": CarefulPolicy,
    }
    try:
        return policies[name]()
    except KeyError as exc:
        raise ValueError(f"unknown policy {name!r}; choose from {', '.join(policies)}") from exc


def run_policy(
    seed: int,
    policy: str = "greedy",
    planet: str = "mars",
    size: int = 40,
    max_sols: int = 30,
) -> PolicyRun:
    """Run a reference policy using only observations, returning full replay data."""
    world = World(seed=seed, planet=planet, size=size)
    agent = make_policy(policy, seed)
    results: list[StepResult] = []
    for _ in range(max_sols):
        if not world.alive:
            break
        result = world.step(agent.actions(world.observe()))
        results.append(result)
        agent.learn(result)
    return PolicyRun(seed, planet, policy, world.metrics(), results, world)


def _direction_toward(origin: tuple[int, int], target: tuple[int, int]) -> str:
    dx = target[0] - origin[0]
    dy = target[1] - origin[1]
    sx = 0 if dx == 0 else (1 if dx > 0 else -1)
    sy = 0 if dy == 0 else (1 if dy > 0 else -1)
    for name, delta in DIRECTIONS.items():
        if delta == (sx, sy):
            return name
    return "E"


__all__ = [
    "CarefulPolicy",
    "GreedyPolicy",
    "PolicyRun",
    "RandomPolicy",
    "make_policy",
    "run_policy",
]
