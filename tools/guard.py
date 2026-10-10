"""Keep the clones consistent with the engine's own object lifetime (EE-side, no PINE latency).

  python3 guard.py install     write the routines and detour the two stage functions
  python3 guard.py uninstall   restore the stage functions
  python3 guard.py status      show the counters
  python3 guard.py clear       delete every clone now (same routine the detours use)

Clones are spawned from the Wander's and the horse's stage segments but the stage manager does not
know them. When it unloads such a segment (checkpoint load after a death in the field reloads the
horse and the accessories that draw the Wander's body), the assets the clones draw and animate with are
freed, and the clones crash the game later ("Jump to unmapped recLUT page" while loading) or lose their
body. Three pieces prevent that:

  * ownership marker: every clone object's delete-request handler (obj+0x18, called and cleared by
    isysRequestDeleteObj; the class's FlushCharacterObjImmediate / FlushLayoutObjImmediate) is
    replaced by CLONE_DELETED, which zeroes the clone's mailbox pointer and tail-calls the original
    handler (kept at the pointer's address + ORIGINAL_OFFSET). So a mailbox pointer is either a live
    clone or 0, whoever deletes it, and obj+0x18 == CLONE_DELETED tells "this object is one of ours".
  * DELETE_ALL requests the deletion of every clone that still carries the marker.
  * stagemanager_unloadstagecore runs DELETE_ALL first (a restart, i.e. a death at the shrine, keeps the
    segments' assets, so the clones survive it).
The bridge then sees the clones gone and spawns them again once the world is playable.
"""
import struct
import sys

from hook import MAILBOX, call
from mips import Asm
from pine import Pine

ISYS_REQUEST_DELETE_OBJ = 0x001C7548
UNLOAD_STAGE_CORE = 0x013665D8
UNLOAD_PROLOGUE = (0x3C02001E, 0x3C07015A)      # lui v0,0x1e ; lui a3,0x15a
RESTART_STAGE_CORE = 0x013667D8
RESTART_PROLOGUE = (0x27BDFFF0, 0x00E0402D)     # addiu sp,sp,-16 ; daddu t0,a3,zero

CLONE_DELETED = 0x000F5000      # delete-request handler installed on every clone object
DELETE_ALL = 0x000F5100         # request deletion of every owned clone
UNLOAD_DETOUR = 0x000F5300
RESTART_DETOUR = 0x000F5400
GUARD_END = 0x000F5500
# The hook's command stack grows down from 0xEFFF0; the detours get their own below it (free RAM).
GUARD_STACK_TOP = 0x000E8000

# Mailbox words holding clone pointers (see spawn.py): Wanders at 0x70..0xf0, horses at 0x120..0x160.
POINTER_RANGES = ((0x70, 0xF0), (0x120, 0x160))
ORIGINAL_OFFSET = 0x400         # original delete-request handler of the object at pointer word + 0x400
MB_GUARD_RUNS = 0x180           # times DELETE_ALL ran because of a stage unload
MB_CLONES_DELETED = 0x184       # clone objects whose deletion went through CLONE_DELETED


def build_clone_deleted():
    a = Asm(CLONE_DELETED)
    for start, end in POINTER_RANGES:
        tag = "r%x" % start
        a.li("t0", MAILBOX + start)
        a.li("t1", MAILBOX + end)
        a.label(tag)
        a.lw("t2", 0, "t0")
        a.bne("t2", "a0", tag + "n")
        a.nop()
        a.lw("t9", ORIGINAL_OFFSET, "t0")
        a.sw("zero", 0, "t0")
        a.sw("zero", ORIGINAL_OFFSET, "t0")
        a.li("t3", MAILBOX + MB_CLONES_DELETED)
        a.lw("t2", 0, "t3")
        a.addiu("t2", "t2", 1)
        a.sw("t2", 0, "t3")
        a.beq("t9", "zero", "out")
        a.nop()
        a.jr("t9")                      # tail call: the class's own flush, a0 = obj, ra = caller
        a.nop()
        a.label(tag + "n")
        a.addiu("t0", "t0", 4)
        a.bne("t0", "t1", tag)
        a.nop()
    a.label("out")
    a.jr("ra")
    a.nop()
    return a.assemble()


def build_delete_all():
    """DELETE_ALL(): isysRequestDeleteObj on every pointer word whose object still has our handler."""
    a = Asm(DELETE_ALL)
    a.addiu("sp", "sp", -0x20)
    a.sd("ra", 0x00, "sp")
    a.sd("s0", 0x08, "sp")
    a.sd("s1", 0x10, "sp")
    for start, end in POINTER_RANGES:
        tag = "d%x" % start
        a.li("s0", MAILBOX + start)
        a.li("s1", MAILBOX + end)
        a.label(tag)
        a.lw("a0", 0, "s0")
        a.beq("a0", "zero", tag + "n")
        a.nop()
        a.lw("t0", 0x18, "a0")
        a.li("t1", CLONE_DELETED)
        a.beq("t0", "t1", tag + "del")
        a.nop()
        a.sw("zero", 0, "s0")             # not ours any more (freed or reused): only forget it
        a.beq("zero", "zero", tag + "n")
        a.sw("zero", ORIGINAL_OFFSET, "s0")
        a.label(tag + "del")
        a.jal(ISYS_REQUEST_DELETE_OBJ)    # calls CLONE_DELETED, which clears the word
        a.nop()
        a.label(tag + "n")
        a.addiu("s0", "s0", 4)
        a.bne("s0", "s1", tag)
        a.nop()
    a.ld("ra", 0x00, "sp")
    a.ld("s0", 0x08, "sp")
    a.ld("s1", 0x10, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 0x20)
    return a.assemble()


