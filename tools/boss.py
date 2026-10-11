"""Colossus state: find the boss, read/merge its life, read its pose (for the future boss sync).

  python3 boss.py           print the active colossus' object, pose, motion, per-part life (needs the hook)
  python3 boss.py watch     same, every second

Facts (verified on the dormant first colossus, savestate 2; the structure matches getBossPartsLifeRatio):
  * the boss is the first object of group 0x01465948 (GAMEUTIL_get_boss_obj, 0x013439f0): an ordinary character
    object (class word 0x01206DD8, kind 4) whose script is the generic boss loop (SCRExecBossPreProcess /
    updateScriptDataSystem / SCRExecBossPostProcess); its AI is a bytecode interpreter in updateScriptDataSystem.
  * work = GetSCRCharaAIWorkPtr(boss) (0x013CFB28); base = *work. base+0x54 = number of parts, base+0x10 = the
    part definitions (0x58 bytes each: +0 id, +0x1c maximum life), base+0xE8 = the life block: part i's CURRENT life is
    the float at life + 0x24 + 8*i, the damage taken the float at life + 0x20 + 8*i + 4 (SCRGetBossPartsDamage).
  * life only ever goes down during a fight, so two machines fighting the same colossus can merge with a
    per-part minimum: nothing is lost when both hit the same part, and the order of updates does not matter.
The boss' AI does not run until the game's own trigger (an event script runs BootBoss = bossStatStartDemo when
the player enters the arena); a teleport to the arena does not trigger it, so the animation sync (motion index +
frame + root transform through the puppet script, like the Wander) still needs a savestate taken in a real fight.
"""
import struct
import sys
import time

from hook import call
from pine import Pine

GET_BOSS_OBJ = 0x013439F0
GET_AI_WORK = 0x013CFB28
CLASS = 0x90


class Boss:
    def __init__(self, p):
        self.p = p
        self.obj = call(p, GET_BOSS_OBJ)
        if not self.obj:
            raise LookupError("no boss object (not in a colossus area)")
        work = call(p, GET_AI_WORK, self.obj)
        base = p.read32(work)
        self.count, self.defs, self.life = p.read32_many([base + 0x54, base + 0x10, base + 0xE8])
        if not 0 < self.count <= 64:
            raise LookupError("boss work looks uninitialised (parts=%d)" % self.count)
        raw = p.read_block(self.defs & ~7, 0x58 * self.count + 8)
        shift = self.defs & 7
        self.ids = [struct.unpack_from("<I", raw, shift + 0x58 * i)[0] & 0x3F0 for i in range(self.count)]
        self.max = [struct.unpack_from("<f", raw, shift + 0x58 * i + 0x1C)[0] for i in range(self.count)]

    def life_addr(self, i):
        return self.life + 0x24 + 8 * i

    def read_life(self):
        words = self.p.read32_many([self.life_addr(i) for i in range(self.count)])
        return [struct.unpack("<f", struct.pack("<I", w))[0] for w in words]

    def merge_life(self, remote):
        """Lower every part's life to min(local, remote); returns how many parts changed."""
        changed = 0
        for i, (mine, theirs) in enumerate(zip(self.read_life(), remote)):
            if theirs < mine:
                self.p.writef(self.life_addr(i), theirs)
                changed += 1
        return changed

    def pose(self):
        """(motion pointer, motion table, frame, position xyz) of the boss character."""
        cl = self.obj + CLASS
        motion, table, frame = self.p.read32_many([cl + 0x1C0, cl + 0x2AC, cl + 0x1C8])
        pos = tuple(self.p.readf(self.obj + 0xA0 + 4 * i) for i in range(3))
        return motion, table, struct.unpack("<f", struct.pack("<I", frame))[0], pos


def show(p):
    boss = Boss(p)
    motion, table, frame, pos = boss.pose()
    print("boss 0x%08x pos (%.1f, %.1f, %.1f) motion 0x%08x table 0x%08x frame %.1f" % (boss.obj, *pos, motion, table, frame))
    for i, (life, mx) in enumerate(zip(boss.read_life(), boss.max)):
        print("  part %2d id %#05x life %8.2f / %8.2f" % (i, boss.ids[i], life, mx))


if __name__ == "__main__":
    pine = Pine()
    show(pine)
    while len(sys.argv) > 1 and sys.argv[1] == "watch":
        time.sleep(1.0)
        print()
        show(pine)
