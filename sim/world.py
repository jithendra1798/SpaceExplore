"""Seeded rover world used by the Explorer and the demo.

The simulator deliberately has no database, LLM, or network dependency.  A
world's terrain is generated once from ``Random(seed)``; each sol gets its own
runtime stream from ``Random(seed * 1000 + sol)``.  Observations omit science
locations and sand slip unless a scan or probe has revealed them.
"""

from __future__ import annotations

import math
import random
from typing import Any, Iterable

from sim.models import Action, ActionOutcome, Metrics, Observation, SimEvent, StepResult, TileView

TERRAIN = ("bedrock", "regolith", "sand", "rocks", "crater_edge")
# Dust storms blind the navigation cameras. Driving while tau is above this
# risks losing visual odometry: the rover slides off course and must hold
# until Earth fixes its position from orbit and uplinks a new route.
NAV_DRIFT_TAU = 2.0
UPLINK_DELAY_SOLS = 2
ROUTE_AVOID = frozenset({"sand", "rocks", "crater_edge", "ice"})

DIRECTIONS: dict[str, tuple[int, int]] = {
    "N": (0, -1),
    "NE": (1, -1),
    "E": (1, 0),
    "SE": (1, 1),
    "S": (0, 1),
    "SW": (-1, 1),
    "W": (-1, 0),
    "NW": (-1, -1),
}


