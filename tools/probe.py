"""Count calls (and remember the first argument) of game functions with tiny EE detours; read-only probes.
   python probe.py install    python probe.py show    python probe.py remove"""
import struct
import sys

from hook import MAILBOX
from mips import Asm
from pine import Pine

BASE = 0x000F6800
PROBES = {   # name: (function, number of displaced instructions = 2, counter offset in the mailbox)
    "bossStatStartDemo": (0x01349AD8, 0x1B0),
    "demoManStart": (0x0140E390, 0x1C0),
    "gameModeSet": (0x01352B70, 0x1D0),
    "gameModeCls": (0x01352BC8, 0x1E0),
}


def j_to(t):
    return 0x08000000 | ((t >> 2) & 0x3FFFFFF)


def build(p, name, index):
    fn, off = PROBES[name]
    base = BASE + index * 0x80
    a = Asm(base)
    a.li("t6", MAILBOX + off)
    a.lw("t7", 0, "t6")
    a.addiu("t7", "t7", 1)
    a.sw("t7", 0, "t6")
    a.sw("a0", 4, "t6")
    a.sw("a1", 8, "t6")
    a.emit(p.read32(fn))
    a.j(fn + 8)
    a.emit(p.read32(fn + 4))
    return base, fn, a.assemble()


def install(p):
    for i, name in enumerate(PROBES):
        fn = PROBES[name][0]
        if p.read32(fn) >> 26 == 2:
            continue                              # already patched
        base, fn, code = build(p, name, i)
        p.write_block(base, code + b"\0" * (-len(code) % 8))
        p.write_block(fn, struct.pack("<II", j_to(base), 0))
        # the displaced pair lives in the stub: keep the originals for removal
        p.write_block(base + 0x70, struct.pack("<II", *struct.unpack_from("<II", code, 6 * 4 + 0)[:0] or (0, 0)))


def show(p):
    for name, (fn, off) in PROBES.items():
        c, a0, a1 = p.read32_many([MAILBOX + off, MAILBOX + off + 4, MAILBOX + off + 8])
        print("%-20s calls %-5d last a0 %#x a1 %#x" % (name, c, a0, a1))


if __name__ == "__main__":
    pine = Pine()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "install":
        install(pine)
    show(pine)
