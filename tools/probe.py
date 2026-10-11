"""Count calls (and remember the first two arguments) of game functions with tiny EE detours; read-only probes.

   python probe.py install [NAME ...]    python probe.py show    python probe.py remove

Without names the default set is used (encounter start / cutscene functions). At most 8 probes (stubs at
0x000F6800 + 0x80*i, up to the barrier detour at 0x000F6C00); counters at mailbox +0xA00 + 0x10*i
(count, a0, a1). Functions starting with a jump cannot be probed; one starting with beq/bne can.
"""
import os
import struct
import sys

from hook import MAILBOX
from mips import Asm
from pine import Pine

BASE = 0x000F6800
COUNTERS = 0xA00                # (+0x800..+0x9FF: guard.DEFINITION_OFFSET words)
MAX_PROBES = 8
DEFAULT = ["bossStatStartDemo", "demoManStart", "gameModeSet", "gameModeCls"]


def symbols():
    names = {}
    for line in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "work", "symbols.tsv")):
        a, _s, _k, n = line.rstrip("\n").split("\t")
        names.setdefault(n, int(a, 16))
    return names


def configure(names):
    """PROBES = {name: (function address, counter offset)} for the given names (or 0x addresses)."""
    syms = symbols()
    assert len(names) <= MAX_PROBES
    return {n: (syms[n] if n in syms else int(n, 16), COUNTERS + 0x10 * i) for i, n in enumerate(names)}


PROBES = configure(DEFAULT)


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
    first, second = p.read32(fn), p.read32(fn + 4)
    op = first >> 26
    if op in (4, 5):
        # the function starts with beq/bne: re-issue it with its own delay slot, then leave by the matching
        # path (gameModeCls starts with "beq a0,zero"; copying it verbatim hung the game)
        target = fn + 4 + (((first & 0xFFFF) ^ 0x8000) - 0x8000) * 4
        a.emit((first & 0xFFFF0000) | 3)          # same condition, skip to the taken path below
        a.emit(second)                            # delay slot (runs on both paths, as in the original)
        a.j(fn + 8)
        a.nop()
        a.j(target)
        a.nop()
    elif op in (1, 2, 3, 6, 7, 0x14, 0x15, 0x16, 0x17) or (op == 0 and (first & 0x3F) in (8, 9)):
        raise ValueError("probe: cannot displace a jump/branch at %#x" % fn)
    else:
        a.emit(first)
        a.j(fn + 8)
        a.emit(second)
    return base, fn, a.assemble()


def install(p):
    for off in range(COUNTERS, COUNTERS + 0x10 * MAX_PROBES, 4):
        p.write32(MAILBOX + off, 0)
    for i, name in enumerate(PROBES):
        fn = PROBES[name][0]
        if p.read32(fn) >> 26 == 2:
            continue                              # already patched
        base, fn, code = build(p, name, i)
        assert len(code) <= 0x70
        # the original first two words are kept at stub + 0x70 for remove()
        p.write_block(base, code + b"\0" * (0x70 - len(code)) + struct.pack("<II", p.read32(fn), p.read32(fn + 4)))
        p.write_block(fn, struct.pack("<II", j_to(base), 0))


def remove(p):
    for i, (name, (fn, _off)) in enumerate(PROBES.items()):
        base = BASE + i * 0x80
        if p.read32(fn) == j_to(base):
            p.write_block(fn, p.read_block(base + 0x70, 8))


def show(p):
    for name, (fn, off) in PROBES.items():
        c, a0, a1 = p.read32_many([MAILBOX + off, MAILBOX + off + 4, MAILBOX + off + 8])
        print("%-20s calls %-5d last a0 %#x a1 %#x" % (name, c, a0, a1))


if __name__ == "__main__":
    pine = Pine()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "install":
        if len(sys.argv) > 2:
            PROBES.clear()
            PROBES.update(configure(sys.argv[2:]))
        install(pine)
    elif cmd == "remove":
        remove(pine)
    show(pine)
