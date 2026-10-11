"""Share one colossus fight between two machines (the client's colossus mirrors the host's).

Each machine runs its own game, so each has its own colossus object with its own AI. A fight is shared by

  * POSE (host -> client): the host sends the colossus' animation (a machine-independent motion id, the frame),
    root position and rotation; on the client an EE routine called every frame from the logic hook
    (hook.MB_FRAME_CB, before any script runs) forces that motion/frame/rotation, and moves the object when
    the position drifts. The client's own AI keeps running (its damage bookkeeping and hit detection work on the
    bones of the pose that is shown, because the pose is overwritten every frame), but what is seen and hit is
    the host's animation.
  * DAMAGE (both ways): damage per part only grows, so each side raises its local damage to the maximum of
    both (boss.Boss.merge_damage); a part that is dead for one player is dead for both and the game's own
    death logic runs on both machines.

The motion id is the descriptor's word +8 (its offset inside the loaded animation file, identical on every
machine); the descriptor address on a machine is   base + id   with   base = table - [table + 8]   where
table = *(class + 0x2ac). Frame and rotation are plain floats.
"""
import struct
import time

from boss import Boss
from hook import MAILBOX, MB_FRAME_CB, MB_SAVED_SP, call
from mips import Asm
from puppet import (CL_FRAME, CL_MOTION, CL_PREV_FRAME, DEBUG_SET_OBJECT_GEOMETRY, FLAG_MOTION, FLAG_MOVE, FLAG_POSE,
                    FLAG_TELEPORT, FORCE_ACCEPT_MOTION, INIT_ANIMATION_CLASS, JR_RA, RESET_ANIME_FRAME_TABLE,
                    RESET_CHARACTER_OBJ, CALL_LAYOUT_OBJ_DEBUG_RESET, SET_LAYOUT_CLASS_ROOT_QUATERNION, ST_BLEND,
                    ST_FLAGS, ST_FRAME, ST_MOTION, ST_POS, ST_QUAT)

CLASS = 0x90
ROUTINE = 0x000F6000
STATE = MAILBOX + 0xB00          # same layout as a puppet state block, plus the object at +0x14
# (+0x500 collided with guard.py's horse words at +0x520..+0x55F: the pose overwrote the horse clone's delete
# handler and deleting the horse jumped to a float)
ST_OBJ = 0x14
MB_CALLS, MB_TELEPORTS, MB_MOVES = 0x1A0, 0x1A4, 0x1A8   # mailbox counters, for diagnostics
MAX_PARTS = 32
GET_BOSS_OBJ = 0x013439F0
GAME_MODE = 0x01465D70          # gameModeSet bits: bit 0 set during cutscenes and menus, 0 in gameplay
CHAR_CLASS_WORD = 0x01206DD8
BOSS_KIND = 4
MOVE_DISTANCE = 1.0              # horizontal error that triggers a light move of the colossus
MOVE_HEIGHT = 1.0
JUMP_HEIGHT = 8.0
JUMP_DISTANCE = 15.0             # error that triggers a full teleport (a cutscene moved the colossus)


def count(a, offset):
    """Increment a mailbox counter (clobbers t6, t7)."""
    a.li("t6", MAILBOX + offset)
    a.lw("t7", 0, "t6")
    a.addiu("t7", "t7", 1)
    a.sw("t7", 0, "t6")


