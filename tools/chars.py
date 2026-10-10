"""List live character objects (layout type 9, class 0x1206dd8) with kind and position."""
import struct

import numpy as np

from pine import Pine

CHAR_CLASS = 0x01206DD8
KIND_NAMES = {2: "player", 3: "horse?"}


def find_chars(p):
    ram = p.read_block(0, 32 * 1024 * 1024)
    b = np.frombuffer(ram, dtype=np.uint8)
    out = []
    for x in (np.nonzero(b[8::16] == 9)[0] * 16).tolist():
        if x < 0x1000000 or x + 0xA00 > len(ram):
            continue
        if struct.unpack_from("<I", ram, x + 4)[0] != CHAR_CLASS:
            continue
        kind = struct.unpack_from("<h", ram, x + 0x90 + 0x370)[0]
        pos = struct.unpack_from("<3f", ram, x + 0xA0)
        out.append((x, kind, pos))
    return out


if __name__ == "__main__":
    for x, kind, pos in find_chars(Pine()):
        print("0x%08x kind=%d %-7s pos=(%.2f, %.2f, %.2f)" % (x, kind, KIND_NAMES.get(kind, ""), *pos))
