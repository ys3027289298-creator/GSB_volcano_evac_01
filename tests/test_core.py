import json
import os
import tempfile
import unittest

from volcano.core import (
    DEFAULT_SEED,
    FUEL_CAPACITY,
    FUEL_COST_PER_TRIP,
    TICKS_PER_ROUND,
    Game,
    GameError,
)
from volcano.__main__ import execute


class OccupancyTest(unittest.TestCase):
    def test_sheltered_residents_leave_zone(self):
        game = Game.default()
        ok, _ = game.dispatch("b1", "north")
        self.assertTrue(ok)
        for rid in game.shelter:
            self.assertNotIn(rid, game.zones["north"])
            self.assertTrue(game.residents[rid].sheltered)
        self.assertEqual(len(game.shelter), 2)  # family f1 fills bus b1

    def test_dispatch_is_idempotent(self):
        game = Game.default()
        game.dispatch("b1", "north")
        snapshot = game.to_dict()
        ok, _ = game.dispatch("b1", "north")
        self.assertFalse(ok)
        self.assertEqual(snapshot, game.to_dict())


class RoadLogicTest(unittest.TestCase):
    def test_closed_road_blocks_dispatch_logic(self):
        game = Game.default()
        game.set_road("north", False)
        ok, msg = game.dispatch("b1", "north")
        self.assertFalse(ok)
        self.assertIn("closed", msg)
        self.assertEqual(game.shelter, [])
        self.assertEqual(game.zones["north"], ["r1", "r2", "r3"])
        self.assertEqual(game.buses["b1"].fuel, FUEL_CAPACITY)

    def test_reopen_road_allows_dispatch(self):
        game = Game.default()
        game.set_road("north", False)
        game.set_road("north", True)
        ok, _ = game.dispatch("b1", "north")
        self.assertTrue(ok)


class CapacityTest(unittest.TestCase):
    def test_bus_never_carries_capacity_plus_one(self):
        game = Game()
        for i in range(3):
            game.add_resident("p%d" % i, "P%d" % i, "fam%d" % i, "zone")
        game.add_bus("bus", capacity=2)
        ok, _ = game.dispatch("bus", "zone")
        self.assertTrue(ok)
        self.assertEqual(len(game.buses["bus"].passengers), 2)
        self.assertEqual(len(game.shelter), 2)
        self.assertEqual(len(game.zones["zone"]), 1)

    def test_exact_capacity_boundary(self):
        game = Game()
        for i in range(2):
            game.add_resident("p%d" % i, "P%d" % i, "fam%d" % i, "zone")
        game.add_bus("bus", capacity=2)
        ok, _ = game.dispatch("bus", "zone")
        self.assertTrue(ok)
        self.assertEqual(len(game.shelter), 2)
        self.assertEqual(game.zones["zone"], [])

    def test_zero_capacity_bus_moves_nobody(self):
        game = Game()
        game.add_resident("p0", "P0", "fam0", "zone")
        game.add_bus("bus", capacity=0)
        ok, _ = game.dispatch("bus", "zone")
        self.assertFalse(ok)
        self.assertEqual(game.zones["zone"], ["p0"])
        self.assertEqual(game.shelter, [])


class AlertTest(unittest.TestCase):
    def test_alert_downgrade_keeps_confirmed_list(self):
        game = Game.default()
        game.set_alert("red")
        game.confirm("r1")
        game.confirm("r2")
        game.set_alert("green")
        self.assertEqual(game.confirmed, ["r1", "r2"])

    def test_invalid_alert_level_rejected(self):
        game = Game.default()
        ok, _ = game.set_alert("purple")
        self.assertFalse(ok)
        self.assertEqual(game.alert, "green")


class FuelTest(unittest.TestCase):
    def test_cancel_refunds_fuel(self):
        game = Game.default()
        game.dispatch("b1", "north")
        self.assertEqual(game.buses["b1"].fuel, FUEL_CAPACITY - FUEL_COST_PER_TRIP)
        ok, _ = game.cancel("b1")
        self.assertTrue(ok)
        self.assertEqual(game.buses["b1"].fuel, FUEL_CAPACITY)

    def test_cancel_is_idempotent_no_double_refund(self):
        game = Game.default()
        game.dispatch("b1", "north")
        # passengers returned on first cancel only
        game.cancel("b1")
        snapshot = game.to_dict()
        ok, _ = game.cancel("b1")
        self.assertFalse(ok)
        self.assertEqual(game.buses["b1"].fuel, FUEL_CAPACITY)
        self.assertEqual(snapshot, game.to_dict())

    def test_cancel_restores_passengers_to_zone(self):
        game = Game.default()
        game.dispatch("b1", "north")
        game.cancel("b1")
        self.assertEqual(game.zones["north"], ["r1", "r2", "r3"])
        self.assertEqual(game.shelter, [])


