"""Colossus state: find the boss, read/merge its life, read its pose (for the future boss sync).

  python3 boss.py           print the active colossus' object, pose, motion, per-part life (needs the hook)
  python3 boss.py watch     same, every second

Facts (verified on the dormant first colossus, savestate 2; the structure matches getBossPartsLifeRatio):
  * the boss is the first object of group 0x01465948 (GAMEUTIL_get_boss_obj, 0x013439f0): an ordinary character
    object (class word 0x01206DD8, kind 4) whose script is the generic boss loop (SCRExecBossPreProcess /
    updateScriptDataSystem / SCRExecBossPostProcess); its AI is a bytecode interpreter in updateScriptDataSystem.
  * work = GetSCRCharaAIWorkPtr(boss) (0x013CFB28); base = *work. base+0x54 = number of parts, base+0x10 = the
    part definitions (0x58 bytes each: +0 id, +0x1c maximum life), base+0xE8 = the damage block: the DAMAGE taken
    by part i is the float at block + 0x24 + 8*i (SCRGetBossPartsDamage, 0 at the start; getBossPartsLifeRatio
    = (max - damage) / max).
  * the colossus' LIFE is the float at base+0x44 (maximum at base+0x40; 200 for the first colossus). A hit lowers it
    (SCRExecBossPreProcess -> 0x013774c0) besides adding to the part's damage. Every frame the same function checks
    life <= 0 with the dead flag base+0xEC still 0: then it loads the death motions (checkDeadMotionLoadByScript-
    DataSystem), sets the flag and marks the character dead (SCRCharaDead). Writing life = 0 is therefore a clean
    kill through the game's own path; setting +0xEC by hand (the script command at 0x01370000 does) skips the
    motion load and the game halts on "motion address is NULL: anim/..._death_front_loop.anb".
  * the hit handler (tail at 0x013776C0) recomputes life = max - damage and, when it reaches 0, sets life = 0,
    LayoutClass(boss)+0x1AC = 1, playerEnableDeath (0x01465E88) = 0 (the player can no longer die) and raises the
    DEAD_BOSS script interrupt (*0x01566BEC): st_boss_status, the arena's waiting event script, jumps to
    st_boss_dead (death cutscene, tendrils, return to the shrine). Boss.kill() replays exactly that sequence;
    life = 0 alone plays the collapse and the tendrils but nothing after them (the interrupt is never raised).
  * damage only ever goes up during a fight, so two machines fighting the same colossus can merge with a
    per-part maximum: nothing is lost when both hit different parts, and the order of updates does not matter.
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
GET_LAYOUT_CLASS = 0x011EBDA0
SCRIPT_INTR_SIGNAL = 0x011FD008
SCR_INTR_DEAD_BOSS = 0x01566BEC    # holds the DEAD_BOSS interrupt definition
PLAYER_ENABLE_DEATH = 0x01465E88


class Boss:
    def __init__(self, p):
        self.p = p
        self.obj = call(p, GET_BOSS_OBJ)
        if not self.obj:
            raise LookupError("no boss object (not in a colossus area)")
        work = call(p, GET_AI_WORK, self.obj)
        base = p.read32(work)
        self.base = base
        self.count, self.defs, self.block = p.read32_many([base + 0x54, base + 0x10, base + 0xE8])
        if not 0 < self.count <= 64:
            raise LookupError("boss work looks uninitialised (parts=%d)" % self.count)
        raw = p.read_block(self.defs & ~7, 0x58 * self.count + 8)
        shift = self.defs & 7
        self.ids = [struct.unpack_from("<I", raw, shift + 0x58 * i)[0] & 0x3F0 for i in range(self.count)]
        self.max = [struct.unpack_from("<f", raw, shift + 0x58 * i + 0x1C)[0] for i in range(self.count)]

    def life(self):
        """(current life, maximum life, dead flag)."""
        cur, mx, dead = self.p.read32_many([self.base + 0x44, self.base + 0x40, self.base + 0xEC])
        f = lambda w: struct.unpack("<f", struct.pack("<I", w))[0]
        return f(cur), f(mx), dead

    def merge_life(self, remote):
        """Lower the local life to the remote one (life only goes down); a remote 0 kills the colossus here
        through the game's own death path. Returns True when the local life changed."""
        cur, _mx, _dead = self.life()
        if 0.0 <= remote < cur:
            self.p.writef(self.base + 0x44, max(0.0, remote))
            return True
        return False

    def kill(self):
        """Kill the colossus the way the game's own killing blow does (see the module notes). Call it outside
        cutscenes: a colossus killed while its intro plays leaves the intro waiting forever."""
        p = self.p
        p.writef(self.base + 0x44, 0.0)
        layout = call(p, GET_LAYOUT_CLASS, self.obj)
        p.write32(layout + 0x1AC, 1)
        p.write32(PLAYER_ENABLE_DEATH, 0)
        call(p, SCRIPT_INTR_SIGNAL, p.read32(SCR_INTR_DEAD_BOSS))

    def damage_addr(self, i):
        return self.block + 0x24 + 8 * i

    def read_damage(self):
        words = self.p.read32_many([self.damage_addr(i) for i in range(self.count)])
        return [struct.unpack("<f", struct.pack("<I", w))[0] for w in words]

    def merge_damage(self, remote):
        """Raise every part's damage to max(local, remote); returns how many parts changed."""
        changed = 0
        for i, (mine, theirs) in enumerate(zip(self.read_damage(), remote)):
            if theirs > mine:
                self.p.writef(self.damage_addr(i), theirs)
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
    print("boss 0x%08x pos (%.1f, %.1f, %.1f) motion 0x%08x table 0x%08x frame %.1f life %.1f / %.1f dead %d" % (
        boss.obj, *pos, motion, table, frame, *boss.life()))
    for i, (dmg, mx) in enumerate(zip(boss.read_damage(), boss.max)):
        print("  part %2d id %#05x damage %8.2f / %8.2f" % (i, boss.ids[i], dmg, mx))


if __name__ == "__main__":
    pine = Pine()
    show(pine)
    while len(sys.argv) > 1 and sys.argv[1] == "watch":
        time.sleep(1.0)
        print()
        show(pine)
