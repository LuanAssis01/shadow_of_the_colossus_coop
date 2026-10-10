"""Spawn a second, complete Wander for the remote player.

  python3 spawn.py         clone the player segment and every accessory segment

The Wander is drawn by several layout objects, all posed by one skeleton:
  player segment (0xb6)  player character (skeleton, sword in hand, cape cloth;
                         script nico_player1) and its sheath script;
  accessory segments     loaded by gStatLoadPlayerAccessories, ids kept in the
                         globals below (they change with the active boss/costume):
                         0xb8  hair character (nico_parentSync, motion-synced);
                         0xb7  alias object drawing torso, arms and legs;
                         0xb9  second alias object.
Alias objects (CreateAliasScriptLayoutObj, script st_collisionalias) have no
skeleton: InitAliasLayoutObj copies the parent's position/matrix pointers and
stores the parent in lc+0x194, and the draw reuses the parent's matrix packet.
Without them a clone shows only the sword, hair, belt strips and sheath.

Children find their parent through the parent's layout entry (+0x48
spawned-object slot), so the clone is assembled by the game itself if the
children spawn while the player's entry points at the cloned player.

A layout entry spawns only while its +0x48 slot and its definition's instance
cache (D+8) are empty. An injected EE routine, run once inside the logic hook on
its private stack, therefore:
  1. clears the player segment's slots/caches and runs
     initstage(group, segment, -1, -1, 0, 0) (player clone first, then its
     sheath, which already parents to it);
  2. does the same for each accessory segment (they parent to the clone);
  3. copies the clone pointers to the mailbox and restores every original slot
     and cache, so the game keeps tracking the local player.
While it runs, the core allocator is detoured to return zeroed memory (see below).
No frame runs between these steps, so no script ever sees the swapped entries.
"""
import struct
import time

from hook import MAILBOX, call, counters
from layout import handle_block, segment_entries, u32
from mips import Asm
from pine import Pine

INITSTAGE = 0x013D0318
GC_GET_STAGE_DATA = 0x01365D58
PLAYER_GROUP = 0x001E0C88
# Segment ids as loaded by gStatLoadPlayerAccessories: plain words, and pointers
# to {?, segment, ...} records (0 = none).
PLAYER_SEGMENT_VAR = 0x01465E00
ACCESSORY_SEGMENT_VARS = (0x01465E04, 0x01465E08)
ACCESSORY_RECORD_VARS = (0x01465E0C, 0x01465E10)
ROUTINE = 0x000F1000           # injected spawn routine (inside the free stub region)
MB_CLONES = 0x70               # mailbox: cloned object pointers, one word per entry
CLONE_STRIDE = 0x20            # clone k keeps its (<= 8) object pointers at MB_CLONES + k * CLONE_STRIDE
MAX_CLONES = 4

# Zero-filling allocator detour. The stage loader spawns objects on a freshly
# cleared heap and the engine relies on that; clones come from a reused heap, so
# while they spawn the core allocator FUN_001addc0(heap, size, align, ...) is
# detoured to clear every block it returns (always safe for a malloc).
CORE_ALLOC = 0x001ADDC0
CORE_ALLOC_PROLOGUE = (0x27BDFFB0, 0x00A0482D)  # addiu sp,sp,-80 ; daddu t1,a1,zero
MEMSET = 0x001283D0
ZERO_ALLOC = 0x000F1800        # wrapper
ZERO_ALLOC_TRAMPOLINE = 0x000F1900


def build_zero_alloc():
    """Wrapper + trampoline for CORE_ALLOC; returns {address: code}."""
    t = Asm(ZERO_ALLOC_TRAMPOLINE)
    for word in CORE_ALLOC_PROLOGUE:
        t.emit(word)
    t.j(CORE_ALLOC + 8)
    t.nop()
    w = Asm(ZERO_ALLOC)
    w.addiu("sp", "sp", -0x20)
    w.sd("ra", 0x00, "sp")
    w.sd("s0", 0x08, "sp")
    w.sd("s1", 0x10, "sp")
    w.jal(ZERO_ALLOC_TRAMPOLINE)
    w.move("s1", "a1")  # delay slot: keep the requested size
    w.beq("v0", "zero", "out")
    w.move("s0", "v0")  # delay slot
    w.move("a0", "v0")
    w.move("a1", "zero")
    w.jal(MEMSET)
    w.move("a2", "s1")  # delay slot
    w.label("out")
    w.move("v0", "s0")
    w.ld("ra", 0x00, "sp")
    w.ld("s0", 0x08, "sp")
    w.ld("s1", 0x10, "sp")
    w.jr("ra")
    w.addiu("sp", "sp", 0x20)  # delay slot
    return {ZERO_ALLOC: w.assemble(), ZERO_ALLOC_TRAMPOLINE: t.assemble()}


def hooks_advance(p, wait=1.0):
    """(logic advanced, draw advanced) over `wait` seconds."""
    before = counters(p)
    time.sleep(wait)
    after = counters(p)
    return after[0] != before[0], after[1] != before[1]


