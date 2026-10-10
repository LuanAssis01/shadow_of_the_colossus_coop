"""Turn a spawned Wander clone into a puppet driven from outside the emulator.

  python3 puppet.py [CLONE [SLOT]]  restart CLONE's script as puppet SLOT (default: clone 0, slot 0)

The clone is spawned with the player's own script (nico_player1), which reads the
1P pad and runs the player's logic, so it would copy the local player's input.
Every script object owns a coroutine whose state is its script class (obj+0x20
for characters): +0x00/+0x04 entry, +0x28 run state, +0x38 restart request,
+0x3c restart info ({?, entry}). When +0x38 is set, the script executor
(execScript -> FUN_011fc9b8) re-initialises the coroutine with
isysInitSubThreadExecEnv(obj, info->entry) and starts it fresh on the next frame.
That is used to swap nico_player1 for an injected puppet script.

Each frame the puppet script reads its state block (written through PINE) and
  - motion (flag 2): when the motion descriptor differs from the current one
    (class+0x1c0), switches like the engine's demo-motion player does:
    ForceAcceptMotion($f12=blend frames, class, motion), InitAnimationClass,
    ResetCharacterClassAnimeFrameTableInfo; then sets the frame (class+0x1c8/0x1cc);
  - pose (flag 1): DebugSetObjectGeometry(obj, quat, pos), which carries the
    attached objects (hair, aliases, sword, sheath) along;
then yields with scrRun. Animation, geometry, cloth and drawing stay with the engine.
"""
import struct
import sys
import time

from hook import MAILBOX
from mips import Asm
from pine import Pine

SCR_RUN = 0x013CF9E8
IS_GET_THIS_SUB_THREAD = 0x001CA1B0
FORCE_ACCEPT_MOTION = 0x011C7498
INIT_ANIMATION_CLASS = 0x011B4C20
RESET_ANIME_FRAME_TABLE = 0x011C7688
DEBUG_SET_OBJECT_GEOMETRY = 0x01341218
SET_LAYOUT_CLASS_ROOT_QUATERNION = 0x011EC5D8
# DebugSetObjectGeometry = teleport, but it also calls these two, which wipe the character's motion,
# velocity and cloth. The "light move" (FLAG_MOVE) runs the same teleport with both turned into an
# immediate return for the duration of the call, so the position base moves and nothing else resets.
RESET_CHARACTER_OBJ = 0x01339EA8
CALL_LAYOUT_OBJ_DEBUG_RESET = 0x011ED300
JR_RA, NOP = 0x03E00008, 0x00000000

PUPPET_SCRIPT = 0x000F3000      # one script per slot, PUPPET_SCRIPT_SIZE apart
PUPPET_SCRIPT_SIZE = 0x200
PUPPET_RESTART_INFO = 0x000F3800  # {0, entry} per slot, 8 bytes apart
PUPPET_STATE = 0x000F8200       # one state block per slot
STATE_SIZE = 0x40
MAX_SLOTS = 4
MB_CLONES = 0x70                # spawn.py stores the player clone first
CLONE_STRIDE = 0x20             # clone k: MB_CLONES + k * CLONE_STRIDE (same as spawn.py)

# State block layout.
ST_SEQ, ST_FLAGS, ST_MOTION, ST_FRAME, ST_BLEND, ST_POS, ST_QUAT = 0x00, 0x04, 0x08, 0x0C, 0x10, 0x20, 0x30
FLAG_POSE, FLAG_MOTION, FLAG_TELEPORT, FLAG_MOVE = 1, 2, 4, 8

