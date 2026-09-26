"""Stand-in for sim.World (CONTRACTS §2) until workstream A delivers.

12x12 grid: a sand band at x=6 with one safe crossing, rocks, a crater edge,
two science deposits and a dust storm on sols 5-8.
"""

from __future__ import annotations

import math
import random

from contracts.models import (
    DIRECTIONS, Action, ActionOutcome, Metrics, Observation, SimEvent, StepResult, TileView,
)

VIEW_RADIUS = 3
SCAN_RADIUS = 5


class FakeWorld:
    def __init__(self, seed: int, planet: str = "mars", size: int = 12):
        self.seed, self.planet, self.size = seed, planet, size
        self.terrain = [["regolith"] * size for _ in range(size)]
        self.slip: dict[tuple[int, int], float] = {}
        for y in range(2, 10):
            self.terrain[y][6] = "sand"
            self.slip[(6, y)] = 0.8
        self.slip[(6, 6)] = 0.2  # the one safe crossing
        for x, y in [(8, 2), (9, 2), (8, 3), (9, 3)]:
            self.terrain[y][x] = "rocks"
        for y in range(9, 12):
            self.terrain[y][10] = "crater_edge"
        self.science = {(9, 6): 15.0, (3, 10): 8.0}
        self.collected: set[tuple[int, int]] = set()
        self.start = (2, 6)
        self.pos = self.start
        self.battery, self.wheel, self.dust = 80.0, 1.0, 0.02
        self.stuck, self.alive, self.sol = False, True, 0
        self.signals: list[str] = []
        self._science_total = 0.0
        self._distance = self._stuck_sols = self._incidents = 0
        self._min_battery = self.battery

    # --- helpers ---------------------------------------------------------

    def _tau(self, sol: int) -> float:
        return {3: 1.2, 4: 1.9}.get(sol, 3.5 if 5 <= sol <= 8 else 0.5)

    def _rng(self) -> random.Random:
        return random.Random(self.seed * 1000 + self.sol)

    def _in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size

    def _event(self, events: list[SimEvent], type_: str, severity: str, **details) -> None:
        if severity in ("major", "critical"):
            self._incidents += 1
        events.append(SimEvent(type=type_, severity=severity, sol=self.sol, pos=self.pos, details=details))

    def _drain(self, pct: float, events: list[SimEvent]) -> None:
        before = self.battery
        self.battery -= pct
        self._min_battery = min(self._min_battery, self.battery)
        if before >= 25 > self.battery:
            self._event(events, "BATTERY_LOW", "minor", battery=round(self.battery, 1))
        if before >= 10 > self.battery:
            self._event(events, "BATTERY_CRITICAL", "major", battery=round(self.battery, 1))
        if self.battery <= 0 and self.alive:
            self.alive = False
            self._event(events, "DEATH", "critical", cause="battery")

    # --- CONTRACTS §2 API --------------------------------------------------

    def observe(self) -> Observation:
        px, py = self.pos
        tiles = [
            TileView(x=x, y=y, terrain=self.terrain[y][x])
            for y in range(py - VIEW_RADIUS, py + VIEW_RADIUS + 1)
            for x in range(px - VIEW_RADIUS, px + VIEW_RADIUS + 1)
            if self._in_bounds(x, y)
        ]
        tau = self._tau(self.sol)
        return Observation(
            sol=self.sol, pos=self.pos, battery=round(self.battery, 1), wheel_health=round(self.wheel, 2),
            panel_dust=round(self.dust, 3), stuck=self.stuck, tau=tau,
            tau_trend=round(tau - self._tau(self.sol - 1), 2), local_tiles=tiles, signals=list(self.signals),
        )

    def action_cost(self, action: Action) -> float:
        if action.tool == "move":
            if self.stuck:
                return 3.0
            return 2.0 * int(action.args.get("steps", 1)) * (2 if self.wheel < 0.4 else 1)
        return {"probe_terrain": 1.0, "scan": 3.0, "drill": 6.0}.get(action.tool, 0.0)

    def step(self, actions: list[Action]) -> StepResult:
        executed: list[ActionOutcome] = []
        events: list[SimEvent] = []
        sheltered, gained = False, 0.0
        rng = self._rng()
        for a in actions:
            if not self.alive:
                break
            if sheltered:
                executed.append(ActionOutcome(action=a, ok=False, message="sheltering; ignored"))
                continue
            ok, msg, data = self._do(a, rng, events)
            if a.tool == "shelter":
                sheltered = True
            if a.tool == "drill" and ok:
                gained += data["science"]
            executed.append(ActionOutcome(action=a, ok=ok, message=msg, data=data))

        if self.alive:
            tau = self._tau(self.sol)
            gain = 30 * math.exp(-tau / 1.5) * (1 - self.dust)
            self._drain((4 if sheltered else 12) - gain, events)
            self.battery = min(100.0, self.battery)
            self.dust = min(0.9, self.dust + 0.005)
            prev = self._tau(self.sol - 1)
            if prev <= 2.0 < tau:
                self._event(events, "STORM_ONSET", "info", tau=tau)
            if prev > 2.0 >= tau:
                self._event(events, "STORM_END", "info", tau=tau)
        if self.stuck:
            self._stuck_sols += 1
        self.sol += 1
        return StepResult(sol=self.sol - 1, executed=executed, events=events, observation=self.observe(),
                          alive=self.alive, science_gained=gained)

    def _do(self, a: Action, rng: random.Random, events: list[SimEvent]) -> tuple[bool, str, dict]:
        if a.tool == "move":
            return self._move(a, rng, events)
        if a.tool == "probe_terrain":
            dx, dy = DIRECTIONS[a.args.get("direction", "N")]
            x, y = self.pos[0] + dx, self.pos[1] + dy
            if not self._in_bounds(x, y):
                return False, "edge of map", {}
            self._drain(1.0, events)
            slip = self.slip.get((x, y), 0.05) + rng.uniform(-0.05, 0.05)
            return True, f"slip at ({x},{y}) ~{slip:.2f}", {"tile": [x, y], "slip": round(max(0.0, slip), 2)}
        if a.tool == "scan":
            self._drain(3.0, events)
            px, py = self.pos
            sigs = [f"anomaly at ({x},{y}) strength {0.5 + v / 40:.2f}" for (x, y), v in self.science.items()
                    if (x, y) not in self.collected and abs(x - px) <= SCAN_RADIUS and abs(y - py) <= SCAN_RADIUS]
            if rng.random() < 0.3:
                fx, fy = rng.randrange(self.size), rng.randrange(self.size)
                sigs.append(f"anomaly at ({fx},{fy}) strength {rng.uniform(0.4, 0.7):.2f}")
            self.signals = sigs
            return True, f"{len(sigs)} signals", {"signals": sigs}
        if a.tool == "drill":
            self._drain(6.0, events)
            value = self.science.get(self.pos)
            if value is None or self.pos in self.collected:
                return False, "nothing found", {"science": 0.0}
            self.collected.add(self.pos)
            self._science_total += value
            self._event(events, "DISCOVERY", "info", value=value)
            return True, f"collected {value} science", {"science": value}
        if a.tool == "shelter":
            return True, "low-power mode", {}
        return True, "waited", {}

    def _move(self, a: Action, rng: random.Random, events: list[SimEvent]) -> tuple[bool, str, dict]:
        if self.stuck:
            self._drain(3.0, events)
            if rng.random() < 0.25:
                self.stuck = False
                self._event(events, "FREED", "info")
                return False, "freed from sand", {}
            return False, "still stuck", {}
        dx, dy = DIRECTIONS[a.args.get("direction", "N")]
        steps = max(1, min(5, int(a.args.get("steps", 1))))
        moved = 0
        for _ in range(steps):
            x, y = self.pos[0] + dx, self.pos[1] + dy
            if not self._in_bounds(x, y):
                return moved > 0, f"moved {moved}; edge of map", {"moved": moved}
            self._drain(2.0 * (2 if self.wheel < 0.4 else 1), events)
            if not self.alive:
                return False, "died", {"moved": moved}
            self.pos, moved = (x, y), moved + 1
            self._distance += 1
            terrain = self.terrain[y][x]
            if terrain == "sand" and rng.random() < self.slip.get((x, y), 0.5):
                self.stuck = True
                self._event(events, "STUCK", "major", terrain="sand", slip=self.slip[(x, y)], action=a.model_dump())
                return False, f"stuck in sand at ({x},{y})", {"moved": moved}
            if terrain == "rocks":
                self.wheel = max(0.0, self.wheel - 0.04)
                self._event(events, "WHEEL_DAMAGE", "major" if self.wheel < 0.4 else "minor", wheel=self.wheel)
            if terrain == "crater_edge" and steps > 1:
                self.wheel = max(0.0, self.wheel - 0.4)
                self._event(events, "FALL", "critical", action=a.model_dump())
                self._drain(20.0, events)
                return False, f"fell at crater edge ({x},{y})", {"moved": moved}
        return True, f"moved {moved} {a.args.get('direction')}", {"moved": moved}

    def metrics(self) -> Metrics:
        return Metrics(sols_survived=self.sol, alive=self.alive, science=self._science_total,
                       distance=self._distance, stuck_sols=self._stuck_sols, incidents=self._incidents,
                       min_battery=round(self._min_battery, 1))

    def snapshot(self) -> dict:
        return {
            "seed": self.seed, "planet": self.planet, "size": self.size, "start": list(self.start),
            "terrain": self.terrain,
            "slip": {f"{x},{y}": s for (x, y), s in self.slip.items()},
            "science": [{"pos": [x, y], "value": v} for (x, y), v in self.science.items()],
            "storms": [{"start_sol": 5, "peak_tau": 3.5, "length": 4}],
        }
