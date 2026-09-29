"""Core state machine for the volcano evacuation command-line game.

Design rules:
- Standard library only.
- Logical clock (integer ticks), fixed-seed RNG, JSON save files.
- Every mutating action is idempotent: repeating an action with the same
  parameters never changes the state a second time.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

ALERT_LEVELS = ("green", "yellow", "orange", "red")
FUEL_COST_PER_TRIP = 10
FUEL_CAPACITY = 30
TICKS_PER_ROUND = 5
DEFAULT_SEED = 73


class GameError(Exception):
    """Raised for malformed data or unreadable save files."""


@dataclass
class Resident:
    rid: str
    name: str
    family: str
    zone: str
    health: int = 100
    sheltered: bool = False

    def to_dict(self):
        return {
            "rid": self.rid,
            "name": self.name,
            "family": self.family,
            "zone": self.zone,
            "health": self.health,
            "sheltered": self.sheltered,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            rid=str(data["rid"]),
            name=str(data["name"]),
            family=str(data["family"]),
            zone=str(data["zone"]),
            health=int(data["health"]),
            sheltered=bool(data["sheltered"]),
        )


@dataclass
class Bus:
    bid: str
    capacity: int
    fuel: int = FUEL_CAPACITY
    status: str = "idle"  # "idle" | "en_route"
    zone: str = ""
    passengers: list = field(default_factory=list)

    def to_dict(self):
        return {
            "bid": self.bid,
            "capacity": self.capacity,
            "fuel": self.fuel,
            "status": self.status,
            "zone": self.zone,
            "passengers": list(self.passengers),
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            bid=str(data["bid"]),
            capacity=int(data["capacity"]),
            fuel=int(data["fuel"]),
            status=str(data["status"]),
            zone=str(data["zone"]),
            passengers=[str(p) for p in data["passengers"]],
        )


def _rng_state_to_json(state):
    version, internal, gauss = state
    return [version, list(internal), gauss]


def _rng_state_from_json(data):
    version, internal, gauss = data
    return (int(version), tuple(int(x) for x in internal), gauss)


class Game:
    """Full evacuation game state plus idempotent actions."""

    def __init__(self, seed=DEFAULT_SEED):
        self.seed = int(seed)
        self.rng = random.Random(self.seed)
        self.clock = 0
        self.round = 0
        self.alert = "green"
        self.zones = {}       # name -> list of resident ids currently present
        self.residents = {}   # rid -> Resident
        self.buses = {}       # bid -> Bus
        self.roads = {}       # zone name -> bool (open)
        self.confirmed = []   # ordered confirmed resident ids
        self.shelter = []     # resident ids that reached the shelter
        self.applied_events = set()
        self._roster = {}     # zone -> original resident order (for restores)

    @classmethod
    def default(cls, seed=DEFAULT_SEED):
        game = cls(seed)
        for rid, name, family, zone in (
            ("r1", "Ann", "f1", "north"),
            ("r2", "Bob", "f1", "north"),
            ("r3", "Cy", "f2", "north"),
            ("r4", "Di", "f3", "south"),
            ("r5", "Eli", "f3", "south"),
            ("r6", "Fay", "f4", "south"),
        ):
            game.add_resident(rid, name, family, zone)
        game.add_bus("b1", capacity=2)
        game.add_bus("b2", capacity=3)
        game.roads = {"north": True, "south": True}
        return game

    # ------------------------------------------------------------------ setup

    def add_resident(self, rid, name, family, zone):
        if rid in self.residents:
            return False, "resident %s already exists" % rid
        self.residents[rid] = Resident(rid, name, family, zone)
        self.zones.setdefault(zone, []).append(rid)
        self._roster.setdefault(zone, []).append(rid)
        self.roads.setdefault(zone, True)
        return True, "resident %s registered in %s" % (rid, zone)

    def add_bus(self, bid, capacity):
        if bid in self.buses:
            return False, "bus %s already exists" % bid
        if capacity < 0:
            return False, "capacity must be >= 0"
        self.buses[bid] = Bus(bid, capacity)
        return True, "bus %s registered (capacity %d)" % (bid, capacity)

    # ---------------------------------------------------------------- actions

    def confirm(self, rid):
        """Add a resident to the confirmed list (idempotent)."""
        if rid not in self.residents:
            return False, "unknown resident %s" % rid
        if rid in self.confirmed:
            return True, "resident %s already confirmed" % rid
        self.confirmed.append(rid)
        return True, "resident %s confirmed" % rid

    def set_alert(self, level):
        """Change alert level. Downgrades never clear the confirmed list."""
        if level not in ALERT_LEVELS:
            return False, "invalid alert level %s" % level
        if level == self.alert:
            return True, "alert already %s" % level
        self.alert = level
        return True, "alert set to %s" % level

    def set_road(self, zone, open_):
        if zone not in self.roads:
            return False, "unknown zone %s" % zone
        self.roads[zone] = bool(open_)
        return True, "road %s %s" % (zone, "open" if open_ else "closed")

    def dispatch(self, bid, zone):
        """Load a bus in `zone` and drive it to the shelter.

        Families board as a unit and are never split; the bus carries at
        most `capacity` residents (no off-by-one). Requires an open road
        and enough fuel. Repeating the call while en route is a no-op.
        """
        bus = self.buses.get(bid)
        if bus is None:
            return False, "unknown bus %s" % bid
        if zone not in self.zones:
            return False, "unknown zone %s" % zone
        if bus.status != "idle":
            return False, "bus %s already dispatched" % bid
        if not self.roads.get(zone, False):
            return False, "road to %s is closed" % zone
        if bus.fuel < FUEL_COST_PER_TRIP:
            return False, "bus %s has insufficient fuel" % bid

        families = {}
        for rid in self.zones[zone]:
            families.setdefault(self.residents[rid].family, []).append(rid)

        chosen = []
        for members in families.values():
            if len(chosen) + len(members) <= bus.capacity:
                chosen.extend(members)
        if not chosen:
            return False, "no evacuees fit on bus %s" % bid

        for rid in chosen:
            self.zones[zone].remove(rid)
            self.residents[rid].sheltered = True
            self.shelter.append(rid)
        bus.passengers = list(chosen)
        bus.zone = zone
        bus.status = "en_route"
        bus.fuel -= FUEL_COST_PER_TRIP
        return True, "bus %s evacuated %d resident(s) from %s" % (
            bid, len(chosen), zone)

    def cancel(self, bid):
        """Cancel an en-route trip: passengers return, fuel is refunded.

        Idempotent: cancelling an idle bus changes nothing and never
        refunds fuel twice.
        """
        bus = self.buses.get(bid)
        if bus is None:
            return False, "unknown bus %s" % bid
        if bus.status != "en_route":
            return False, "bus %s has no active trip" % bid
        zone = bus.zone
        for rid in bus.passengers:
            self.residents[rid].sheltered = False
            self.shelter.remove(rid)
            self.zones[zone].append(rid)
        order = self._roster[zone]
        self.zones[zone].sort(key=order.index)
        bus.fuel = min(FUEL_CAPACITY, bus.fuel + FUEL_COST_PER_TRIP)
        bus.status = "idle"
        bus.passengers = []
        bus.zone = ""
        return True, "bus %s trip cancelled, fuel refunded" % bid

    def apply_ash(self, event_id, zone, damage):
        """Apply an ash-fall event once per `event_id` (idempotent)."""
        if event_id in self.applied_events:
            return False, "event %s already applied" % event_id
        if zone not in self.zones:
            return False, "unknown zone %s" % zone
        damage = int(damage)
        if damage < 0:
            return False, "damage must be >= 0"
        self.applied_events.add(event_id)
        for rid in self.zones[zone]:
            resident = self.residents[rid]
            resident.health = max(0, resident.health - damage)
        return True, "ash event %s hit %s for %d" % (event_id, zone, damage)

    def advance_round(self):
        """Advance exactly one evacuation round on the logical clock."""
        self.round += 1
        self.clock += TICKS_PER_ROUND
        return True, "round %d (clock %d)" % (self.round, self.clock)

    # ------------------------------------------------------------ persistence

    def to_dict(self):
        return {
            "seed": self.seed,
            "rng_state": _rng_state_to_json(self.rng.getstate()),
            "clock": self.clock,
            "round": self.round,
            "alert": self.alert,
            "zones": {name: list(rids) for name, rids in self.zones.items()},
            "residents": {rid: r.to_dict() for rid, r in self.residents.items()},
            "buses": {bid: b.to_dict() for bid, b in self.buses.items()},
            "roads": dict(self.roads),
            "confirmed": list(self.confirmed),
            "shelter": list(self.shelter),
            "applied_events": sorted(self.applied_events),
            "roster": {name: list(rids) for name, rids in self._roster.items()},
        }

    @classmethod
    def from_dict(cls, data):
        if not data:
            raise GameError("empty save data")
        required = ("seed", "clock", "round", "alert", "zones",
                    "residents", "buses", "roads", "confirmed",
                    "shelter", "applied_events", "roster", "rng_state")
        missing = [key for key in required if key not in data]
        if missing:
            raise GameError("save data missing keys: %s" % ", ".join(missing))
        game = cls(int(data["seed"]))
        game.rng.setstate(_rng_state_from_json(data["rng_state"]))
        game.clock = int(data["clock"])
        game.round = int(data["round"])
        game.alert = str(data["alert"])
        game.zones = {str(k): [str(r) for r in v] for k, v in data["zones"].items()}
        game.residents = {str(k): Resident.from_dict(v)
                          for k, v in data["residents"].items()}
        game.buses = {str(k): Bus.from_dict(v) for k, v in data["buses"].items()}
        game.roads = {str(k): bool(v) for k, v in data["roads"].items()}
        game.confirmed = [str(r) for r in data["confirmed"]]
        game.shelter = [str(r) for r in data["shelter"]]
        game.applied_events = set(str(e) for e in data["applied_events"])
        game._roster = {str(k): [str(r) for r in v]
                        for k, v in data["roster"].items()}
        return game

    def save(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, sort_keys=True)
        return True, "saved to %s" % path

    @classmethod
    def load(cls, path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            raise GameError("cannot load %s: %s" % (path, exc))
        return cls.from_dict(data)
