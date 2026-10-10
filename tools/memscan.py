"""Differential EE RAM scanner over PINE.

  python3 memscan.py snap NAME            save RAM snapshot to work/scan/NAME.bin
  python3 memscan.py pos A B C            float triples that moved A->B and are still in B->C
  python3 memscan.py watch ADDR [N]       print N floats at ADDR every 0.25s
"""
import os
import struct
import sys
import time

import numpy as np

from pine import Pine

SCAN_DIR = os.path.join(os.path.dirname(__file__), "..", "work", "scan")
RAM_SIZE = 32 * 1024 * 1024
LO, HI = 0x00100000, 0x02000000


def load(name):
    return np.fromfile(os.path.join(SCAN_DIR, name + ".bin"), dtype="<f4")


def snap(name):
    os.makedirs(SCAN_DIR, exist_ok=True)
    data = Pine().read_block(0, RAM_SIZE)
    open(os.path.join(SCAN_DIR, name + ".bin"), "wb").write(data)
    print("saved", name)


def pos(a, b, c):
    fa, fb, fc = load(a), load(b), load(c)
    with np.errstate(invalid="ignore", over="ignore"):
        sane = lambda f: np.isfinite(f) & (np.abs(f) < 1e5) & ((np.abs(f) > 1e-3) | (f == 0))
        ok = sane(fa) & sane(fb) & sane(fc)
        moved = ok & (np.abs(fb - fa) > 0.05) & (fb == fc)
    idx = np.nonzero(moved)[0]
    idx = idx[(idx * 4 >= LO) & (idx * 4 < HI)]
    moved_set = set(idx.tolist())
    hits = []
    for i in idx:
        # x,y,z triple starting here with at least two moved components and the third stable/moved.
        if i - 1 in moved_set:
            continue
        group = [j for j in (i, i + 1, i + 2) if j in moved_set]
        if len(group) >= 2 and ok[i + 2] and fb[i + 2] == fc[i + 2]:
            dist = float(np.sqrt(sum((fb[j] - fa[j]) ** 2 for j in (i, i + 1, i + 2))))
            hits.append((i * 4, dist, fa[i:i + 3].tolist(), fb[i:i + 3].tolist()))
    print("%d moved floats, %d triples" % (len(idx), len(hits)))
    for addr, dist, va, vb in hits[:200]:
        print("0x%08x  d=%8.3f  %s -> %s" % (addr, dist,
              " ".join("%9.3f" % v for v in va), " ".join("%9.3f" % v for v in vb)))


def watch(addr, n=4):
    p = Pine()
    while True:
        print("0x%08x " % addr + " ".join("%10.3f" % p.readf(addr + 4 * k) for k in range(n)))
        time.sleep(0.25)


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "snap":
        snap(args[0])
    elif cmd == "pos":
        pos(*args)
    elif cmd == "watch":
        watch(int(args[0], 16), int(args[1]) if len(args) > 1 else 4)
