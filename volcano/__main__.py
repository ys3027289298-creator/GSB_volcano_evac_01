"""Command-line interface for the volcano evacuation game.

Usage:
    python -m volcano          # interactive REPL (one command per line)
    python -m volcano demo     # fixed deterministic demonstration
"""

from __future__ import annotations

import sys

from .core import ALERT_LEVELS, Game, GameError

USAGE = """commands:
  status                     show full game state
  alert <level>              set alert level (%s)
  confirm <resident>         confirm a resident for evacuation
  dispatch <bus> <zone>      drive a bus from a zone to the shelter
  cancel <bus>               cancel an en-route trip (refunds fuel)
  close <zone>               close the road to a zone
  open <zone>                reopen the road to a zone
  ash <event> <zone> <dmg>   apply an ash-fall event (once per event id)
  round                      advance one evacuation round
  save <path>                save state as JSON
  load <path>                load state from JSON
  help                       show this text
  quit                       exit
""" % "/".join(ALERT_LEVELS)


def render(game):
    lines = [
        "clock=%d round=%d alert=%s" % (game.clock, game.round, game.alert),
        "confirmed=%s" % (",".join(game.confirmed) or "-"),
        "shelter=%s" % (",".join(game.shelter) or "-"),
    ]
    for name, rids in game.zones.items():
        road = "open" if game.roads.get(name) else "closed"
        lines.append("zone %s [%s]: %s" % (name, road, ",".join(rids) or "-"))
    for bid, bus in game.buses.items():
        lines.append("bus %s cap=%d fuel=%d %s" % (
            bid, bus.capacity, bus.fuel, bus.status))
    return "\n".join(lines)


def execute(game, line):
    """Run one command line; always returns a text reply, never raises."""
    parts = line.split()
    if not parts:
        return "error: empty command"
    cmd, args = parts[0], parts[1:]
    try:
        if cmd == "status" and not args:
            return render(game)
        if cmd == "help" and not args:
            return USAGE
        if cmd == "alert" and len(args) == 1:
            return game.set_alert(args[0])[1]
        if cmd == "confirm" and len(args) == 1:
            return game.confirm(args[0])[1]
        if cmd == "dispatch" and len(args) == 2:
            return game.dispatch(args[0], args[1])[1]
        if cmd == "cancel" and len(args) == 1:
            return game.cancel(args[0])[1]
        if cmd == "close" and len(args) == 1:
            return game.set_road(args[0], False)[1]
        if cmd == "open" and len(args) == 1:
            return game.set_road(args[0], True)[1]
        if cmd == "ash" and len(args) == 3:
            return game.apply_ash(args[0], args[1], args[2])[1]
        if cmd == "round" and not args:
            return game.advance_round()[1]
        if cmd == "save" and len(args) == 1:
            return game.save(args[0])[1]
        if cmd == "load" and len(args) == 1:
            loaded = Game.load(args[0])
            game.__dict__.update(loaded.__dict__)
            return "loaded from %s" % args[0]
        if cmd == "quit" and not args:
            raise SystemExit(0)
        if cmd in ("alert", "confirm", "dispatch", "cancel", "close",
                   "open", "ash", "round", "save", "load", "status"):
            return "error: bad arguments for '%s'" % cmd
        return "error: unknown command '%s'" % cmd
    except (GameError, ValueError) as exc:
        return "error: %s" % exc


DEMO_SCRIPT = [
    "alert orange",
    "confirm r1",
    "confirm r2",
    "confirm r3",
    "close south",
    "dispatch b1 north",
    "dispatch b2 south",
    "ash e1 north 15",
    "ash e1 north 15",
    "cancel b1",
    "dispatch b1 north",
    "round",
    "save /tmp/volcano_demo_save.json",
    "load /tmp/volcano_demo_save.json",
    "round",
    "status",
]


def demo():
    game = Game.default()
    print("== volcano evacuation demo (fixed seed %d) ==" % game.seed)
    for line in DEMO_SCRIPT:
        print("> " + line)
        print(execute(game, line))
    print("== demo complete ==")


def main(argv):
    if argv[1:] == ["demo"]:
        demo()
        return 0
    game = Game.default()
    print(USAGE)
    while True:
        try:
            line = input("volcano> ")
        except EOFError:
            break
        try:
            print(execute(game, line))
        except SystemExit:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