def entries_of(p, ram, segment):
    """[(entry, definition, original object)] for every spawned entry of segment."""
    out = []
    for entry in segment_entries(ram, call(p, GC_GET_STAGE_DATA, segment)):
        obj = u32(ram, entry + 0x48)
        definition = u32(ram, handle_block(ram, entry + 4) + 0x10)
        if not obj or u32(ram, definition + 8) != obj:
            raise SystemExit("segment 0x%x entry 0x%08x is not in the expected spawned state" % (segment, entry))
        out.append((entry, definition, obj))
    return out


def build_routine(groups, mailbox_slot=MAILBOX + MB_CLONES):
    """groups: [(segment, [(entry, definition, obj)])] spawned in order; clone pointers go to mailbox_slot."""
    a = Asm(ROUTINE)
    a.addiu("sp", "sp", -0x20)
    a.sd("ra", 0x00, "sp")
    # detour the core allocator: j ZERO_ALLOC ; nop
    a.li("t0", CORE_ALLOC)
    a.li("t1", 0x08000000 | ((ZERO_ALLOC >> 2) & 0x3FFFFFF))
    a.sw("t1", 0, "t0")
    a.sw("zero", 4, "t0")
    for segment, items in groups:
        for entry, definition, _obj in items:
            a.li("t0", entry)
            a.sw("zero", 0x48, "t0")
            a.li("t0", definition)
            a.sw("zero", 8, "t0")
        a.li("a0", PLAYER_GROUP)
        a.addiu("a1", "zero", segment)
        a.addiu("a2", "zero", -1)
        a.addiu("a3", "zero", -1)
        a.move("t0", "zero")
        a.jal(INITSTAGE)
        a.move("t1", "zero")  # delay slot
    slot = 0
    a.li("t2", mailbox_slot)
    for _segment, items in groups:
        for entry, definition, obj in items:
            a.li("t0", entry)
            a.lw("t1", 0x48, "t0")
            a.sw("t1", 4 * slot, "t2")
            a.li("t1", obj)
            a.sw("t1", 0x48, "t0")
            a.li("t0", definition)
            a.sw("t1", 8, "t0")
            slot += 1
    # restore the allocator prologue
    a.li("t0", CORE_ALLOC)
    a.li("t1", CORE_ALLOC_PROLOGUE[0])
    a.sw("t1", 0, "t0")
    a.li("t1", CORE_ALLOC_PROLOGUE[1])
    a.sw("t1", 4, "t0")
    a.ld("ra", 0x00, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 0x20)  # delay slot
    return a.assemble()


def wander_segments(ram):
    """Player segment followed by the loaded accessory segments, in load order."""
    accessories = [u32(ram, var) for var in ACCESSORY_SEGMENT_VARS]
    for var in ACCESSORY_RECORD_VARS:
        record = u32(ram, var)
        accessories.append(u32(ram, record + 4) if record else 0)
    segments = [u32(ram, PLAYER_SEGMENT_VAR)]
    for segment in accessories:
        if segment and segment not in segments:
            segments.append(segment)
    return segments


def spawn_wander(p, index=0):
    """Return [(segment, original object, clone)] for every object of the Wander. Clone `index`
    (0..MAX_CLONES-1) publishes its pointers at MB_CLONES + index * CLONE_STRIDE."""
    slot = MAILBOX + MB_CLONES + CLONE_STRIDE * index
    if not hooks_advance(p, 0.5)[0]:
        raise SystemExit("logic hook is not running; install it / unpause the game first")
    ram = p.read_block(0, 32 << 20)
    groups = [(segment, entries_of(p, ram, segment)) for segment in wander_segments(ram)]
    originals = [obj for _s, items in groups for _e, _d, obj in items]
    if (p.read32(CORE_ALLOC), p.read32(CORE_ALLOC + 4)) != CORE_ALLOC_PROLOGUE:
        raise SystemExit("unexpected allocator prologue (different game version?)")
    for address, code in build_zero_alloc().items():
        p.write_block(address, code + b"\0" * (-len(code) % 8))
    p.write_block(slot, b"\0" * 4 * len(originals))
    code = build_routine(groups, slot)
    p.write_block(ROUTINE, code + b"\0" * (-len(code) % 8))
    call(p, ROUTINE)
    clones = struct.unpack("<%dI" % len(originals), p.read_block(slot, 8 * ((len(originals) + 1) // 2))[:4 * len(originals)])
    segments = [segment for segment, items in groups for _item in items]
    return list(zip(segments, originals, clones))


def main():
    import sys
    p = Pine()
    index = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    for segment, original, clone in spawn_wander(p, index):
        print("clone %d segment 0x%x: 0x%08x -> 0x%08x" % (index, segment, original, clone))
    logic, draw = hooks_advance(p)
    print("logic hook %s, draw hook %s" % ("running" if logic else "STOPPED",
                                          "running" if draw else "STOPPED"))


if __name__ == "__main__":
    main()