SC = 0x20                       # script class offset in a character object
SC_ENTRY, SC_STATE, SC_RESTART, SC_RESTART_INFO = 0x00, 0x28, 0x38, 0x3C
CLASS = 0x90                    # character class offset
CL_MOTION, CL_FRAME, CL_PREV_FRAME = 0x1C0, 0x1C8, 0x1CC
# Object offsets holding the world position (found by teleporting twice and diffing):
# layout position/previous (+0xa0, +0xb0, +0xd0), the collision finder's current,
# previous and past positions (+0x550, +0x560, +0x590) and two controller copies.
# The engine rederives the position from these every frame, so all must move together.
# Writing them does not stick: the engine recomputes the position each frame from a base
# plus the motion's root offset, so the position is only set by the teleport (below).
POSITION_FIELDS = (0xA0, 0xB0, 0xD0, 0x550, 0x560, 0x590, 0x1140, 0x11E0)


def clone_address(p, slot):
    """Player object of clone `slot` (0 when that clone was not spawned)."""
    return p.read32(MAILBOX + MB_CLONES + CLONE_STRIDE * slot)


def script_address(slot):
    return PUPPET_SCRIPT + slot * PUPPET_SCRIPT_SIZE


def state_address(slot):
    return PUPPET_STATE + slot * STATE_SIZE


def build_puppet_script(slot, originals):
    """originals: {function address: (word0, word1)} of the two functions the light move disables."""
    a = Asm(script_address(slot))
    a.addiu("sp", "sp", -0x20)
    a.sd("ra", 0x00, "sp")
    a.sd("s0", 0x08, "sp")
    a.sd("s1", 0x10, "sp")
    a.sd("s2", 0x18, "sp")
    a.jal(IS_GET_THIS_SUB_THREAD)
    a.nop()
    a.move("s0", "v0")                  # object
    a.addiu("s1", "s0", CLASS)          # character class
    a.li("s2", state_address(slot))
    a.label("frame")
    # --- pose: pass 1 (teleport, once) ---
    a.lw("t0", ST_FLAGS, "s2")
    a.andi("t1", "t0", FLAG_TELEPORT)
    a.beq("t1", "zero", "soft")
    a.addiu("t2", "zero", ~FLAG_TELEPORT)       # delay slot
    a.and_("t0", "t0", "t2")
    a.sw("t0", ST_FLAGS, "s2")                   # clear the request
    a.move("a0", "s0")
    a.addiu("a1", "s2", ST_QUAT)
    a.jal(DEBUG_SET_OBJECT_GEOMETRY)             # also resets the character (motion!)
    a.addiu("a2", "s2", ST_POS)                  # delay slot
    a.beq("zero", "zero", "motion")
    a.nop()
    # --- light move: teleport the position base without resetting motion / velocity / cloth ---
    a.label("soft")
    a.andi("t1", "t0", FLAG_MOVE)
    a.beq("t1", "zero", "pose")
    a.addiu("t2", "zero", ~FLAG_MOVE)            # delay slot
    a.and_("t0", "t0", "t2")
    a.sw("t0", ST_FLAGS, "s2")                   # clear the request
    for fn in originals:                         # disable the two resets (restored right after)
        a.li("t3", fn)
        a.li("t4", JR_RA)
        a.sw("t4", 0, "t3")
        a.sw("zero", 4, "t3")
    a.move("a0", "s0")
    a.addiu("a1", "s2", ST_QUAT)
    a.jal(DEBUG_SET_OBJECT_GEOMETRY)
    a.addiu("a2", "s2", ST_POS)                  # delay slot
    for fn, (w0, w1) in originals.items():
        a.li("t3", fn)
        a.li("t4", w0)
        a.sw("t4", 0, "t3")
        a.li("t4", w1)
        a.sw("t4", 4, "t3")
    a.lw("t0", ST_FLAGS, "s2")
    # --- pose: per-frame rotation without the reset ---
    a.label("pose")
    a.andi("t1", "t0", FLAG_POSE)
    a.beq("t1", "zero", "motion")
    a.nop()
    # NOT writing POSITION_FIELDS here: the engine recomputes the position from its hidden base every
    # frame, so a per-frame write only makes the object jump by the tracking error and back, and the
    # cloth/hair simulation reads that as a violent wind (reproduced with a constant 0.8 error).
    a.move("a0", "s1")
    a.jal(SET_LAYOUT_CLASS_ROOT_QUATERNION)
    a.addiu("a1", "s2", ST_QUAT)                 # delay slot
    # --- motion ---
    a.label("motion")
    a.lw("t0", ST_FLAGS, "s2")
    a.andi("t1", "t0", FLAG_MOTION)
    a.beq("t1", "zero", "yield")
    a.lw("a1", ST_MOTION, "s2")                  # delay slot
    a.beq("a1", "zero", "yield")
    a.lw("t2", CL_MOTION, "s1")                  # delay slot
    a.beq("a1", "t2", "set_frame")
    a.nop()
    a.lwc1(12, ST_BLEND, "s2")
    a.jal(FORCE_ACCEPT_MOTION)
    a.move("a0", "s1")                           # delay slot
    a.addiu("a0", "s1", CL_MOTION)
    a.jal(INIT_ANIMATION_CLASS)
    a.lw("a1", ST_MOTION, "s2")                  # delay slot
    a.jal(RESET_ANIME_FRAME_TABLE)
    a.move("a0", "s1")                           # delay slot
    a.label("set_frame")
    a.lw("t0", ST_FRAME, "s2")
    a.lw("t5", CL_FRAME, "s1")
    a.sw("t5", CL_PREV_FRAME, "s1")              # previous frame drives the root motion
    a.sw("t0", CL_FRAME, "s1")
    a.label("yield")
    a.jal(SCR_RUN)
    a.nop()
    a.beq("zero", "zero", "frame")
    a.nop()
    code = a.assemble()
    assert len(code) <= PUPPET_SCRIPT_SIZE
    return code


