"""Give the game's main heap more memory: hand unused EE RAM to the allocator as free blocks.

  python heapgrow.py            donate the regions (idempotent) and print the heap's free space
  python heapgrow.py status     only print

Why: the main heap (partition 0x001E0C88) covers 0x00290FFC..0x01FE0000 and a colossus fight uses nearly all of
it; with a Wander clone (~250 KB) and a horse clone (~180 KB) the fight's camera allocation failed ("memory over
ptn 0x1e0c88 size 0x65c", iosJumpRecoverPoint, the game froze) with 44 KB left in 413 fragments.

Block header (32 bytes before the data, 24-bit word indexes = address >> 2):
  +0x00 low 24: size in words, header excluded (next block in memory = header + 0x20 + size*4)
  +0x03/+0x07/+0x0B bytes: the owning partition (word index, low/mid/high byte)
  +0x04 low 24: requested size in words           +0x08 low 24: next block in memory (0 = none)
  +0x0C low 24: previous block in memory (0 = none), bit 24 = in use
  +0x14 low 24: previous free block                +0x1C: next free block (address)
and the allocation returned to the caller has the header address at ptr - 4.
A donated region becomes one "in use" block with no neighbours in memory, released with the game's own iosFree
(0x001B0328): the allocator files it in its size class and adds it to the free counter (0x001E0BF0, words).
Blocks split from it point to each other, so the region stays an island the allocator manages like the rest.

Only RAM that is outside the heap and zero in every snapshot (title, shrine, field, colossus fight) is used:
  0x00098000..0x000D8000  between the boot ELF's loader area and our stacks (guard stack top 0x000E8000,
                          hook stack top 0x000EFFF0, stubs from 0x000F0000)
The EE kernel area also has a zero gap (0x00025000..0x00075000, 320 KB), but PINE writes below 0x00080000 are
dropped, so it cannot be prepared from here (and freeing an unwritten header corrupts the heap: never do it).
"""
import struct
import sys

from hook import call
from pine import Pine

ROOT_PARTITION = 0x001E0C88
FREE_WORDS = 0x001E0BF0
IOS_FREE = 0x001B0328
HEADER = 0x20
REGIONS = [(0x00098000, 0x000D8000)]
M24 = 0xFFFFFF
HEAP_START, HEAP_END = 0x00290000, 0x02000000    # the main heap's own range (blocks from 0x00290FFC)


def in_heap(addr):
    """True for an address the game heap can hand out: its own range or a donated region. Every 'is this a live
    object pointer' check must use it: after a donation clones (and even the real Wander after a reload) can
    live below 1 MB, and treating them as invalid made the bridge respawn the horse clone until the heap ran out."""
    return HEAP_START <= addr < HEAP_END or any(s <= addr < e for s, e in REGIONS)


def header(start, end):
    """32-byte header + back pointer making [start, end) one in-use block of the root partition."""
    words = (end - start - HEADER) >> 2
    ptn = ROOT_PARTITION >> 2
    w0 = (words & M24) | ((ptn & 0xFF) << 24)
    w1 = (words & M24) | (((ptn >> 8) & 0xFF) << 24)
    w2 = 0 | (((ptn >> 16) & 0xFF) << 24)          # no next block in memory
    w3 = 0 | (1 << 24)                              # no previous block, in use
    return struct.pack("<8I", w0, w1, w2, w3, 0, 0, 0, 0) + struct.pack("<I", start)


def donated(p, start):
    """True when the region already belongs to the heap (its header names the root partition)."""
    raw = p.read_block(start, 16)
    ptn = raw[3] | raw[7] << 8 | raw[11] << 16
    return ptn == ROOT_PARTITION >> 2


def region_clean(p, start, end):
    data = p.read_block(start, end - start)
    return data.count(0) == len(data)


def grow(p, quiet=False):
    """Donate every region not donated yet; returns the number of bytes added."""
    added = 0
    for start, end in REGIONS:
        if donated(p, start):
            continue
        if not region_clean(p, start, end):
            if not quiet:
                print("region %08x-%08x is not empty: left alone" % (start, end))
            continue
        before = p.read32(FREE_WORDS)
        p.write_block(start, header(start, end) + b"\0" * 4)   # header + back pointer (data at +0x24)
        call(p, IOS_FREE, start + HEADER + 4)
        added += (p.read32(FREE_WORDS) - before) * 4
        if not quiet:
            print("donated %08x-%08x: heap free %d -> %d words" % (start, end, before, p.read32(FREE_WORDS)))
    return added


def status(p):
    print("heap free counter %d words (%d KB); regions %s" % (
        p.read32(FREE_WORDS), p.read32(FREE_WORDS) * 4 // 1024,
        ", ".join("%08x:%s" % (s, "donated" if donated(p, s) else "no") for s, _e in REGIONS)))


if __name__ == "__main__":
    pine = Pine()
    if len(sys.argv) < 2 or sys.argv[1] != "status":
        grow(pine)
    status(pine)
