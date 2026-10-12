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

SCR_RUN = 0x013CF9E8
ARG_REGS = ("a0", "a1", "a2", "a3", "t0", "t1", "t2", "t3")


def j_to(t):
    return 0x08000000 | ((t >> 2) & 0x3FFFFFF)


class Barrier:
    """One wait-for-the-peer detour. `mb` is the offset of its 5 mailbox words (enable, pending, go, count,
    timeout), `detour` the address of its code (0x200 bytes, last 8 hold the displaced prologue)."""

    def __init__(self, function, detour, mb):
        self.function, self.detour = function, detour
        self.MB_EN, self.MB_PENDING, self.MB_GO, self.MB_COUNT, self.MB_TIMEOUT = (mb + 4 * i for i in range(5))

    def build(self, displaced):
        a = Asm(self.detour)
        a.addiu("sp", "sp", -0x60)
        a.sd("ra", 0x00, "sp")
        a.sd("s0", 0x08, "sp")
        a.sd("s1", 0x10, "sp")
        for i, reg in enumerate(ARG_REGS):      # all register arguments: the wrapped function may take 8
            a.sd(reg, 0x18 + 8 * i, "sp")
        a.li("s0", MAILBOX)
        a.lw("t0", self.MB_EN, "s0")
        a.beq("t0", "zero", "done")
        a.nop()
        a.addiu("t0", "zero", 1)
        a.sw("t0", self.MB_PENDING, "s0")
        a.sw("zero", self.MB_GO, "s0")
        a.lw("s1", 0, "s0")                       # logic frame counter when the wait began
        a.label("loop")
        a.lw("t0", self.MB_GO, "s0")
        a.bne("t0", "zero", "release")
        a.nop()
        a.lw("t1", 0, "s0")
        a.subu("t1", "t1", "s1")
        a.lw("t2", self.MB_TIMEOUT, "s0")
        a.sltu("t3", "t2", "t1")
        a.bne("t3", "zero", "release")
        a.nop()
        a.jal(SCR_RUN)
        a.nop()
        a.beq("zero", "zero", "loop")
        a.nop()
        a.label("release")
        a.sw("zero", self.MB_PENDING, "s0")
        a.sw("zero", self.MB_GO, "s0")
        a.lw("t0", self.MB_COUNT, "s0")
        a.addiu("t0", "t0", 1)
        a.sw("t0", self.MB_COUNT, "s0")
        a.label("done")
        a.ld("ra", 0x00, "sp")
        a.ld("s0", 0x08, "sp")
        a.ld("s1", 0x10, "sp")
        for i, reg in enumerate(ARG_REGS):
            a.ld(reg, 0x18 + 8 * i, "sp")
        a.addiu("sp", "sp", 0x60)
        a.emit(displaced[0])
        a.j(self.function + 8)
        a.emit(displaced[1])
        return a.assemble()

    def installed(self, p):
        return p.read32(self.function) == j_to(self.detour)

    def install(self, p, timeout_frames=450, enabled=True):
        """Detour the function. The displaced prologue is taken from the live function (or from the stub)."""
        if self.installed(p):
            displaced = struct.unpack_from("<II", p.read_block(self.detour + 0x1F0, 8))
        else:
            displaced = (p.read32(self.function), p.read32(self.function + 4))
        code = self.build(displaced)
        assert len(code) < 0x1F0
        p.write_block(self.detour, code + b"\x00" * (0x1F0 - len(code)) + struct.pack("<II", *displaced))
        p.write32(MAILBOX + self.MB_TIMEOUT, timeout_frames)
        p.write32(MAILBOX + self.MB_EN, 1 if enabled else 0)
        p.write32(MAILBOX + self.MB_PENDING, 0)
        p.write32(MAILBOX + self.MB_GO, 0)
        p.write32(MAILBOX + self.MB_COUNT, 0)
        if not self.installed(p):
            p.write_block(self.function, struct.pack("<II", j_to(self.detour), 0))

    def uninstall(self, p):
        p.write32(MAILBOX + self.MB_EN, 0)
        p.write32(MAILBOX + self.MB_GO, 1)
        if self.installed(p):
            displaced = struct.unpack_from("<II", p.read_block(self.detour + 0x1F0, 8))
            p.write_block(self.function, struct.pack("<II", *displaced))

    def pending(self, p):
        return bool(p.read32(MAILBOX + self.MB_PENDING))

    def release(self, p):
        p.write32(MAILBOX + self.MB_GO, 1)

    def set_enabled(self, p, enabled):
        p.write32(MAILBOX + self.MB_EN, 1 if enabled else 0)


# The encounter start (bossStatStartDemo) and every event cutscene (createScrEventDemoManager, 11 arguments: the
# boss intro, the colossus death and the other cutscenes started by event scripts call it once each; the demoManStart
# family is only used by the title / opening scenes).
# Mailbox words: 0x700.. and 0x720.. (see hook.py map); code at 0x000F6C00 and 0x000F6E00.
BOSS = Barrier(0x01349AD8, 0x000F6C00, 0x700)
DEMO = Barrier(0x0145AF70, 0x000F6E00, 0x720)

# Module-level API of the encounter barrier, kept for the callers that predate the class.
FUNCTION, DETOUR = BOSS.function, BOSS.detour
MB_EN, MB_PENDING, MB_GO, MB_COUNT, MB_TIMEOUT = BOSS.MB_EN, BOSS.MB_PENDING, BOSS.MB_GO, BOSS.MB_COUNT, BOSS.MB_TIMEOUT
installed, install, uninstall, pending, release = BOSS.installed, BOSS.install, BOSS.uninstall, BOSS.pending, BOSS.release