def build_detour(base, prologue, resume):
    """Save the arguments, run DELETE_ALL on a private stack (the callers are script coroutines with
    small stacks and the object flush handlers go deep), count, then run the displaced prologue."""
    a = Asm(base)
    a.addiu("sp", "sp", -0x10)
    a.sd("ra", 0x00, "sp")
    a.sd("s0", 0x08, "sp")
    a.move("s0", "sp")
    a.li("sp", GUARD_STACK_TOP)
    a.addiu("sp", "sp", -0x40)
    for i, reg in enumerate(("a0", "a1", "a2", "a3", "t0", "t1", "t2", "t3")):
        a.sd(reg, 8 * i, "sp")
    a.jal(DELETE_ALL)
    a.nop()
    a.li("t0", MAILBOX + MB_GUARD_RUNS)
    a.lw("t1", 0, "t0")
    a.addiu("t1", "t1", 1)
    a.sw("t1", 0, "t0")
    for i, reg in enumerate(("a0", "a1", "a2", "a3", "t0", "t1", "t2", "t3")):
        a.ld(reg, 8 * i, "sp")
    a.move("sp", "s0")
    a.ld("ra", 0x00, "sp")
    a.ld("s0", 0x08, "sp")
    a.addiu("sp", "sp", 0x10)
    a.emit(prologue[0])
    a.j(resume)
    a.emit(prologue[1])                    # delay slot
    return a.assemble()


def j_to(target):
    return 0x08000000 | ((target >> 2) & 0x3FFFFFF)


# Only stage UNLOADS are guarded. A restart (death at the shrine: gStatRestartPlayerChara) re-instantiates
# the segments but keeps their assets, so the clones survive it; deleting and respawning them at every
# death leaked ~60 KB of the ~380 KB game heap per cycle (measured, see MULTIPLAYER.md) until a spawn
# could not get its 77 KB block and the game froze.
SITES = ((UNLOAD_STAGE_CORE, UNLOAD_PROLOGUE, UNLOAD_DETOUR),)
LEGACY_SITES = ((RESTART_STAGE_CORE, RESTART_PROLOGUE, RESTART_DETOUR),)


def installed(p):
    return all(p.read32(site) == j_to(detour) for site, _pro, detour in SITES)


def install(p):
    for site, prologue, detour in LEGACY_SITES:     # an older guard also hooked the restart
        if p.read32(site) == j_to(detour):
            p.write_block(site, struct.pack("<II", *prologue))
    for site, prologue, detour in SITES:
        current = (p.read32(site), p.read32(site + 4))
        if current not in (prologue, (j_to(detour), 0)):
            raise SystemExit("unexpected code at 0x%08x: %08x %08x" % (site, *current))
    blobs = {CLONE_DELETED: build_clone_deleted(), DELETE_ALL: build_delete_all()}
    for site, prologue, detour in SITES:
        blobs[detour] = build_detour(detour, prologue, site + 8)
    for address, code in blobs.items():
        nxt = min([b for b in list(blobs) + [GUARD_END] if b > address])
        assert address + len(code) <= nxt, "guard routine at 0x%x overflows" % address
        p.write_block(address, code + b"\0" * (-len(code) % 8))
    for site, _prologue, detour in SITES:
        # one 8-byte write so no frame sees a half-patched prologue
        p.write_block(site, struct.pack("<II", j_to(detour), 0))


def uninstall(p):
    for site, prologue, _detour in SITES:
        p.write_block(site, struct.pack("<II", *prologue))


def claim(p, pointer_word):
    """Mark the clone object at mailbox pointer word `pointer_word` (absolute address) as ours. Only for
    objects spawned this frame by spawn.py's routine, which does it EE-side; kept for tooling."""
    obj = p.read32(pointer_word)
    p.write32(pointer_word + ORIGINAL_OFFSET, p.read32(obj + 0x18))
    p.write32(obj + 0x18, CLONE_DELETED)


def owned(p, obj):
    """True while obj is a live clone of ours (its delete-request handler is CLONE_DELETED)."""
    if not obj or obj & 3 or not 0x100000 <= obj < 0x2000000:
        return False
    return p.read32(obj + 0x18) == CLONE_DELETED


def delete_all(p):
    call(p, DELETE_ALL)


def counters(p):
    return p.read32(MAILBOX + MB_GUARD_RUNS), p.read32(MAILBOX + MB_CLONES_DELETED)


if __name__ == "__main__":
    pine = Pine()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "install":
        install(pine)
        print("guard installed")
    elif cmd == "uninstall":
        uninstall(pine)
        print("guard removed")
    elif cmd == "clear":
        delete_all(pine)
        print("clones deleted")
    print("installed=%s stage unloads=%d clone objects deleted=%d" % ((installed(pine),) + counters(pine)))