class FamilyTest(unittest.TestCase):
    def test_family_never_split(self):
        game = Game()
        for i in range(3):
            game.add_resident("k%d" % i, "K%d" % i, "kin", "zone")
        game.add_bus("bus", capacity=2)
        ok, _ = game.dispatch("bus", "zone")
        self.assertFalse(ok)  # family of 3 does not fit, nobody boards
        locations = {game.residents[r].sheltered for r in ("k0", "k1", "k2")}
        self.assertEqual(locations, {False})

    def test_family_boards_together_once(self):
        game = Game.default()
        game.dispatch("b1", "north")  # family f1 (r1, r2) fills the bus
        self.assertEqual(set(game.shelter), {"r1", "r2"})
        # second bus from same zone takes remaining family f2 whole
        game.dispatch("b2", "north")
        self.assertEqual(set(game.shelter), {"r1", "r2", "r3"})
        self.assertEqual(game.zones["north"], [])


class AshEventTest(unittest.TestCase):
    def test_ash_event_applied_only_once(self):
        game = Game.default()
        ok, _ = game.apply_ash("e1", "north", 20)
        self.assertTrue(ok)
        ok, _ = game.apply_ash("e1", "north", 20)
        self.assertFalse(ok)
        self.assertEqual(game.residents["r1"].health, 80)

    def test_ash_only_hits_residents_still_in_zone(self):
        game = Game.default()
        game.dispatch("b1", "north")  # r1, r2 sheltered
        game.apply_ash("e1", "north", 20)
        self.assertEqual(game.residents["r1"].health, 100)
        self.assertEqual(game.residents["r3"].health, 80)

    def test_health_floors_at_zero(self):
        game = Game.default()
        game.apply_ash("e1", "north", 250)
        self.assertEqual(game.residents["r1"].health, 0)


class PersistenceTest(unittest.TestCase):
    def test_save_load_roundtrip(self):
        game = Game.default()
        game.confirm("r1")
        game.dispatch("b1", "north")
        game.apply_ash("e1", "south", 10)
        game.advance_round()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "save.json")
            game.save(path)
            loaded = Game.load(path)
        self.assertEqual(game.to_dict(), loaded.to_dict())

    def test_round_does_not_double_advance_after_load(self):
        game = Game.default()
        game.advance_round()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "save.json")
            game.save(path)
            loaded = Game.load(path)
        self.assertEqual(loaded.round, 1)
        self.assertEqual(loaded.clock, TICKS_PER_ROUND)
        loaded.advance_round()
        self.assertEqual(loaded.round, 2)
        self.assertEqual(loaded.clock, 2 * TICKS_PER_ROUND)

    def test_load_empty_data_raises(self):
        with self.assertRaises(GameError):
            Game.from_dict({})
        with self.assertRaises(GameError):
            Game.from_dict(None)

    def test_load_missing_keys_raises(self):
        with self.assertRaises(GameError):
            Game.from_dict({"seed": DEFAULT_SEED})

    def test_load_missing_file_raises(self):
        with self.assertRaises(GameError):
            Game.load("/nonexistent/path/save.json")

    def test_load_corrupt_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            with self.assertRaises(GameError):
                Game.load(path)

    def test_fixed_seed_determinism(self):
        self.assertEqual(Game.default().to_dict(), Game.default().to_dict())


class CommandTest(unittest.TestCase):
    def test_illegal_command_does_not_mutate(self):
        game = Game.default()
        snapshot = game.to_dict()
        reply = execute(game, "explode everything")
        self.assertIn("error", reply)
        self.assertEqual(snapshot, game.to_dict())

    def test_empty_command_rejected(self):
        game = Game.default()
        self.assertIn("error", execute(game, "   "))

    def test_bad_arguments_rejected(self):
        game = Game.default()
        snapshot = game.to_dict()
        self.assertIn("error", execute(game, "dispatch b1"))
        self.assertIn("error", execute(game, "confirm"))
        self.assertEqual(snapshot, game.to_dict())

    def test_duplicate_input_is_idempotent(self):
        game = Game.default()
        execute(game, "confirm r1")
        execute(game, "confirm r1")
        self.assertEqual(game.confirmed, ["r1"])
        execute(game, "ash e1 north 10")
        execute(game, "ash e1 north 10")
        self.assertEqual(game.residents["r1"].health, 90)

    def test_unknown_resident_and_bus_rejected(self):
        game = Game.default()
        ok, _ = game.confirm("ghost")
        self.assertFalse(ok)
        ok, _ = game.dispatch("ghost", "north")
        self.assertFalse(ok)
        ok, _ = game.dispatch("b1", "atlantis")
        self.assertFalse(ok)

    def test_load_command_via_cli(self):
        game = Game.default()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "save.json")
            game.advance_round()
            game.save(path)
            fresh = Game.default()
            reply = execute(fresh, "load " + path)
            self.assertIn("loaded", reply)
            self.assertEqual(fresh.round, 1)

    def test_save_file_is_valid_json(self):
        game = Game.default()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "save.json")
            game.save(path)
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        self.assertEqual(data["seed"], DEFAULT_SEED)


if __name__ == "__main__":
    unittest.main()
