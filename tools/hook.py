"""Install per-frame MIPS hooks into running SotC (SCUS-97472) through PINE.

  python3 hook.py install       write the stubs and redirect both frame calls
  python3 hook.py uninstall     restore the original calls
  python3 hook.py status        show hook counters
  python3 hook.py nudge OBJ DY  teleport object OBJ (hex) up by DY via DebugSetObjectGeometry
  python3 hook.py call FN [ARGS] call EE function FN(args...) from the logic hook, print v0

The game's frame work runs as script coroutines that loop forever, yielding
through scrRun(). Two of them are hooked by replacing one `jal`:
  logic: the main task's `jal graphFrameInit` (also runs InitLayoutObjMatrix);
         this stub executes mailbox commands.
  draw:  the fade/demo-bar task's `jal graphFrameFinish`; counter only.
Each stub saves the argument registers and $f12, runs, then calls the original.
"""
import struct
import sys
import time

from mips import Asm
from pine import Pine

# Addresses from SCUS-97472 v1.00 (recovered XFF symbols).
GRAPH_FRAME_INIT = 0x01189F68
GRAPH_FRAME_FINISH = 0x01189C58
DEBUG_SET_OBJECT_GEOMETRY = 0x01341218

# Free EE RAM (zero in every snapshot, title screen through gameplay).
# Mailbox map (offsets from MAILBOX; keep it current, three overlaps already cost freezes):
#   0x000-0x06F hook commands          0x070-0x0EF spawn: Wander clone pointers (4 x 0x20)
#   0x100-0x11B pad override, frame cb 0x120-0x15F spawn: horse clone pointers (4 x 0x10)
#   0x180-0x18F guard / spawn counters 0x1A0-0x1AB bosssync counters
#   0x200-0x3FF puppet state blocks (8 x 0x40)
#   0x470-0x4EF, 0x520-0x55F  guard: original delete handlers (pointer word + 0x400)
#   0x600-0x60F warp.py scratch        0x700-0x713 barrier.py
#   0x870-0x8EF, 0x920-0x95F  guard: layout definitions (pointer word + 0x800)
#   0x9F0       scratch                0xA00-0xA7F probe.py counters
#   0xB00-0xB3F bosssync state block
MAILBOX = 0x000F8000
MB_FRAME, MB_CMD, MB_OBJ, MB_QUAT, MB_POS, MB_DONE, MB_DRAW = 0x00, 0x04, 0x08, 0x10, 0x20, 0x30, 0x34
# Generic call: CMD=2 calls MB_FN(a0..a3, t0, t1) from MB_ARGS and stores v0 in MB_RET.
MB_RET, MB_FN, MB_ARGS = 0x3C, 0x40, 0x44
MB_FRET = 0x38  # $f0 of the last generic call (float return value)
CMD_TELEPORT, CMD_CALL = 1, 2
MB_SAVED_SP = 0x60
# Pad override (player 1): when MB_PAD_EN != 0 the logic hook overwrites the game's pad state
# every frame before any script runs: held/push/release/repeat button words and the left
# stick bytes (see tools/pad.py).
MB_PAD_EN, MB_PAD_WORDS, MB_PAD_STICK = 0x100, 0x104, 0x114
# Per-frame callback: when MB_FRAME_CB is non-zero the logic hook calls it (no arguments) every frame, before
# any script runs, on the private stack (see bosssync.py).
MB_FRAME_CB = 0x118
PAD_STATE = 0x01287A00
PAD_WORD_OFFSETS = (0x00, 0x08, 0x0C, 0x10)
PAD_STICK_OFFSET = 0x54
# Script coroutines run on small stacks; game calls made from the hook (spawning
# in particular) overflow them, so commands run on a private stack in free RAM.
PRIVATE_STACK_TOP = 0x000EFFF0

# name: (call site, original target, stub address, counter offset, runs commands)
SITES = {
    "logic": (0x0134637C, GRAPH_FRAME_INIT, 0x000F0000, MB_FRAME, True),
    "draw": (0x013463F8, GRAPH_FRAME_FINISH, 0x000F0400, MB_DRAW, False),
}


STUB_REGION = (0x000F0000, 0x000F8000)


def jal(target):
    return 0x0C000000 | ((target >> 2) & 0x3FFFFFF)


def is_our_jal(word):
    target = (word & 0x3FFFFFF) << 2
    return word >> 26 == 3 and STUB_REGION[0] <= target < STUB_REGION[1]


