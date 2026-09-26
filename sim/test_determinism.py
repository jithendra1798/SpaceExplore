"""Contract checks for deterministic simulation and tuned reference seeds."""

from __future__ import annotations

import unittest

from sim.policies import run_policy
from sim.seeds import HELD_OUT_SEEDS
from sim.world import World


class DeterminismTests(unittest.TestCase):
    def test_same_seed_and_actions_have_identical_metrics(self) -> None:
        plans = [
            [{"tool": "scan", "args": {}}],
            [{"tool": "move", "args": {"direction": "E", "steps": 3}}],
            [{"tool": "probe_terrain", "args": {"direction": "E"}}, {"tool": "wait", "args": {}}],
            [{"tool": "move", "args": {"direction": "SE", "steps": 2}}],
        ]
        worlds = [World(seed=42), World(seed=42)]
        for plan in plans:
            results = [world.step(plan) for world in worlds]
            self.assertEqual(results[0], results[1])
        self.assertEqual(worlds[0].metrics(), worlds[1].metrics())

    def test_showcase_route_and_storm_are_present(self) -> None:
        world = World(seed=42)
        snapshot = world.snapshot()
        sx, sy = snapshot["start"]
        target = next(item for item in snapshot["science"] if item["pos"] == [sx + 8, sy])
        self.assertEqual(target["value"], 18.0)
        self.assertTrue(8 <= len(snapshot["science"]) <= 12)
        self.assertEqual(snapshot["terrain"][sy][sx + 4], "sand")
        self.assertEqual(snapshot["storms"][0]["start_sol"], 12)

    def test_observation_keeps_ground_truth_hidden(self) -> None:
        world = World(seed=42)
        observation = world.observe()
        self.assertFalse(hasattr(observation, "slip"))
        self.assertFalse(hasattr(observation, "science"))
        self.assertTrue(all(not hasattr(tile, "slip") for tile in observation.local_tiles))

    def test_tuned_eval_policy_gap(self) -> None:
        greedy_stuck = 0
        for seed in HELD_OUT_SEEDS:
            self.assertTrue(run_policy(seed, "careful", max_sols=30).metrics.alive)
            if run_policy(seed, "greedy", max_sols=30).metrics.stuck_sols > 0:
                greedy_stuck += 1
        self.assertGreaterEqual(greedy_stuck, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
