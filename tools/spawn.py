"""Spawn a second, complete Wander for the remote player.

  python3 spawn.py [K]         clone the player segment and every accessory segment (clone K = 0..3)
  python3 spawn.py horse [K]   clone the player's horse (segment 0xba: horse + helper object)

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

import guard
from guard import CLONE_DELETED, DEFINITION_OFFSET, ORIGINAL_OFFSET
from hook import MAILBOX, call, counters
from layout import handle_block, resolve, segment_entries, u32
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
MB_HORSES = 0x120              # horse clone k: pointers at MB_HORSES + k * HORSE_STRIDE (horse, then its helper object)
HORSE_STRIDE = 0x10
HORSE_SEGMENT_VAR = 0x01465E14  # segment id of the loaded horse (gStatLoadHorseChara)
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
MB_ALLOC_FAILS = 0x188         # mailbox: allocations that failed during a spawn, then the last size


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
    w.sd("s2", 0x18, "sp")
    w.move("s2", "a0")                     # the partition (heap) this request is for
    w.jal(ZERO_ALLOC_TRAMPOLINE)
    w.move("s1", "a1")  # delay slot: keep the requested size
    w.bne("v0", "zero", "ok")
    w.move("s0", "v0")  # delay slot
    w.li("t0", MAILBOX + MB_ALLOC_FAILS)   # out of memory while spawning: count it for diagnostics
    w.lw("t1", 0, "t0")
    w.addiu("t1", "t1", 1)
    w.sw("t1", 0, "t0")
    w.sw("s1", 4, "t0")                    # size of the last failed request
    w.sw("s2", 8, "t0")                    # and its partition
    w.beq("zero", "zero", "out")
    w.nop()
    w.label("ok")
    w.move("a0", "v0")
    w.move("a1", "zero")
    w.jal(MEMSET)
    w.move("a2", "s1")  # delay slot
    w.label("out")
    w.move("v0", "s0")
    w.ld("ra", 0x00, "sp")
    w.ld("s0", 0x08, "sp")
    w.ld("s1", 0x10, "sp")
    w.ld("s2", 0x18, "sp")
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
    """[(entry, definition, original object, original instance cache, dependency definition, gobj)] for
    every spawned entry of segment. The cache (definition+8, set by loadObj) holds the definition's spawned
    instance; children find their parent through it, and loadObj refuses to instantiate a definition whose
    cache is set. The dependency (entry+0xc, 0 if none) is the definition whose instance must exist before
    the entry can spawn; gobj (definition+0x10, 0 if none) carries the engine's display gate (bit 0 of +0x10,
    gStatGObjDispChk)."""
    out = []
    for entry in segment_entries(ram, call(p, GC_GET_STAGE_DATA, segment)):
        obj = u32(ram, entry + 0x48)
        definition = u32(ram, handle_block(ram, entry + 4) + 0x10)
        cache = u32(ram, definition + 8)
        if not obj or cache not in (0, obj):
            raise SpawnRefused("segment 0x%x entry 0x%08x is not in the expected spawned state" % (segment, entry))
        dep_block = handle_block(ram, entry + 0xC)
        dependency = u32(ram, dep_block + 0x10) if dep_block else 0
        out.append((entry, definition, obj, cache, dependency, resolve(ram, definition + 0x10)))
    return out


class SpawnRefused(Exception):
    """The engine would not instantiate an entry right now (e.g. the horse is not displayable right after
    a retry); nothing was left behind, try again later."""


def build_routine(groups, mailbox_slot=MAILBOX + MB_CLONES):
    """groups: [(segment, [(entry, definition, obj, cache, dependency, gobj)])] spawned in order; clone
    pointers go to mailbox_slot. Returns (in v0) 0, or k + 1 when entry k could not be spawned.

    A whole segment is spawned by one initstage call: its first pass (FUN_013600a8 for every entry) clears
    every entry's +0x48 slot and instantiates the entries without dependency, the second pass loops until
    the dependent ones have spawned. If the engine refuses an entry that another one depends on, that loop
    never ends (seen: the game froze inside the spawn routine). So before touching anything the routine
    checks, for every entry, what FUN_013600a8 checks (entry word 0 set, display gate bit) and that every
    dependency outside the segment has an instance; and after initstage that every entry spawned."""
    a = Asm(ROUTINE)
    a.addiu("sp", "sp", -0x20)
    a.sd("ra", 0x00, "sp")
    a.sd("s0", 0x08, "sp")
    a.move("s0", "zero")
    items = [(segment,) + item for segment, seg_items in groups for item in seg_items]
    spawned_here = {item[1] for _segment, seg_items in groups for item in seg_items}
    for k, (segment, entry, _definition, _obj, _cache, dependency, gobj) in enumerate(items):
        a.li("t0", entry)
        a.lw("t1", 0, "t0")
        a.beq("t1", "zero", "fail%d" % k)
        a.nop()
        if gobj:
            a.li("t0", gobj)
            a.lw("t1", 0x10, "t0")
            a.andi("t1", "t1", 1)
            a.beq("t1", "zero", "fail%d" % k)
            a.nop()
        if dependency and dependency not in spawned_here:   # instantiated by an earlier segment of this spawn otherwise
            a.li("t0", dependency)
            a.lw("t1", 8, "t0")
            a.beq("t1", "zero", "fail%d" % k)
            a.nop()
    # detour the core allocator: j ZERO_ALLOC ; nop
    a.li("t0", CORE_ALLOC)
    a.li("t1", 0x08000000 | ((ZERO_ALLOC >> 2) & 0x3FFFFFF))
    a.sw("t1", 0, "t0")
    a.sw("zero", 4, "t0")
    k = 0
    for segment, seg_items in groups:
        for entry, definition, _obj, _cache, _dependency, _gobj in seg_items:
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
        for entry, definition, *_rest in seg_items:
            a.li("t0", entry)
            a.lw("t1", 0x48, "t0")
            a.li("t2", mailbox_slot)
            a.sw("t1", 4 * k, "t2")
            a.li("t3", definition)            # guard.py restores the definition's real instance on delete
            a.sw("t3", DEFINITION_OFFSET + 4 * k, "t2")
            a.bne("t1", "zero", "claim%d" % k)
            a.nop()
            a.beq("zero", "zero", "next%d" % k)
            a.addiu("s0", "zero", k + 1)  # delay slot: remember the failure, keep claiming the others
            a.label("claim%d" % k)
            # ownership marker (guard.py): keep the class's delete-request handler, install ours
            a.lw("t3", 0x18, "t1")
            a.sw("t3", ORIGINAL_OFFSET + 4 * k, "t2")
            a.li("t3", CLONE_DELETED)
            a.sw("t3", 0x18, "t1")
            a.label("next%d" % k)
            k += 1
        # the next segments depend on this one's instances: stop at the first failure
        a.bne("s0", "zero", "restore")
        a.nop()
    a.label("restore")
    for _segment, entry, definition, obj, cache, _dependency, _gobj in items:
        a.li("t0", entry)
        a.li("t1", obj)
        a.sw("t1", 0x48, "t0")
        a.li("t0", definition)
        a.li("t1", cache)
        a.sw("t1", 8, "t0")
    # restore the allocator prologue
    a.li("t0", CORE_ALLOC)
    a.li("t1", CORE_ALLOC_PROLOGUE[0])
    a.sw("t1", 0, "t0")
    a.li("t1", CORE_ALLOC_PROLOGUE[1])
    a.sw("t1", 4, "t0")
    a.label("out")
    a.move("v0", "s0")
    a.ld("ra", 0x00, "sp")
    a.ld("s0", 0x08, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 0x20)  # delay slot
    for k in range(len(items)):   # refused before anything was touched: nothing to restore
        a.label("fail%d" % k)
        a.beq("zero", "zero", "out")
        a.addiu("s0", "zero", k + 1)  # delay slot
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
    return spawn_segments(p, MAILBOX + MB_CLONES + CLONE_STRIDE * index, wander_segments)


def spawn_horse(p, index=0):
    """Clone the player's horse (segment in HORSE_SEGMENT_VAR: the horse character and its helper object).
    Pointers go to MB_HORSES + index * HORSE_STRIDE."""
    return spawn_segments(p, MAILBOX + MB_HORSES + HORSE_STRIDE * index,
                          lambda ram: [u32(ram, HORSE_SEGMENT_VAR)], "horse")


IOS_MALLOC_NO_CHECK = 0x001B00F0   # iosMallocAlignNoCheck(heap, size, align): NULL when it does not fit, no recovery path
IOS_FREE = 0x001B0328
MAIN_HEAP = 0x001E0C88
HEAP_FREE = 0x001E0BF0             # bytes free in the main heap
# What a Wander / a horse clone need: their biggest single request seen was 0x12d14 (77 KB), total ~60 KB / ~45 KB.
SPAWN_COSTS = {"wander": (0x14000, 0x30000), "horse": (0x14000, 0x24000)}


def heap_fits(p, kind):
    """True when the main heap can take a clone of `kind`: enough free bytes and a contiguous block for its
    biggest request. The engine has no out-of-memory path for these allocations (a failed one froze the
    game in initlayout's dependency loop or crashed it in initParticleObject), so never start a spawn that
    could fail."""
    block, total = SPAWN_COSTS[kind]
    if p.read32(HEAP_FREE) < total + 0x10000:
        return False
    probe = call(p, IOS_MALLOC_NO_CHECK, MAIN_HEAP, block, 16)
    if not probe:
        return False
    call(p, IOS_FREE, probe)
    return True


def spawn_segments(p, slot, pick_segments, kind="wander"):
    if not hooks_advance(p, 0.5)[0]:
        raise SystemExit("logic hook is not running; install it / unpause the game first")
    if not heap_fits(p, kind):
        raise SpawnRefused("not enough game heap for a %s clone (%d bytes free)" % (kind, p.read32(HEAP_FREE)))
    if not guard.installed(p):
        guard.install(p)        # the clones' delete handler lives there; never spawn without it
    ram = p.read_block(0, 32 << 20)
    groups = [(segment, entries_of(p, ram, segment)) for segment in pick_segments(ram)]
    originals = [item[2] for _s, items in groups for item in items]
    if (p.read32(CORE_ALLOC), p.read32(CORE_ALLOC + 4)) != CORE_ALLOC_PROLOGUE:
        raise SystemExit("unexpected allocator prologue (different game version?)")
    for address, code in build_zero_alloc().items():
        p.write_block(address, code + b"\0" * (-len(code) % 8))
    p.write_block(slot, b"\0" * 4 * len(originals))
    code = build_routine(groups, slot)
    assert ROUTINE + len(code) <= ZERO_ALLOC, "spawn routine overflows into the allocator wrapper"
    p.write_block(ROUTINE, code + b"\0" * (-len(code) % 8))
    failed = call(p, ROUTINE)
    if failed:
        despawn_clones(p)   # the entries spawned before the refusal carry our marker
        entry = [item[0] for _s, items in groups for item in items][failed - 1]
        raise SpawnRefused("the engine refused to spawn entry 0x%08x right now (%s)" % (entry, diagnose(p, groups, failed - 1)))
    clones = struct.unpack("<%dI" % len(originals), p.read_block(slot, 8 * ((len(originals) + 1) // 2))[:4 * len(originals)])
    segments = [segment for segment, items in groups for _item in items]
    return list(zip(segments, originals, clones))


def diagnose(p, groups, k):
    """Why entry k was refused: the checks the routine makes, evaluated now."""
    item = [(seg,) + it for seg, items in groups for it in items][k]
    seg, entry, definition, _obj, _cache, dependency, gobj = item
    word0 = p.read32(entry)
    gate = p.read32(gobj + 0x10) if gobj else None
    dep = p.read32(dependency + 8) if dependency else None
    return "segment 0x%x entry word0=%#x gobj %s gate=%s dependency instance=%s allocation failures=%d" % (
        seg, word0, hex(gobj), None if gate is None else hex(gate), None if dep is None else hex(dep),
        p.read32(MAILBOX + MB_ALLOC_FAILS))


LAYOUT_TYPES = (9, 0x0A, 0x0B, 0x0D, 0x16)   # layout object +0x08 type byte (character, script, alias...)


def repair_definitions(p):
    """Give the real Wander's / horse's layout definitions their instance back where a clone deletion cleared it.

    Deleting a clone clears its definition's instance cache (definition+8), which the real object shares; guard.py
    restores it for clones spawned by this version, but clones carried in from an older savestate have no recorded
    definition. With the cache at 0 the accessories loaded after a colossus dies (segment 0xC3 depends on the
    player definition) wait forever in initlayout. Returns the number of caches repaired."""
    from heapgrow import in_heap
    ram = p.read_block(0, 32 << 20)
    segments = wander_segments(ram) + [u32(ram, HORSE_SEGMENT_VAR)]
    clones = {u32(ram, MAILBOX + off) for start, end in guard.POINTER_RANGES for off in range(start, end, 4)}
    fixed = 0
    for segment in segments:
        if not segment:
            continue
        for entry in segment_entries(ram, call(p, GC_GET_STAGE_DATA, segment)):
            obj = u32(ram, entry + 0x48)
            definition = u32(ram, handle_block(ram, entry + 4) + 0x10)
            if not definition or u32(ram, definition + 8) or not obj or obj in clones or not in_heap(obj):
                continue
            if ram[obj + 8] not in LAYOUT_TYPES or p.read32(obj + 0x18) == CLONE_DELETED:
                continue
            p.write32(definition + 8, obj)
            fixed += 1
    return fixed


def despawn_clones(p):
    """Delete every clone (Wanders and horses) through the EE routine the stage guard uses: it only
    deletes objects that still carry our delete handler, so a stale pointer is never deleted."""
    if not guard.installed(p):
        # without the guard nothing was ever marked as ours: only forget the pointers
        for start, end in guard.POINTER_RANGES:
            p.write_block(MAILBOX + start, bytes(end - start))
        return
    guard.delete_all(p)
    repair_definitions(p)


def main():
    import sys
    p = Pine()
    if len(sys.argv) > 1 and sys.argv[1] == "despawn":
        despawn_clones(p)
        print("clones deleted; guard counters (unloads, deleted objects):", guard.counters(p))
        return
    horse = len(sys.argv) > 1 and sys.argv[1] == "horse"
    index = int(sys.argv[-1]) if len(sys.argv) > 1 and sys.argv[-1].isdigit() else 0
    for segment, original, clone in (spawn_horse if horse else spawn_wander)(p, index):
        print("%s clone %d segment 0x%x: 0x%08x -> 0x%08x" % ("horse" if horse else "wander", index, segment, original, clone))
    logic, draw = hooks_advance(p)
    print("logic hook %s, draw hook %s" % ("running" if logic else "STOPPED",
                                          "running" if draw else "STOPPED"))


if __name__ == "__main__":
    main()