def build_routine(originals):
    """Per-frame routine (called with jalr from the logic hook, private stack): apply the state block to the
    colossus object it names. The body is the puppet script's, without the coroutine loop."""
    a = Asm(ROUTINE)
    a.addiu("sp", "sp", -0x30)
    a.sd("ra", 0x00, "sp")
    a.sd("s0", 0x08, "sp")
    a.sd("s1", 0x10, "sp")
    a.sd("s2", 0x18, "sp")
    a.li("s2", STATE)
    a.lw("s0", ST_OBJ, "s2")
    a.beq("s0", "zero", "out")
    a.nop()
    count(a, MB_CALLS)
    a.addiu("s1", "s0", CLASS)
    a.lw("t0", ST_FLAGS, "s2")
    a.andi("t1", "t0", FLAG_TELEPORT)
    a.beq("t1", "zero", "soft")
    a.addiu("t2", "zero", ~FLAG_TELEPORT)
    a.and_("t0", "t0", "t2")
    a.sw("t0", ST_FLAGS, "s2")
    count(a, MB_TELEPORTS)
    a.move("a0", "s0")
    a.addiu("a1", "s2", ST_QUAT)
    a.jal(DEBUG_SET_OBJECT_GEOMETRY)
    a.addiu("a2", "s2", ST_POS)
    a.beq("zero", "zero", "motion")
    a.nop()
    a.label("soft")
    a.andi("t1", "t0", FLAG_MOVE)
    a.beq("t1", "zero", "pose")
    a.addiu("t2", "zero", ~FLAG_MOVE)
    a.and_("t0", "t0", "t2")
    a.sw("t0", ST_FLAGS, "s2")
    count(a, MB_MOVES)
    for fn in originals:
        a.li("t3", fn)
        a.li("t4", JR_RA)
        a.sw("t4", 0, "t3")
        a.sw("zero", 4, "t3")
    a.move("a0", "s0")
    a.addiu("a1", "s2", ST_QUAT)
    a.jal(DEBUG_SET_OBJECT_GEOMETRY)
    a.addiu("a2", "s2", ST_POS)
    for fn, (w0, w1) in originals.items():
        a.li("t3", fn)
        a.li("t4", w0)
        a.sw("t4", 0, "t3")
        a.li("t4", w1)
        a.sw("t4", 4, "t3")
    a.lw("t0", ST_FLAGS, "s2")
    a.label("pose")
    a.andi("t1", "t0", FLAG_POSE)
    a.beq("t1", "zero", "motion")
    a.nop()
    a.move("a0", "s1")
    a.jal(SET_LAYOUT_CLASS_ROOT_QUATERNION)
    a.addiu("a1", "s2", ST_QUAT)
    a.label("motion")
    a.lw("t0", ST_FLAGS, "s2")
    a.andi("t1", "t0", FLAG_MOTION)
    a.beq("t1", "zero", "out")
    a.lw("a1", ST_MOTION, "s2")
    a.beq("a1", "zero", "out")
    a.lw("t2", CL_MOTION, "s1")
    a.beq("a1", "t2", "set_frame")
    a.nop()
    a.lwc1(12, ST_BLEND, "s2")
    a.jal(FORCE_ACCEPT_MOTION)
    a.move("a0", "s1")
    a.addiu("a0", "s1", CL_MOTION)
    a.jal(INIT_ANIMATION_CLASS)
    a.lw("a1", ST_MOTION, "s2")
    a.jal(RESET_ANIME_FRAME_TABLE)
    a.move("a0", "s1")
    a.label("set_frame")
    a.lw("t0", ST_FRAME, "s2")
    a.lw("t5", CL_FRAME, "s1")
    a.sw("t5", CL_PREV_FRAME, "s1")
    a.sw("t0", CL_FRAME, "s1")
    a.label("out")
    a.ld("ra", 0x00, "sp")
    a.ld("s0", 0x08, "sp")
    a.ld("s1", 0x10, "sp")
    a.ld("s2", 0x18, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 0x30)
    return a.assemble()


def f32(word):
    return struct.unpack("<f", struct.pack("<I", word))[0]


