"""See the game without a screen grab: save a state through PINE and pull the
frame PCSX2 embeds in every savestate (Screenshot.png), optionally the EE RAM.

  python3 snap.py OUT.png [--ram OUT.bin] [--slot N]

The scratch slot (default 9) is overwritten; slots 1-7 hold the project's states.
GNOME on Wayland blocks screenshot tools, so this is how tests are checked.
"""
import argparse
import os
import struct
import time
import zipfile

from pine import SAVE_STATE, Pine

SSTATES = os.environ.get("SSTATES") or os.path.expanduser("~/.var/app/net.pcsx2.PCSX2/config/PCSX2/sstates")
SERIAL = "SCUS-97472 (C19A374E)"
SCRATCH_SLOT = 9


def state_path(slot):
    return os.path.join(SSTATES, "%s.%02d.p2s" % (SERIAL, slot))


def snap(p, png, ram=None, slot=SCRATCH_SLOT, timeout=20.0):
    path = state_path(slot)
    before = os.path.getmtime(path) if os.path.exists(path) else 0
    p._call(struct.pack("<BB", SAVE_STATE, slot))
    end = time.time() + timeout
    while True:
        # PCSX2 compresses and writes the state on a worker thread; wait for a
        # newer, complete archive.
        if os.path.exists(path) and os.path.getmtime(path) > before:
            try:
                with zipfile.ZipFile(path) as z:
                    with open(png, "wb") as f:
                        f.write(z.read("Screenshot.png"))
                    if ram:
                        with open(ram, "wb") as f:
                            f.write(z.read("eeMemory.bin"))
                return png
            except (zipfile.BadZipFile, KeyError, OSError):
                pass
        if time.time() > end:
            raise TimeoutError("savestate slot %d was not written" % slot)
        time.sleep(0.3)


class AsyncSnap:
    """Non-blocking screenshots for loops that must keep ticking: start() asks PCSX2 to save the
    scratch state, poll() returns True once Screenshot.png has been written to `png`."""

    def __init__(self, p, png, slot=SCRATCH_SLOT):
        self.p, self.png, self.slot = p, png, slot
        path = state_path(slot)
        self.before = os.path.getmtime(path) if os.path.exists(path) else 0
        p._call(struct.pack("<BB", SAVE_STATE, slot))
        self.deadline = time.time() + 20.0

    def poll(self):
        path = state_path(self.slot)
        if os.path.exists(path) and os.path.getmtime(path) > self.before:
            try:
                with zipfile.ZipFile(path) as z:
                    with open(self.png, "wb") as f:
                        f.write(z.read("Screenshot.png"))
                return True
            except (zipfile.BadZipFile, KeyError, OSError):
                pass
        if time.time() > self.deadline:
            raise TimeoutError("savestate slot %d was not written" % self.slot)
        return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("png")
    ap.add_argument("--ram")
    ap.add_argument("--slot", type=int, default=SCRATCH_SLOT)
    args = ap.parse_args()
    print(snap(Pine(), args.png, args.ram, args.slot))
