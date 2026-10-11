"""Start a colossus fight (and its cutscene) on both machines together.

The game starts an encounter when an event script runs the script command BootBoss = bossStatStartDemo
(0x01349AD8): it is called once per encounter, then the intro cutscene (gameModeSet(1) ... gameModeCls(1)) and the
fight follow. Measured by probe.py on the first colossus: bossStatStartDemo 1 call, gameModeSet 1, gameModeCls 1.

A detour on bossStatStartDemo makes the local game wait (yielding with scrRun, so the world keeps running; only
that event script waits) until either
  * the bridge sets MB_GO (the peer reached its own BootBoss, or its colossus is already running), or
  * MB_TIMEOUT logic frames have passed (the peer is somewhere else: do not hold a player forever).
While it waits MB_PENDING is 1, which the bridge sends to the peer. Both players arriving at the arena then see
the same cutscene start at (nearly) the same moment; a lone player is held for the timeout once.
"""
import struct

from hook import MAILBOX
from mips import Asm

FUNCTION = 0x01349AD8            # bossStatStartDemo
SCR_RUN = 0x013CF9E8
DETOUR = 0x000F6C00
# mailbox +0x200..+0x3FF holds the puppet state blocks (puppet.PUPPET_STATE): stay clear of them
MB_EN, MB_PENDING, MB_GO, MB_COUNT, MB_TIMEOUT = 0x700, 0x704, 0x708, 0x70C, 0x710


def j_to(t):
    return 0x08000000 | ((t >> 2) & 0x3FFFFFF)


def build(displaced):
    a = Asm(DETOUR)
    a.addiu("sp", "sp", -0x30)
    a.sd("ra", 0x00, "sp")
    a.sd("s0", 0x08, "sp")
    a.sd("s1", 0x10, "sp")
    a.sd("a0", 0x18, "sp")
    a.sd("a1", 0x20, "sp")
    a.li("s0", MAILBOX)
    a.lw("t0", MB_EN, "s0")
    a.beq("t0", "zero", "done")
    a.nop()
    a.addiu("t0", "zero", 1)
    a.sw("t0", MB_PENDING, "s0")
    a.sw("zero", MB_GO, "s0")
    a.lw("s1", 0, "s0")                       # logic frame counter when the wait began
    a.label("loop")
    a.lw("t0", MB_GO, "s0")
    a.bne("t0", "zero", "release")
    a.nop()
    a.lw("t1", 0, "s0")
    a.subu("t1", "t1", "s1")
    a.lw("t2", MB_TIMEOUT, "s0")
    a.sltu("t3", "t2", "t1")
    a.bne("t3", "zero", "release")
    a.nop()
    a.jal(SCR_RUN)
    a.nop()
    a.beq("zero", "zero", "loop")
    a.nop()
    a.label("release")
    a.sw("zero", MB_PENDING, "s0")
    a.sw("zero", MB_GO, "s0")
    a.lw("t0", MB_COUNT, "s0")
    a.addiu("t0", "t0", 1)
    a.sw("t0", MB_COUNT, "s0")
    a.label("done")
    a.ld("ra", 0x00, "sp")
    a.ld("s0", 0x08, "sp")
    a.ld("s1", 0x10, "sp")
    a.ld("a0", 0x18, "sp")
    a.ld("a1", 0x20, "sp")
    a.addiu("sp", "sp", 0x30)
    a.emit(displaced[0])
    a.j(FUNCTION + 8)
    a.emit(displaced[1])
    return a.assemble()


def installed(p):
    return p.read32(FUNCTION) == j_to(DETOUR)


def install(p, timeout_frames=450, enabled=True):
    """Detour bossStatStartDemo. The displaced prologue is taken from the live function (or from the stub)."""
    if installed(p):
        displaced = struct.unpack_from("<II", p.read_block(DETOUR + 0x1F0, 8))
    else:
        displaced = (p.read32(FUNCTION), p.read32(FUNCTION + 4))
    code = build(displaced)
    assert len(code) < 0x1F0
    p.write_block(DETOUR, code + b"\0" * (0x1F0 - len(code)) + struct.pack("<II", *displaced))
    p.write32(MAILBOX + MB_TIMEOUT, timeout_frames)
    p.write32(MAILBOX + MB_EN, 1 if enabled else 0)
    p.write32(MAILBOX + MB_PENDING, 0)
    p.write32(MAILBOX + MB_GO, 0)
    p.write32(MAILBOX + MB_COUNT, 0)
    if not installed(p):
        p.write_block(FUNCTION, struct.pack("<II", j_to(DETOUR), 0))


def uninstall(p):
    p.write32(MAILBOX + MB_EN, 0)
    p.write32(MAILBOX + MB_GO, 1)
    if installed(p):
        displaced = struct.unpack_from("<II", p.read_block(DETOUR + 0x1F0, 8))
        p.write_block(FUNCTION, struct.pack("<II", *displaced))


def pending(p):
    return bool(p.read32(MAILBOX + MB_PENDING))


def release(p):
    p.write32(MAILBOX + MB_GO, 1)
