"""Show which emulator reacts to which physical controller (nothing is injected).

  python3 padwatch.py [SECONDS]     watch the engine's player-1 pad state of instance A (28011) and B (28012)

Prints a line whenever the held-button word or the left stick of an instance changes. Needs the hook
only for the layout; reads PAD_STATE directly. Do not run together with netbridge (one PINE client).
"""
import os
import sys
import time

import pine
from hook import PAD_STATE, PAD_STICK_OFFSET


def connect(slot):
    path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid()),
                        ".flatpak/net.pcsx2.PCSX2/xdg-run/pcsx2.sock" + ("." + slot if slot != "28011" else ""))
    return pine.Pine(path)


if __name__ == "__main__":
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    emus = {"A": connect("28011"), "B": connect("28012")}
    last = {}
    end = time.time() + seconds
    while time.time() < end:
        for name, p in emus.items():
            held, stick = p.read32_many([PAD_STATE, PAD_STATE + PAD_STICK_OFFSET])
            lx, ly = stick & 0xFF, (stick >> 8) & 0xFF
            # the stick rests near 0x7f/0x80; report only real deflection
            deflected = abs(lx - 0x7F) > 24 or abs(ly - 0x7F) > 24
            key = (held, deflected)
            if last.get(name) != key:
                print("%s: held=0x%04x stick=(%3d,%3d)%s" % (name, held, lx, ly, " <-- moved" if deflected else ""), flush=True)
                last[name] = key
        time.sleep(0.02)
    print("done")