class World:
    """A deterministic 2D rover mission.

    ``sol`` is the number of completed sols and starts at zero.  ``step``
    executes an ordered action plan for exactly one sol, unless the rover dies
    partway through it.
    """

    def __init__(self, seed: int, planet: str = "mars", size: int = 40):
        if size < 8:
            raise ValueError("size must be at least 8 so the rover has room to explore")
        if planet not in {"mars", "icy"}:
            raise ValueError("planet must be 'mars' or 'icy'")

        self.seed = int(seed)
        self.planet = planet
        self.size = int(size)
        self._generation_rng = random.Random(self.seed)
        self.terrain: list[list[str]] = [
            [self._generation_rng.choices(("regolith", "bedrock"), weights=(0.76, 0.24))[0]
             for _ in range(self.size)]
            for _ in range(self.size)
        ]
        self.slip: dict[tuple[int, int], float] = {}
        self.science: dict[tuple[int, int], float] = {}
        self.storms: list[dict[str, float | int]] = []
        self.craters: list[dict[str, int]] = []

        self.start = (min(4, self.size - 3), self.size // 2)
        self._generate_geology()
        self._generate_science()
        self._generate_weather()
        self._apply_showcase_scenario()

        self.x, self.y = self.start
        self.sol = 0
        self.alive = True
        self.battery = 100.0
        self.wheel_health = 1.0
        self.panel_dust = 0.0
        self.stuck = False
        self.science_collected = 0.0
        self.distance = 0
        self.stuck_sols = 0
        self.incidents = 0
        self.min_battery = 100.0
        self._tau = 0.5
        self._previous_tau = 0.5
        self._last_signals: list[str] = []
        self._events: list[SimEvent] = []
        self._runtime_rng = random.Random(self.seed * 1000)
        self._low_battery_announced = False
        self._critical_battery_announced = False
        # Navigation hold after a storm drift: (uplink sol, route destination).
        self.uplink_due_sol: int | None = None
        self._uplink_destination: tuple[int, int] | None = None

    # ------------------------------------------------------------------
    # World generation
    # ------------------------------------------------------------------
    def _inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size

    def _set_terrain(self, x: int, y: int, terrain: str) -> None:
        if self._inside(x, y):
            self.terrain[y][x] = terrain
            if terrain == "sand":
                self.slip[(x, y)] = self._generation_rng.uniform(0.2, 0.9)
            else:
                self.slip.pop((x, y), None)

    def _generate_geology(self) -> None:
        rng = self._generation_rng
        sx, sy = self.start

        # Soft-sand basins have irregular edges instead of perfect circles.
        for _ in range(max(4, self.size // 6)):
            cx = rng.randrange(self.size)
            cy = rng.randrange(self.size)
            rx = rng.randint(1, max(2, self.size // 16))
            ry = rng.randint(1, max(2, self.size // 18))
            for y in range(max(0, cy - ry), min(self.size, cy + ry + 1)):
                for x in range(max(0, cx - rx), min(self.size, cx + rx + 1)):
                    distance = ((x - cx) / max(rx, 1)) ** 2 + ((y - cy) / max(ry, 1)) ** 2
                    if distance <= 1.0 and rng.random() < 0.84:
                        self._set_terrain(x, y, "sand")

        # Rock fields are elongated so a short detour can often avoid them.
        for _ in range(max(4, self.size // 7)):
            cx = rng.randrange(self.size)
            cy = rng.randrange(self.size)
            horizontal = rng.random() < 0.5
            radius = rng.randint(1, max(2, self.size // 18))
            for offset in range(-radius, radius + 1):
                x = cx + offset if horizontal else cx + rng.randint(-1, 1)
                y = cy + rng.randint(-1, 1) if horizontal else cy + offset
                if self._inside(x, y) and rng.random() < 0.82:
                    self._set_terrain(x, y, "rocks")

        # Crater rims are broken rings. Their geometry is visible; the danger
        # comes from crossing a rim quickly rather than from hidden terrain.
        for _ in range(max(2, self.size // 12)):
            cx = rng.randrange(3, self.size - 3)
            cy = rng.randrange(3, self.size - 3)
            radius = rng.randint(2, max(3, self.size // 10))
            self.craters.append({"center": [cx, cy], "radius": radius})
            for y in range(max(0, cy - radius - 1), min(self.size, cy + radius + 2)):
                for x in range(max(0, cx - radius - 1), min(self.size, cx + radius + 2)):
                    radial = math.hypot(x - cx, y - cy)
                    if radius - 0.55 <= radial <= radius + 0.55 and rng.random() < 0.76:
                        self._set_terrain(x, y, "crater_edge")

        if self.planet == "icy":
            # The moon's crevasses appear as narrow, branching ice seams.
            for _ in range(max(4, self.size // 6)):
                x = rng.randrange(self.size)
                y = rng.randrange(self.size)
                dx, dy = rng.choice(((1, 0), (0, 1), (1, 1), (-1, 1)))
                length = rng.randint(3, max(4, self.size // 5))
                for index in range(length):
                    px, py = x + dx * index, y + dy * index
                    if self._inside(px, py) and rng.random() < 0.7:
                        self._set_terrain(px, py, "ice")
                        # Ice slip is fixed and visible by terrain type; the
                        # probe reports it with the same sensor noise as sand.
                        self.slip[(px, py)] = 0.42

        # Keep the launch pad safe, whatever the random geology produced.
        for y in range(max(0, sy - 1), min(self.size, sy + 2)):
            for x in range(max(0, sx - 1), min(self.size, sx + 2)):
                if self.terrain[y][x] in {"sand", "rocks", "crater_edge", "ice"}:
                    self.terrain[y][x] = "regolith"
                    self.slip.pop((x, y), None)

    def _generate_science(self) -> None:
        rng = self._generation_rng
        desired = rng.randint(8, 12)
        sx, sy = self.start
        attempts = 0
        while len(self.science) < desired and attempts < self.size * self.size * 3:
            attempts += 1
            x, y = rng.randrange(self.size), rng.randrange(self.size)
            if math.hypot(x - sx, y - sy) < 5:
                continue
            if self.terrain[y][x] in {"crater_edge", "ice"} or (x, y) in self.science:
                continue
            self.science[(x, y)] = float(rng.randint(5, 20))

    def _generate_weather(self) -> None:
        rng = self._generation_rng
        peak = 12 if self.seed == 42 or self.seed in {101, 102, 103} else rng.randint(9, 17)
        peak_tau = 4.8 if self.planet == "icy" else 4.0
        self.storms = [{"start_sol": peak, "peak_tau": peak_tau, "length": 5}]

    def _apply_showcase_scenario(self) -> None:
        """Guarantee a learnable direct-route trap on demo/eval worlds."""
        if self.seed != 42 and self.seed not in {101, 102, 103}:
            return

        sx, sy = self.start
        trap_x = min(self.size - 3, sx + 4)
        target_x = min(self.size - 2, sx + 8)

        # A one-tile high-slip seam makes the greedy route memorable while
        # leaving a safe north/south route for a cautious rover to discover.
        for x in range(sx + 1, target_x + 1):
            self.terrain[sy][x] = "regolith"
            self.slip.pop((x, sy), None)
        self.terrain[sy][trap_x] = "sand"
        self.slip[(trap_x, sy)] = 0.88 if self.seed == 42 else 0.9

        # Give seed 42 a clear, nearest deposit directly beyond the trap.
        # The held-out seeds use the same geometry with independent geology.
        nearest_distance = target_x - sx
        far_science = {
            pos: value
            for pos, value in self.science.items()
            if math.hypot(pos[0] - sx, pos[1] - sy) > nearest_distance + 1
        }
        # Leave room for the guaranteed route deposit while preserving the
        # contract's 8–12 discoveries per planet.
        if len(far_science) > 11:
            far_science = dict(list(far_science.items())[:11])
        self.science = far_science
        self.science[(target_x, sy)] = 18.0 if self.seed == 42 else 16.0
        while len(self.science) < 8:
            x, y = self._generation_rng.randrange(self.size), self._generation_rng.randrange(self.size)
            if math.hypot(x - sx, y - sy) <= nearest_distance + 1:
                continue
            if self.terrain[y][x] not in {"crater_edge", "ice"} and (x, y) not in self.science:
                self.science[(x, y)] = float(self._generation_rng.randint(5, 20))

        self.storms = [{"start_sol": 12, "peak_tau": 4.0, "length": 5}]

    # ------------------------------------------------------------------
    # Public contract
    # ------------------------------------------------------------------
    def observe(self) -> Observation:
        radius = 3
        tiles = [
            TileView(x=x, y=y, terrain=self.terrain[y][x])
            for y in range(max(0, self.y - radius), min(self.size, self.y + radius + 1))
            for x in range(max(0, self.x - radius), min(self.size, self.x + radius + 1))
        ]
        return Observation(
            sol=self.sol,
            pos=(self.x, self.y),
            battery=round(self.battery, 2),
            wheel_health=round(self.wheel_health, 3),
            panel_dust=round(self.panel_dust, 3),
            stuck=self.stuck,
            tau=round(self._tau, 3),
            tau_trend=round(self._tau - self._previous_tau, 3),
            local_tiles=tiles,
            signals=list(self._last_signals),
        )

    def action_cost(self, action: Any) -> float:
        """Predict the battery cost of an action in the current state."""
        tool, args = self._action_parts(action)
        if tool == "move":
            if self.stuck:
                return 3.0
            steps = self._bounded_int(args.get("steps", 1), 1, 5, default=1)
            return float(steps * 2 * (2 if self.wheel_health < 0.4 else 1))
        return {"probe_terrain": 1.0, "scan": 3.0, "drill": 6.0}.get(tool, 0.0)

    def step(self, actions: list[Any]) -> StepResult:
        """Run one sol of ordered actions and return outcomes, events, metrics."""
        self._events = []
        self._last_signals = []
        if not self.alive:
            return StepResult(
                sol=self.sol,
                executed=[],
                events=[],
                observation=self.observe(),
                alive=False,
                science_gained=0.0,
            )

        self.sol += 1
        self._runtime_rng = random.Random(self.seed * 1000 + self.sol)
        self._previous_tau = self._tau
        self._tau = self._tau_for_sol(self.sol)
        self._maybe_emit_storm_transition()
        executed: list[ActionOutcome] = []
        science_before = self.science_collected
        sheltered = False

        for raw_action in list(actions):
            if not self.alive:
                break
            action = self._normalize_action(raw_action)
            ok, message, data = self._execute(action)
            executed.append(ActionOutcome(action=action, ok=ok, message=message, data=data))
            if action.tool == "shelter" and ok:
                sheltered = True

        if self.alive:
            self._finish_sol(sheltered=sheltered)
        if self.alive and self.uplink_due_sol is not None:
            if self.sol >= self.uplink_due_sol:
                self._deliver_uplink()
            else:
                self._last_signals.append(
                    "NAV HOLD: position lost in the dust storm; driving is disabled until Earth "
                    f"uplinks a position fix and route (expected end of sol {self.uplink_due_sol})."
                )
        if self.stuck:
            self.stuck_sols += 1
            if self.alive and self.wheel_health <= 0:
                self._kill("wheels failed while the rover was stuck")

        return StepResult(
            sol=self.sol,
            executed=executed,
            events=list(self._events),
            observation=self.observe(),
            alive=self.alive,
            science_gained=round(self.science_collected - science_before, 2),
        )

    def metrics(self) -> Metrics:
        return Metrics(
            sols_survived=self.sol,
            alive=self.alive,
            science=round(self.science_collected, 2),
            distance=self.distance,
            stuck_sols=self.stuck_sols,
            incidents=self.incidents,
            min_battery=round(self.min_battery, 2),
        )

    def snapshot(self) -> dict[str, Any]:
        """Return UI ground truth; never pass this object into an agent prompt."""
        return {
            "seed": self.seed,
            "planet": self.planet,
            "size": self.size,
            "start": [self.start[0], self.start[1]],
            "terrain": [list(row) for row in self.terrain],
            "slip": {f"{x},{y}": round(value, 3) for (x, y), value in sorted(self.slip.items())},
            "science": [
                {"pos": [x, y], "value": value}
                for (x, y), value in sorted(self.science.items())
            ],
            "storms": [dict(storm) for storm in self.storms],
            "craters": [dict(crater) for crater in self.craters],
        }

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------
    @staticmethod
    def _action_parts(action: Any) -> tuple[str, dict[str, Any]]:
        if isinstance(action, dict):
            tool = action.get("tool", "wait")
            args = action.get("args", {})
        else:
            tool = getattr(action, "tool", "wait")
            args = getattr(action, "args", {})
        return str(tool), dict(args or {})

    def _normalize_action(self, action: Any) -> Action:
        tool, args = self._action_parts(action)
        return Action(tool=tool, args=args)

    @staticmethod
    def _bounded_int(value: Any, low: int, high: int, default: int) -> int:
        try:
            return max(low, min(high, int(value)))
        except (TypeError, ValueError):
            return default

    def _execute(self, action: Action) -> tuple[bool, str, dict[str, Any]]:
        tool, args = self._action_parts(action)
        if tool == "move":
            return self._move(args)
        if tool == "probe_terrain":
            return self._probe(args)
        if tool == "scan":
            return self._scan()
        if tool == "drill":
            return self._drill()
        if tool == "shelter":
            return True, "Shelter engaged for the rest of this sol.", {}
        if tool == "wait":
            return True, "The rover waits and monitors its instruments.", {}
        return False, f"Unknown tool: {tool}", {}

    def _move(self, args: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        direction = str(args.get("direction", "E")).upper()
        if direction not in DIRECTIONS:
            return False, f"Invalid direction: {direction}", {}
        steps = self._bounded_int(args.get("steps", 1), 1, 5, default=1)
        dx, dy = DIRECTIONS[direction]

        if self.uplink_due_sol is not None:
            return False, (
                "Holding position: the rover lost its position fix in the dust and waits for Earth "
                f"to uplink a new route (expected end of sol {self.uplink_due_sol})."
            ), {"holding": True}

        if self.stuck:
            if not self._pay(3.0):
                return False, "Battery depleted while attempting to free the rover.", {}
            if self._runtime_rng.random() < 0.25:
                self.stuck = False
                self._emit("FREED", "info", {"action": "move", "direction": direction})
                return False, "The rover is free, but made no distance this sol.", {"freed": True}
            return False, "The wheels churn in the sand; the rover remains stuck.", {"freed": False}

        start = (self.x, self.y)
        planned = (
            max(0, min(self.size - 1, self.x + dx * steps)),
            max(0, min(self.size - 1, self.y + dy * steps)),
        )
        headings = [(dx, dy)] * steps
        drifting = self._tau > NAV_DRIFT_TAU and self._runtime_rng.random() < min(
            0.85, 0.35 + 0.25 * (self._tau - NAV_DRIFT_TAU)
        )
        if drifting:
            # Without visual odometry the wheels slide on wind-drifted fines and
            # the heading creeps to one side, step by step.
            order = list(DIRECTIONS)
            side = 1 if self._runtime_rng.random() < 0.5 else -1
            veer = order[(order.index(direction) + side) % len(order)]
            headings = [
                DIRECTIONS[veer] if index > 0 and self._runtime_rng.random() < 0.6 else (dx, dy)
                for index in range(steps)
            ]
            headings.append(DIRECTIONS[veer])
        trail: list[list[int]] = []

        def drift_result(ok: bool, message: str, data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
            data = {**data, "trail": trail}
            if drifting and self.alive and (self.x, self.y) != planned:
                self._begin_nav_hold(start, planned)
                data["drift"] = {"planned": list(planned), "actual": [self.x, self.y]}
                message = (
                    f"{message} Visual odometry lost lock in the dust: the rover ended at "
                    f"({self.x},{self.y}) instead of ({planned[0]},{planned[1]}) and is holding for Earth."
                )
                ok = False
            return ok, message, data

        traversed = 0
        for dx, dy in headings:
            step_cost = 2.0 * (2 if self.wheel_health < 0.4 else 1)
            if not self._pay(step_cost):
                return drift_result(False, "Battery depleted during the move.", {"steps_moved": traversed})
            nx, ny = self.x + dx, self.y + dy
            if not self._inside(nx, ny):
                return drift_result(traversed > 0, "The rover reached the edge of the mapped world.", {"steps_moved": traversed})

            self.x, self.y = nx, ny
            trail.append([nx, ny])
            traversed += 1
            self.distance += 1
            terrain = self.terrain[ny][nx]
            self.panel_dust = min(0.95, self.panel_dust + (0.0015 if terrain == "sand" else 0.0003))

            if terrain == "rocks":
                self.wheel_health = max(0.0, self.wheel_health - 0.04)
                severity = "major" if self.wheel_health < 0.4 else "minor"
                self._emit("WHEEL_DAMAGE", severity, {"terrain": "rocks", "wheel_health": round(self.wheel_health, 3)})
            elif terrain == "sand":
                slip = self.slip.get((nx, ny), 0.5)
                if self._runtime_rng.random() < slip:
                    self.stuck = True
                    self._emit("STUCK", "major", {"terrain": "sand", "slip": round(slip, 3), "action": {"tool": "move", "args": args}})
                    return drift_result(False, f"The rover bogged down in sand with {slip:.0%} slip risk.", {"steps_moved": traversed, "stuck": True})
            elif terrain == "ice":
                if self._runtime_rng.random() < self.slip.get((nx, ny), 0.42):
                    self.wheel_health = max(0.0, self.wheel_health - 0.08)
                    self._emit("ICE_SLIP", "minor", {"terrain": "ice", "wheel_health": round(self.wheel_health, 3)})
                    return drift_result(False, "A wheel skidded along an icy crevasse; the rover stopped to stabilize.", {"steps_moved": traversed, "ice_slip": True})
            elif terrain == "crater_edge" and len(headings) > 1:
                self.wheel_health = max(0.0, self.wheel_health - 0.4)
                self._emit("FALL", "critical", {"terrain": "crater_edge", "steps": steps})
                self._pay(20.0)
                return drift_result(False, "The rover crossed a crater rim too quickly and fell into the bowl.", {"steps_moved": traversed, "fall": True})

            if not self.alive:
                return False, "The rover has no power to continue.", {"steps_moved": traversed, "trail": trail}

        return drift_result(True, f"Moved {traversed} step(s) {direction}.", {"steps_moved": traversed})

    def _begin_nav_hold(self, start: tuple[int, int], planned: tuple[int, int]) -> None:
        self.uplink_due_sol = self.sol + UPLINK_DELAY_SOLS
        self._uplink_destination = planned
        self._emit("NAV_DRIFT", "major", {
            "start": list(start),
            "planned": list(planned),
            "actual": [self.x, self.y],
            "offset": [self.x - planned[0], self.y - planned[1]],
            "tau": round(self._tau, 2),
            "uplink_sol": self.uplink_due_sol,
        })

    def _deliver_uplink(self) -> None:
        """Earth localizes the rover from orbit and sends a hazard-free route."""
        destination = self._uplink_destination or (self.x, self.y)
        route = self._ground_route((self.x, self.y), destination)
        if route is None:
            destination, route = (self.x, self.y), []
        directions = [
            next(name for name, delta in DIRECTIONS.items() if delta == (b[0] - a[0], b[1] - a[1]))
            for a, b in zip([(self.x, self.y)] + route, route)
        ]
        self.uplink_due_sol = None
        self._uplink_destination = None
        self._emit("GROUND_UPLINK", "info", {
            "fix": [self.x, self.y],
            "destination": list(destination),
            "route": [list(point) for point in route],
            "directions": directions,
        })
        self._last_signals.append(
            f"UPLINK from Earth: position fix ({self.x},{self.y}). Safe route to "
            f"({destination[0]},{destination[1]}): {','.join(directions) or 'hold position'}"
        )

    def _ground_route(self, origin: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]] | None:
        """Shortest 8-way path planned on orbital imagery, avoiding known hazard terrain."""
        import heapq

        if origin == goal:
            return []
        frontier: list[tuple[float, int, tuple[int, int]]] = [(0.0, 0, origin)]
        came_from: dict[tuple[int, int], tuple[int, int] | None] = {origin: None}
        cost: dict[tuple[int, int], float] = {origin: 0.0}
        counter = 0
        while frontier:
            _, _, current = heapq.heappop(frontier)
            if current == goal:
                break
            for dx, dy in DIRECTIONS.values():
                nxt = (current[0] + dx, current[1] + dy)
                if not self._inside(*nxt):
                    continue
                if nxt != goal and self.terrain[nxt[1]][nxt[0]] in ROUTE_AVOID:
                    continue
                new_cost = cost[current] + (1.0 if dx == 0 or dy == 0 else 1.05)
                if new_cost < cost.get(nxt, math.inf):
                    cost[nxt] = new_cost
                    came_from[nxt] = current
                    counter += 1
                    heuristic = max(abs(goal[0] - nxt[0]), abs(goal[1] - nxt[1]))
                    heapq.heappush(frontier, (new_cost + heuristic, counter, nxt))
        if goal not in came_from:
            return None
        route: list[tuple[int, int]] = []
        node: tuple[int, int] | None = goal
        while node is not None and node != origin:
            route.append(node)
            node = came_from[node]
        return list(reversed(route))

    def _probe(self, args: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        direction = str(args.get("direction", "E")).upper()
        if direction not in DIRECTIONS:
            return False, f"Invalid direction: {direction}", {}
        if not self._pay(1.0):
            return False, "Battery depleted before the probe completed.", {}
        dx, dy = DIRECTIONS[direction]
        x, y = self.x + dx, self.y + dy
        if not self._inside(x, y):
            return False, "No terrain in that direction; the rover is at the map edge.", {}
        terrain = self.terrain[y][x]
        if terrain in {"sand", "ice"}:
            true_slip = self.slip.get((x, y), 0.42)
            measured = min(0.9, max(0.2, true_slip + self._runtime_rng.uniform(-0.05, 0.05)))
        else:
            measured = 0.0
        data = {"x": x, "y": y, "terrain": terrain, "slip": round(measured, 2)}
        return True, f"Probe: {terrain} ahead; measured slip {measured:.2f}.", data

    def _scan(self) -> tuple[bool, str, dict[str, Any]]:
        if not self._pay(3.0):
            return False, "Battery depleted before the scan completed.", {}
        actual: list[tuple[int, int, float]] = []
        for (x, y), value in self.science.items():
            distance = math.hypot(x - self.x, y - self.y)
            if distance <= 5:
                strength = max(0.12, min(1.0, 1.0 - distance / 7.0 + self._runtime_rng.uniform(-0.08, 0.08)))
                actual.append((x, y, strength))

        signals = [self._signal_text(x, y, strength) for x, y, strength in actual]
        # A binomial 0.43 false/true ratio produces about 30% false signals
        # among all returned signals over many scans. Empty scans can still
        # produce an occasional convincing ghost.
        false_count = sum(1 for _ in actual if self._runtime_rng.random() < 0.43)
        if not actual and self._runtime_rng.random() < 0.3:
            false_count = 1
        candidates = [
            (x, y)
            for y in range(max(0, self.y - 5), min(self.size, self.y + 6))
            for x in range(max(0, self.x - 5), min(self.size, self.x + 6))
            if math.hypot(x - self.x, y - self.y) <= 5
            and (x, y) not in self.science
        ]
        self._runtime_rng.shuffle(candidates)
        for x, y in candidates[:false_count]:
            signals.append(self._signal_text(x, y, self._runtime_rng.uniform(0.28, 0.9)))

        self._last_signals = signals
        data = {"signals": list(signals), "count": len(signals)}
        return True, f"Scan returned {len(signals)} anomaly signal(s).", data

    @staticmethod
    def _signal_text(x: int, y: int, strength: float) -> str:
        # Real and false signals intentionally share one format. Correctness
        # can only be learned by confirmation, not by parsing a label.
        return f"anomaly at ({x},{y}) strength {strength:.2f}"

    def _drill(self) -> tuple[bool, str, dict[str, Any]]:
        if not self._pay(6.0):
            return False, "Battery depleted before the drill completed.", {}
        value = self.science.pop((self.x, self.y), None)
        if value is None:
            return False, "No science deposit at this tile.", {"discovered": False}
        self.science_collected += value
        self._emit("DISCOVERY", "info", {"value": value, "terrain": self.terrain[self.y][self.x]})
        return True, f"Collected {value:.0f} science points.", {"discovered": True, "value": value}

    # ------------------------------------------------------------------
    # Sol transitions, weather, energy, and event bookkeeping
    # ------------------------------------------------------------------
    def _tau_for_sol(self, sol: int) -> float:
        tau = 0.5
        for storm in self.storms:
            peak_sol = int(storm["start_sol"])
            peak_tau = float(storm["peak_tau"])
            length = max(1, int(storm["length"]))
            if peak_sol - 3 <= sol <= peak_sol:
                fraction = (sol - (peak_sol - 3)) / 3.0
                tau = max(tau, 0.5 + (peak_tau - 0.5) * fraction)
            elif peak_sol < sol < peak_sol + length:
                fraction = 1.0 - (sol - peak_sol) / length
                tau = max(tau, 0.5 + (peak_tau - 0.5) * fraction)
        return tau

    def _maybe_emit_storm_transition(self) -> None:
        if self._previous_tau <= 2.0 < self._tau:
            self._emit("STORM_ONSET", "info", {"tau": round(self._tau, 2), "tau_trend": round(self._tau - self._previous_tau, 2)})
        elif self._previous_tau > 2.0 >= self._tau:
            self._emit("STORM_END", "info", {"tau": round(self._tau, 2)})

    def _finish_sol(self, sheltered: bool) -> None:
        if self.planet == "icy":
            solar_gain = 21.0 * math.exp(-self._tau / 1.5) * (1.0 - self.panel_dust)
            heater = 9.0 if sheltered else 20.0
        else:
            solar_gain = 30.0 * math.exp(-self._tau / 1.5) * (1.0 - self.panel_dust)
            heater = 4.0 if sheltered else 12.0
        self.battery = min(100.0, max(0.0, self.battery + solar_gain - heater))
        if self._tau > 2.0:
            self.panel_dust = min(0.95, self.panel_dust + 0.012)
        self.min_battery = min(self.min_battery, self.battery)

        self._check_battery_events()
        if self.battery <= 0.0:
            self._kill("battery depleted")

    def _check_battery_events(self) -> None:
        if not self._low_battery_announced and self.battery < 25.0:
            self._low_battery_announced = True
            self._emit("BATTERY_LOW", "minor", {"battery": round(self.battery, 2)})
        if not self._critical_battery_announced and self.battery < 10.0:
            self._critical_battery_announced = True
            self._emit("BATTERY_CRITICAL", "major", {"battery": round(self.battery, 2)})

    def _pay(self, cost: float) -> bool:
        if cost <= 0:
            return self.alive
        if self.battery + 1e-9 < cost:
            self.battery = 0.0
            self.min_battery = 0.0
            self._check_battery_events()
            self._kill("battery depleted during an action")
            return False
        self.battery = max(0.0, self.battery - cost)
        self.min_battery = min(self.min_battery, self.battery)
        self._check_battery_events()
        return True

    def _emit(self, event_type: str, severity: str, details: dict[str, Any]) -> None:
        self._events.append(
            SimEvent(
                type=event_type,
                severity=severity,
                sol=self.sol,
                pos=(self.x, self.y),
                details=details,
            )
        )
        if severity in {"major", "critical"}:
            self.incidents += 1

    def _kill(self, reason: str) -> None:
        if self.alive:
            self.alive = False
            self._emit("DEATH", "critical", {"reason": reason, "battery": round(self.battery, 2)})
