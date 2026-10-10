"""Helpers for SotC layout entries (0x4c-byte records whose +0x48 holds the spawned object)."""
import struct

import numpy as np

NULL_SENTINEL_PTR = 0x001E0ED4  # *(0x1e0ed4) is the engine's "null handle block"
GOBJ_DEF_TYPE = 0x01507738      # handle(entry+4)->+8 for spawnable game-object definitions


def u32(ram, a):
    return struct.unpack_from("<I", ram, a)[0]


def handle_block(ram, p):
    """Resolve a self-relative handle at p to its block base (0 if null)."""
    v = u32(ram, p)
    off = v & 0x3FFFF
    if off == 0 or off > p:
        return 0
    base = u32(ram, p - off)
    if base in (0, u32(ram, NULL_SENTINEL_PTR)) or not 0x100000 <= base < 0x2000000:
        return 0
    return base


def find_entries(ram, obj):
    """Layout entries whose spawned-object slot (+0x48) is obj."""
    u = np.frombuffer(ram, dtype="<u4")
    out = []
    for a in (np.nonzero(u == obj)[0] * 4).tolist():
        e = a - 0x48
        if e < 0x100000:
            continue
        blk = handle_block(ram, e + 4)
        if blk and u32(ram, blk + 8) == GOBJ_DEF_TYPE:
            out.append(e)
    return out


def resolve(ram, p):
    """Resolve a self-relative handle at p to the element it indexes (0 if null)."""
    blk = handle_block(ram, p)
    if not blk:
        return 0
    v = u32(ram, p)
    return u32(ram, blk + 0x10) + ((v >> 18) & 0xFFF) * u32(ram, u32(ram, blk + 8) + 0xC)


def segment_entries(ram, stage_data):
    """Layout entries (0x4c bytes each) instantiated by initstage for a stage-data record."""
    layout_list = resolve(ram, stage_data + 4)
    blk = handle_block(ram, layout_list)
    if not blk:
        return []
    count = u32(ram, blk + 0xC)
    base = resolve(ram, layout_list)
    return [base + 0x4C * i for i in range(count)]
