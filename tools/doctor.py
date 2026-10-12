"""Read-only preflight checks for a Shadow of the Colossus co-op session.

Usage:
  python tools/doctor.py          inspect the selected PCSX2/PINE instance
  python tools/doctor.py --ready  also require a running hook and playable world

The command never installs hooks, writes memory, grows the heap, or starts a
bridge.  It is safe to run before ``netbridge.py`` and deliberately uses only
one PINE connection.
"""
import argparse
import time

import guard
import heapgrow
import hook
from netbridge import world_settled
from pine import Pine, PineError
from spawn import CLONE_STRIDE, MAX_CLONES, MB_CLONES

EXPECTED_GAME_ID = "SCUS-97472"


def mailbox_ranges():
    """Named, half-open ranges reserved in the hook mailbox.

    Keeping this map executable makes an accidental overlap visible before a
    live run.  Code ranges in free EE RAM are intentionally not listed here;
    this verifies only the shared data mailbox at ``hook.MAILBOX``.
    """
    return (
        ("hook commands", 0x000, 0x070),
        ("Wander clone pointers", 0x070, 0x0F0),
        ("pad override and frame callback", 0x100, 0x11C),
        ("horse clone pointers", 0x120, 0x160),
        ("guard/spawn diagnostics", 0x180, 0x194),
        ("colossus-sync diagnostics", 0x1A0, 0x1AC),
        ("puppet states", 0x200, 0x400),
        ("guard original delete handlers (Wander)", 0x470, 0x4F0),
        ("guard original delete handlers (horse)", 0x520, 0x560),
        ("warp scratch", 0x600, 0x610),
        ("encounter barrier", 0x700, 0x714),
        ("event-cutscene barrier", 0x720, 0x734),
        ("guard layout definitions (Wander)", 0x870, 0x8F0),
        ("guard layout definitions (horse)", 0x920, 0x960),
        ("shared scratch", 0x9F0, 0x9F4),
        ("function-probe counters", 0xA00, 0xA80),
        ("colossus-sync state", 0xB00, 0xB40),
    )


def mailbox_overlaps(ranges=None):
    """Return overlaps in a mailbox map as ``(left_name, right_name)`` pairs."""
    ranges = sorted(ranges or mailbox_ranges(), key=lambda item: item[1])
    return [(left[0], right[0]) for left, right in zip(ranges, ranges[1:]) if left[2] > right[1]]


def hook_states(p):
    """Return ``{site_name: original|installed|unexpected}`` without changing code."""
    states = {}
    for name, (site, original, _stub, _counter, _commands) in hook.SITES.items():
        word = p.read32(site)
        if word == hook.jal(original):
            states[name] = "original"
        elif hook.is_our_jal(word):
            states[name] = "installed"
        else:
            states[name] = "unexpected 0x%08x" % word
    return states


class Report:
    def __init__(self):
        self.failed = False

    def item(self, label, state, detail="", required=False):
        if state == "ok":
            prefix = "OK"
        elif state == "warn":
            prefix = "WARN"
        else:
            prefix = "FAIL"
            if required:
                self.failed = True
        print("%-4s %-22s %s" % (prefix, label, detail))


def run(ready=False, wait=0.5):
    """Run the preflight and return a shell-compatible status code."""
    report = Report()
    overlaps = mailbox_overlaps()
    report.item("mailbox layout", "ok" if not overlaps else "fail",
                "no overlaps" if not overlaps else repr(overlaps), required=True)
    try:
        p = Pine()
    except (OSError, PineError) as exc:
        report.item("PINE connection", "fail", str(exc), required=True)
        return 2

    try:
        version, game_id, title, status = p.version(), p.game_id(), p.title(), p.status()
        report.item("PINE connection", "ok", "%s | %s" % (version, status), required=True)
        report.item("game build", "ok" if game_id == EXPECTED_GAME_ID else "fail",
                    "%s | %s" % (game_id or "no game", title or "no title"), required=True)

        states = hook_states(p)
        installed = all(state == "installed" for state in states.values())
        detail = ", ".join("%s=%s" % pair for pair in states.items())
        report.item("frame hooks", "ok" if installed else "warn", detail, required=ready)
        if installed:
            before = hook.counters(p)[0]
            time.sleep(wait)
            after = hook.counters(p)[0]
            advancing = after != before
            report.item("logic heartbeat", "ok" if advancing else "fail",
                        "%d -> %d in %.1fs" % (before, after, wait), required=ready)

        playable = world_settled(p)
        report.item("world state", "ok" if playable else "warn",
                    "playable" if playable else "loading, paused, or in a menu", required=ready)

        free_words = p.read32(heapgrow.FREE_WORDS)
        donated = ", ".join("%08x:%s" % (start, "yes" if heapgrow.donated(p, start) else "no")
                            for start, _end in heapgrow.REGIONS)
        report.item("game heap", "ok" if free_words * 4 >= 0x40000 else "warn",
                    "%d KB free; donated %s" % (free_words * 4 // 1024, donated))

        clone_words = [hook.MAILBOX + MB_CLONES + CLONE_STRIDE * slot for slot in range(MAX_CLONES)]
        clones = [p.read32(word) for word in clone_words]
        live = ["%d:0x%08x" % (slot, obj) for slot, obj in enumerate(clones) if obj]
        report.item("clone slots", "ok", ", ".join(live) if live else "none")

        if ready and status != "running":
            report.item("emulator state", "fail", "PCSX2 is %s" % status, required=True)
        elif status != "running":
            report.item("emulator state", "warn", "PCSX2 is %s" % status)
    except (OSError, PineError) as exc:
        report.item("PINE request", "fail", str(exc), required=True)
    finally:
        p.close()

    return 1 if report.failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready", action="store_true", help="require installed hooks and a playable world")
    parser.add_argument("--wait", type=float, default=0.5, help="seconds used to observe the logic hook")
    args = parser.parse_args()
    if args.wait <= 0:
        parser.error("--wait must be positive")
    raise SystemExit(run(args.ready, args.wait))


if __name__ == "__main__":
    main()