def build_stub(base, original, counter, commands):
    a = Asm(base)
    a.addiu("sp", "sp", -0x60)
    a.sd("ra", 0x00, "sp")
    a.sd("s0", 0x08, "sp")
    for i, reg in enumerate(("a0", "a1", "a2", "a3")):
        a.sd(reg, 0x10 + 8 * i, "sp")
    a.swc1(12, 0x30, "sp")
    a.li("s0", MAILBOX)
    a.lw("t0", counter, "s0")
    a.addiu("t0", "t0", 1)
    a.sw("t0", counter, "s0")
    if commands:
        a.lw("t0", MB_PAD_EN, "s0")
        a.beq("t0", "zero", "nopad")
        a.li("t2", PAD_STATE)                    # delay slot (lui part executes harmlessly)
        for i, off in enumerate(PAD_WORD_OFFSETS):
            a.lw("t1", MB_PAD_WORDS + 4 * i, "s0")
            a.sw("t1", off, "t2")
        a.lw("t1", MB_PAD_STICK, "s0")
        a.sw("t1", PAD_STICK_OFFSET, "t2")
        a.label("nopad")
        a.lw("t9", MB_FRAME_CB, "s0")
        a.beq("t9", "zero", "nocb")
        a.nop()
        a.sw("sp", MB_SAVED_SP, "s0")
        a.li("sp", PRIVATE_STACK_TOP)
        a.jalr("t9")
        a.nop()
        a.lw("sp", MB_SAVED_SP, "s0")
        a.label("nocb")
        a.lw("t0", MB_CMD, "s0")
        a.beq("t0", "zero", "skip")
        a.addiu("t1", "zero", CMD_CALL)  # delay slot
        a.sw("sp", MB_SAVED_SP, "s0")
        a.li("sp", PRIVATE_STACK_TOP)
        a.bne("t0", "t1", "teleport")
        a.sw("zero", MB_CMD, "s0")  # delay slot
        a.lw("t9", MB_FN, "s0")
        for i, reg in enumerate(("a0", "a1", "a2", "a3", "t0", "t1")):
            a.lw(reg, MB_ARGS + 4 * i, "s0")
        a.jalr("t9")
        a.nop()
        a.swc1(0, MB_FRET, "s0")
        a.beq("zero", "zero", "done")
        a.sw("v0", MB_RET, "s0")  # delay slot
        a.label("teleport")
        a.lw("a0", MB_OBJ, "s0")
        a.addiu("a1", "s0", MB_QUAT)
        a.jal(DEBUG_SET_OBJECT_GEOMETRY)
        a.addiu("a2", "s0", MB_POS)  # delay slot
        a.label("done")
        a.lw("sp", MB_SAVED_SP, "s0")
        a.lw("t0", MB_DONE, "s0")
        a.addiu("t0", "t0", 1)
        a.sw("t0", MB_DONE, "s0")
        a.label("skip")
    for i, reg in enumerate(("a0", "a1", "a2", "a3")):
        a.ld(reg, 0x10 + 8 * i, "sp")
    a.lwc1(12, 0x30, "sp")
    a.jal(original)
    a.nop()
    a.ld("ra", 0x00, "sp")
    a.ld("s0", 0x08, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 0x60)  # delay slot
    return a.assemble()


def install(p):
    for name, (site, original, stub, counter, commands) in SITES.items():
        current = p.read32(site)
        if is_our_jal(current):
            # Detach before rewriting the stub so no frame runs half-written code.
            p.write32(site, jal(original))
            time.sleep(0.2)
        elif current != jal(original):
            raise SystemExit("unexpected instruction at %s site 0x%08x: 0x%08x" % (name, site, current))
    p.write_block(MAILBOX, b"\0" * 0x70)  # 0x70+ belongs to callers (e.g. spawn results)
    for name, (site, original, stub, counter, commands) in SITES.items():
        p.write_block(stub, build_stub(stub, original, counter, commands))
        p.write32(site, jal(stub))
        print("%s hook: 0x%08x -> stub 0x%08x" % (name, site, stub))


def uninstall(p):
    for name, (site, original, stub, _counter, _commands) in SITES.items():
        if is_our_jal(p.read32(site)):
            p.write32(site, jal(original))
            print("%s hook removed" % name)


def counters(p):
    return p.read32(MAILBOX + MB_FRAME), p.read32(MAILBOX + MB_DRAW)


def status(p):
    (l0, d0) = counters(p)
    time.sleep(1.0)
    (l1, d1) = counters(p)
    print("logic=%d (+%d/s) draw=%d (+%d/s) commands=%d" % (
        l1, l1 - l0, d1, d1 - d0, p.read32(MAILBOX + MB_DONE)))


def call(p, fn, *args, timeout=5.0):
    """Run fn(*args) on the EE inside the logic hook; return v0 (low 32 bits)."""
    words = list(args) + [0] * (6 - len(args))
    p.write32(MAILBOX + MB_FN, fn)
    for i, w in enumerate(words):
        p.write32(MAILBOX + MB_ARGS + 4 * i, w & 0xFFFFFFFF)
    done = p.read32(MAILBOX + MB_DONE)
    p.write32(MAILBOX + MB_CMD, CMD_CALL)
    end = time.time() + timeout
    while p.read32(MAILBOX + MB_DONE) == done:
        if time.time() > end:
            raise TimeoutError("hook did not run the call")
        time.sleep(0.005)
    return p.read32(MAILBOX + MB_RET)


def call_float(p, fn, *args, timeout=5.0):
    """Like call() but return the function's float result ($f0)."""
    call(p, fn, *args, timeout=timeout)
    return p.readf(MAILBOX + MB_FRET)


def teleport(p, obj, pos, quat=(0.0, 0.0, 0.0, 1.0)):
    """Place obj at internal-space pos (x, y, z) with rotation quat via the game."""
    data = struct.pack("<I4x4f4f", obj, *quat, pos[0], pos[1], pos[2], 1.0)
    p.write_block(MAILBOX + MB_OBJ, data)
    p.write32(MAILBOX + MB_CMD, CMD_TELEPORT)


def nudge(p, obj, dy):
    pos = [p.readf(obj + 0xA0 + 4 * i) for i in range(3)]
    print("before", [round(v, 2) for v in pos])
    teleport(p, obj, (pos[0], pos[1] + dy, pos[2]))
    time.sleep(0.2)
    print("after ", [round(p.readf(obj + 0xA0 + 4 * i), 2) for i in range(3)],
          "commands", p.read32(MAILBOX + MB_DONE))


if __name__ == "__main__":
    pine = Pine()
    cmd = sys.argv[1]
    if cmd == "install":
        install(pine)
    elif cmd == "uninstall":
        uninstall(pine)
    elif cmd == "status":
        status(pine)
    elif cmd == "call":
        print(hex(call(pine, *[int(x, 0) for x in sys.argv[2:]])))
    elif cmd == "nudge":
        nudge(pine, int(sys.argv[2], 16), float(sys.argv[3]))
