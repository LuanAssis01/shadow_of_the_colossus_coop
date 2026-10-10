"""Drive player 1's controller through the logic hook (see hook.py MB_PAD_*).

  python3 pad.py stick X Y [SEC]     hold the left stick (-1..1; Y +1 = forward) for SEC seconds
  python3 pad.py press NAME [SEC]    press a button from BUTTONS (e.g. jump) for SEC seconds
  python3 pad.py off                 release the override

Buttons are the game's own 1P mask bits (the argument of GAMEUTIL_is_push_or_down_1p_button).
"""
import struct
import sys
import time

from hook import MAILBOX, MB_PAD_EN, MB_PAD_STICK, MB_PAD_WORDS
from pine import Pine


def stick_bytes(x, y):
    """Left stick to the pad's bytes: 0x7f centred, y inverted (0 = forward)."""
    to_byte = lambda v: max(0, min(255, int(round(127.5 + 127.5 * v))))
    return to_byte(x), to_byte(-y)


def set_pad(p, held=0, push=0, release=0, repeat=0, stick=(0.0, 0.0)):
    sx, sy = stick_bytes(*stick)
    # right stick and the rest of the word stay centred (0x7f) like an idle pad
    sticks = 0x7F7F0000 | (sy << 8) | sx
    p.write_block(MAILBOX + MB_PAD_WORDS, struct.pack("<5I", held, push, release, repeat, sticks))
    p.write32(MAILBOX + MB_PAD_EN, 1)


def release(p):
    p.write32(MAILBOX + MB_PAD_EN, 0)


# Game-internal 1P button bits, measured by pressing each one on a standing Wander (bitsweep, 2026-10-10).
# Names are what the bit DOES; the PS2 face-button names were not verified. Bits not listed did nothing
# on flat ground (probably grip / horse / camera, context dependent). PlayerControlButtonContext
# (0x014659a4) lists the action bit indices [15, 13, 4, 7, 5, 6, 3, 2, 1].
BUTTONS = {
    "jump": 0x80,        # motions 89 -> 91
    "bit6": 0x40,        # motion 453
    "bit5": 0x20,        # motions 511-513 (HUD item pose)
    "bit4": 0x10,        # motions 44, 46, 63, 57, 58 (screen blur)
    "menu_a": 0x08,      # opens the map/pause screen (motions 253-255); STAYS open after release
    "menu_b": 0x04,      # same map screen (motion 32)
}
PUSH_SECONDS = 0.06      # the push edge must last >= 3 game frames or the game can miss it


def press(p, mask, seconds=0.3, stick=(0.0, 0.0)):
    """Hold `mask` for `seconds` (push edge for the first PUSH_SECONDS), then release it."""
    t0 = time.time()
    while time.time() - t0 < seconds:
        set_pad(p, held=mask, push=mask if time.time() - t0 < PUSH_SECONDS else 0, stick=stick)
        time.sleep(0.02)
    set_pad(p, release=mask, stick=stick)
    time.sleep(PUSH_SECONDS)
    set_pad(p, stick=stick)


if __name__ == "__main__":
    pine = Pine()
    if sys.argv[1] == "stick":
        set_pad(pine, stick=(float(sys.argv[2]), float(sys.argv[3])))
        time.sleep(float(sys.argv[4]) if len(sys.argv) > 4 else 1.0)
        release(pine)
    elif sys.argv[1] == "press":
        press(pine, BUTTONS[sys.argv[2]], float(sys.argv[3]) if len(sys.argv) > 3 else 0.3)
        release(pine)
    else:
        release(pine)
