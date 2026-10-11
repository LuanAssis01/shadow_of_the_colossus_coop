"""Visit every colossus: from a clean savestate, make colossus N active, warp to its arrival point, wait for the
area to stream in, then report whether the colossus object exists and where, and save a screenshot.

  python bosstour.py [STATE_SLOT] [FIRST] [LAST]       (single instance, no bridge; SSTATES=<savestate dir>)
Screenshots: work/tour/boss_NN.png. One line per colossus.
"""
import os
import sys
import time

import heapgrow
import warp
from hook import call, install as install_hook
from pine import Pine
from snap import snap

GET_BOSS_OBJ = 0x013439F0
CLASS = 0x90


def visit(p, slot, n, outdir):
    p.load_state(slot)
    time.sleep(3.0)
    install_hook(p)
    heapgrow.grow(p, quiet=True)
    warp.activate(p, n)
    pos, heading, area, hp, grip = warp.arrival(p, n)
    warp.warp_to_boss(p, n, hold=20.0)
    time.sleep(8.0)
    obj = call(p, GET_BOSS_OBJ)
    kind = p.read32(obj + CLASS + 0x370) & 0xFFFF if obj else 0
    bpos = tuple(p.readf(obj + 0xA0 + 4 * i) for i in range(3)) if obj else (0, 0, 0)
    from netbridge import local_player
    player = local_player(p)
    ppos = tuple(p.readf(player + 0xA0 + 4 * i) for i in range(3))
    dist = ((bpos[0] - ppos[0]) ** 2 + (bpos[2] - ppos[2]) ** 2) ** 0.5 if obj else -1
    snap(p, os.path.join(outdir, "boss_%02d.png" % n))
    print("colossus %2d area %#04x hp %3.0f: boss obj %#010x kind %d at (%.0f, %.0f, %.0f), %.0f m away; player at "
          "(%.0f, %.0f, %.0f) (arrival %.0f, %.0f, %.0f), heap %d" % (
              n, area, hp, obj, kind, *bpos, dist, *ppos, *pos, p.read32(heapgrow.FREE_WORDS)), flush=True)


def main():
    slot = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    first = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    last = int(sys.argv[3]) if len(sys.argv) > 3 else warp.BOSS_COUNT - 1
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "work", "tour")
    os.makedirs(outdir, exist_ok=True)
    p = Pine()
    for n in range(first, last + 1):
        try:
            visit(p, slot, n, outdir)
        except Exception as exc:          # one colossus failing (freeze, timeout) must not end the tour
            print("colossus %2d: FAILED (%s: %s)" % (n, type(exc).__name__, exc), flush=True)


if __name__ == "__main__":
    main()