def write_state(p, slot, pos=None, quat=None, motion=0, frame=0.0, blend=6.0, seq=0, teleport=False, move=False):
    """Publish one remote-player sample for the puppet in `slot`."""
    flags = (FLAG_POSE if pos is not None else 0) | (FLAG_MOTION if motion else 0) | (FLAG_TELEPORT if teleport else 0) | (FLAG_MOVE if move else 0)
    pos = pos or (0.0, 0.0, 0.0)
    quat = quat or (0.0, 0.0, 0.0, 1.0)
    block = struct.pack("<IIIff12x4f4f", seq & 0xFFFFFFFF, flags, motion, frame, blend,
                        pos[0], pos[1], pos[2], 1.0, *quat)
    p.write_block(state_address(slot), block)


def make_puppet(p, clone, slot=0, timeout=5.0):
    """Restart clone's script as the puppet script of `slot`; wait until it runs."""
    sc = clone + SC
    if p.read32(sc + SC_ENTRY) == script_address(slot) and not p.read32(sc + SC_RESTART):
        return  # already the puppet; restarting a running coroutine hangs the game's logic
    write_state(p, slot)  # flags 0: hold still until the first sample
    originals = {fn: (p.read32(fn), p.read32(fn + 4)) for fn in (RESET_CHARACTER_OBJ, CALL_LAYOUT_OBJ_DEBUG_RESET)}
    assert all(w[0] != JR_RA for w in originals.values()), "resets are currently patched out"
    code = build_puppet_script(slot, originals)
    p.write_block(script_address(slot), code + b"\0" * (-len(code) % 8))
    info = PUPPET_RESTART_INFO + 8 * slot
    p.write_block(info, struct.pack("<II", 0, script_address(slot)))
    p.write32(sc + SC_RESTART_INFO, info)
    p.write32(sc + SC_RESTART, 1)
    end = time.time() + timeout
    while p.read32(sc + SC_RESTART) or p.read32(sc + SC_ENTRY) != script_address(slot):
        if time.time() > end:
            raise TimeoutError("script executor did not restart the clone's script")
        time.sleep(0.02)


if __name__ == "__main__":
    pine = Pine()
    slot = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    target = int(sys.argv[1], 16) if len(sys.argv) > 1 else clone_address(pine, slot)
    make_puppet(pine, target, slot)
    print("0x%08x now runs the puppet script (state %#x)" % (target, pine.read32(target + SC + SC_STATE)))
