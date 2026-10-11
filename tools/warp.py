"""Move the Wander (and the horse) next to a colossus, the way the game places the horse when a fight starts.

  python3 warp.py list          the 16 colossus records (area, arrival point, max HP/grip)
  python3 warp.py boss N        put the player and the horse at colossus N's arrival point (0 = first)
  python3 warp.py fight N       make colossus N the active one (bossStatSetActiveBoss), then warp there: the
                                colossus is in the arena when the area streams in

Every colossus has a record in the boss table (PTR_s_DEFAULT_0156b440 -> +0x10, 0xec bytes each): +0x50
flags, +0x51 seamless area id, +0x64 arrival point relative to the area centre (x and z are stored
mirrored, see bossStatStartDemo / bossStatSekibanJumpSetHorsePos), +0x70 heading in degrees, +0xbc max
HP and +0xc0 max grip the game gives the player for that fight. The world position is
YattsukeGetSeamlessAreaCenter(area) + (-x, y, -z). The seamless stage streams the area in around the
player after the jump; give it a few seconds.
"""
import math
import struct
import sys
import time

from hook import MAILBOX, call, teleport
from pine import Pine

BOSS_TABLE = 0x0156B440
BOSS_RECORD_SIZE = 0xEC
BOSS_COUNT = 16
AREA_CENTER = 0x013D0B08        # YattsukeGetSeamlessAreaCenter(out vec4, area id)
SCRATCH = MAILBOX + 0x600       # 16 bytes for the returned vector


def record(p, n):
    return p.read32(BOSS_TABLE + 0x10) + n * BOSS_RECORD_SIZE


def arrival(p, n):
    """(world position, heading degrees, area, max hp, max grip) of colossus n's arrival point."""
    r = record(p, n)
    raw = p.read_block(r + 0x50, 0x78)
    area = raw[1]
    rx, ry, rz, heading = struct.unpack_from("<4f", raw, 0x14)
    max_hp, max_grip = struct.unpack_from("<2f", raw, 0x6C)
    call(p, AREA_CENTER, SCRATCH, area)
    cx, cy, cz = struct.unpack("<3f", p.read_block(SCRATCH, 16)[:12])
    return (cx - rx, cy + ry, cz - rz), heading, area, max_hp, max_grip


SET_ACTIVE_BOSS = 0x01349518   # bossStatSetActiveBoss(record): enables only that colossus' layout (game flags),
                               # sets the active index (_gameWork 8), the clear count and its "alive" flag


def activate(p, n):
    """Make colossus n the one the world spawns (also what the game does when the sword points at the next one).
    Note: setBossClearCnt(n) is part of it, so the progress counter follows."""
    call(p, SET_ACTIVE_BOSS, record(p, n))


def heading_quat(degrees):
    half = math.radians(degrees) / 2.0
    return (0.0, math.sin(half), 0.0, math.cos(half))


def warp_to_boss(p, n, side=3.0, hold=20.0, lift=25.0):
    """Teleport the player to colossus n's arrival point and the horse next to it. The terrain around a far
    point streams in over a few seconds, so the player is held `lift` metres above the arrival point (re-teleported
    every 0.2 s, which also zeroes its fall) for `hold` seconds, then dropped."""
    from netbridge import local_horse, local_player
    pos, heading, _area, _hp, _grip = arrival(p, n)
    player = local_player(p)
    horse = local_horse(p)
    quat = heading_quat(heading)
    end = time.time() + hold
    while time.time() < end:
        teleport(p, player, (pos[0], pos[1] + lift, pos[2]), quat)
        if horse:
            teleport(p, horse, (pos[0] + side, pos[1] + lift, pos[2]), quat)
        time.sleep(0.2)
    teleport(p, player, (pos[0], pos[1] + 1.0, pos[2]), quat)
    return pos


if __name__ == "__main__":
    pine = Pine()
    if len(sys.argv) < 2 or sys.argv[1] == "list":
        for i in range(BOSS_COUNT):
            pos, heading, area, hp, grip = arrival(pine, i)
            print("%2d area 0x%02x arrival (%.1f, %.1f, %.1f) heading %.0f  max hp %.0f grip %.0f" % (
                i, area, pos[0], pos[1], pos[2], heading, hp, grip))
    elif sys.argv[1] == "boss":
        print("warped to", warp_to_boss(pine, int(sys.argv[2])))
    elif sys.argv[1] == "fight":
        activate(pine, int(sys.argv[2]))
        print("colossus %s active; warped to" % sys.argv[2], warp_to_boss(pine, int(sys.argv[2])))