class BossLink:
    """The local colossus: resolves it (re-checked about once a second), reads/applies pose and damage."""

    def __init__(self, p):
        self.p = p
        self.boss = None
        self.next_scan = 0.0
        self.installed = False
        self.last_target = None
        self.last_move = 0.0
        self.prev = None
        self.last_change = 0.0        # last time the colossus moved/changed animation by itself (own AI)
        self.last_applied = 0.0       # last time a remote pose was forced on it

    # --- setup -----------------------------------------------------------------------------------------
    def install(self):
        p = self.p
        originals = {fn: (p.read32(fn), p.read32(fn + 4)) for fn in (RESET_CHARACTER_OBJ, CALL_LAYOUT_OBJ_DEBUG_RESET)}
        assert all(w[0] != JR_RA for w in originals.values()), "resets are currently patched out"
        code = build_routine(originals)
        assert len(code) < 0x400
        p.write_block(ROUTINE, code + b"\0" * (-len(code) % 8))
        p.write_block(STATE, b"\0" * 0x40)
        p.write32(MAILBOX + MB_FRAME_CB, ROUTINE)
        self.installed = True

    def uninstall(self):
        self.p.write32(MAILBOX + MB_FRAME_CB, 0)
        self.p.write_block(STATE, b"\0" * 0x40)
        self.installed = False

    # --- the local colossus ----------------------------------------------------------------------------
    def refresh(self):
        """Find the colossus object (None when there is none or its AI data is not initialised yet)."""
        if time.time() < self.next_scan and self.boss is not None:
            return self.boss
        self.next_scan = time.time() + 1.0
        try:
            obj = call(self.p, GET_BOSS_OBJ, timeout=1.0)
        except TimeoutError:
            return self.boss
        word, kind = (self.p.read32_many([obj + 4, obj + CLASS + 0x370]) if obj else (0, 0))
        if not obj or word != CHAR_CLASS_WORD or (kind & 0xFFFF) != BOSS_KIND:
            self.set_boss(None)
            return None
        if self.boss is None or self.boss.obj != obj:
            try:
                self.set_boss(Boss(self.p))
            except LookupError:
                self.set_boss(None)
        return self.boss

    def set_boss(self, boss):
        if (boss.obj if boss else 0) != (self.boss.obj if self.boss else 0):
            self.p.write32(STATE + ST_OBJ, 0)           # the routine does nothing for a stale object
            self.p.write32(STATE + ST_FLAGS, 0)
            self.last_target = None
        self.boss = boss

    def table_base(self):
        table = self.p.read32(self.boss.obj + CLASS + 0x2AC)
        return table - self.p.read32(table + 8)

    # --- who is in charge --------------------------------------------------------------------------------
    def mirroring(self):
        return time.time() - self.last_applied < 0.5

    def awake(self):
        """True while the local AI is running the colossus (it moved or changed animation in the last 3 s and
        is not just being mirrored)."""
        return self.boss is not None and not self.mirroring() and time.time() - self.last_change < 3.0

    # --- local -> wire ---------------------------------------------------------------------------------
    def sample(self):
        """(motion id, frame, pos, quat, damage list, awake, life) of the local colossus, or None."""
        boss = self.refresh()
        if boss is None:
            return None
        p, cl = self.p, boss.obj + CLASS
        quat_ptr = p.read32(cl + 0x13C)
        motion, frame = p.read32_many([cl + 0x1C0, cl + 0x1C8])
        mid = p.read32(motion + 8) if motion else 0
        x, y, z = (p.readf(boss.obj + 0xA0 + 4 * i) for i in range(3))
        quat = tuple(p.readf(quat_ptr + 4 * i) for i in range(4)) if quat_ptr else (0.0, 0.0, 0.0, 1.0)
        now = time.time()
        if not self.mirroring() and self.prev is not None:
            px, py, pz, pm = self.prev
            if pm != mid or abs(px - x) + abs(pz - z) > 0.05:
                self.last_change = now
        self.prev = (x, y, z, mid)
        return mid, f32(frame), (x, y, z), quat, boss.read_damage(), self.awake(), boss.life()[0]

    # --- wire -> local ---------------------------------------------------------------------------------
    def apply_pose(self, mid, frame, pos, quat, blend=6.0):
        """Mirror the host's colossus: motion/frame/rotation every frame (EE routine), position by light moves."""
        boss = self.refresh()
        if boss is None or not self.installed:
            return
        p = self.p
        if p.read32(GAME_MODE) & 1:
            self.stop_pose()    # the local game is in a cutscene/menu: its own demo drives the colossus
            return
        life, _mx, dead = boss.life()
        if life <= 0.0 or dead:
            self.stop_pose()    # dying here: the local game plays its own death (its motions are loaded locally)
            return
        flags = FLAG_POSE | FLAG_MOTION
        cx, cy, cz = (p.readf(boss.obj + 0xA0 + 4 * i) for i in range(3))
        err = ((cx - pos[0]) ** 2 + (cz - pos[2]) ** 2) ** 0.5
        dy = abs(cy - pos[1])
        now = time.time()
        if self.last_target is None or err > JUMP_DISTANCE or dy > JUMP_HEIGHT:
            flags |= FLAG_TELEPORT
            self.last_move = now
        elif (err > MOVE_DISTANCE or dy > MOVE_HEIGHT) and now - self.last_move > 0.25:
            flags |= FLAG_MOVE
            self.last_move = now
        self.last_target = pos
        self.last_applied = now
        motion = self.table_base() + mid if mid else 0
        # some motions (the death ones) are loaded on demand: only force a motion whose descriptor is really
        # there on this machine, i.e. its word +8 is the id (forcing an unloaded one halts the game)
        if motion and p.read32(motion + 8) != mid:
            motion = 0
        block = struct.pack("<IIIffI8x4f4f", 0, flags, motion, frame, blend, boss.obj, pos[0], pos[1], pos[2], 1.0, *quat)
        p.write_block(STATE, block)

    def stop_pose(self):
        """Stop forcing the pose (peer gone / its colossus is dormant): the local AI is in charge again."""
        if self.boss is not None and self.last_target is not None:
            self.p.write32(STATE + ST_FLAGS, 0)
            self.last_target = None

    def idle_check(self):
        """Release the pose when nothing has been mirrored for a while."""
        if self.last_target is not None and time.time() - self.last_applied > 1.5:
            self.stop_pose()

    def merge_life(self, remote):
        """Lower the local life to the peer's. Never during a local cutscene/menu: a colossus killed while its
        intro plays leaves the intro waiting forever (tested); the merge simply happens once the scene ends."""
        boss = self.refresh()
        if boss is None or self.p.read32(GAME_MODE) & 1:
            return False
        life, _mx, dead = boss.life()
        if remote <= 0.0 < life and not dead:
            boss.kill()             # the game's own killing-blow sequence: death cutscene, tendrils, shrine
            return True
        return boss.merge_life(remote)

    def merge_damage(self, remote):
        boss = self.refresh()
        return boss.merge_damage(remote[:boss.count]) if boss else 0
