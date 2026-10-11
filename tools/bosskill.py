"""Kill the active colossus and watch what the game does afterwards (single instance).

  python bosskill.py [STATE_SLOT] [SECONDS] [kill|life|script|absolute|damage]

Loads the state (a colossus fight), installs the hook and the probes, walks forward to wake the colossus,
kills it (kill: Boss.kill(), the game's own killing-blow sequence; life: life = 0 only (collapse and tendrils,
then nothing); script: the kill command 0x01370000 = life 0, dead flag, DEAD_BOSS script interrupt;
absolute: setBossAbsoluteDeath = AI work +0x48 = 1 (no visible effect); damage: every part's damage = its maximum
(no effect: life is separate)) and
logs once a second: game mode bits, demo/gameMode probe counters, stage load, the boss object, the dead-boss
record, HP and the player position; screenshots go to SNAPDIR (default work/kill).
"""
import os
import sys
import time

import pad
import probe
from boss import Boss
from hook import call, install as install_hook
from pine import Pine
from snap import AsyncSnap

SET_BOSS_ABSOLUTE_DEATH = 0x01378B80
KILL_BOSS = 0x01370000          # unnamed script command next to SCRBossDebugDeath
GET_DEAD_BOSS = 0x0134A398
GET_ACTIVE_BOSS = 0x0134A4C0
GAME_MODE = 0x01465D70
STAGE_LOAD_STATE = 0x01465E44
PLAYER_HP = 0x012DE264


def main():
    slot = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
    how = sys.argv[3] if len(sys.argv) > 3 else "kill"
    snapdir = os.environ.get("SNAPDIR", os.path.join(os.path.dirname(__file__), "..", "work", "kill"))
    os.makedirs(snapdir, exist_ok=True)
    p = Pine()
    p.load_state(slot)
    time.sleep(3.0)
    install_hook(p)
    if os.environ.get("PROBE_NAMES"):
        probe.PROBES.clear()
        probe.PROBES.update(probe.configure(os.environ["PROBE_NAMES"].split(",")))
    probe.install(p)
    pad.set_pad(p, stick=(0.0, 1.0))
    t0 = time.time()
    while time.time() - t0 < 12.0:
        time.sleep(0.5)
    pad.release(p)
    boss = Boss(p)
    print("boss 0x%08x, %d parts, damage %s" % (boss.obj, boss.count, [round(d, 1) for d in boss.read_damage()]))
    t0 = time.time()
    while p.read32(GAME_MODE) & 1 and time.time() - t0 < 60.0:
        time.sleep(0.5)         # killing during the intro cutscene leaves it waiting forever
    time.sleep(3.0)
    base = p.read32(call(p, 0x013CFB28, boss.obj))
    print("life %.1f / %.1f" % (p.readf(base + 0x44), p.readf(base + 0x40)))
    if how == "kill":
        boss.kill()
    elif how == "life":
        p.writef(base + 0x44, 0.0)
    elif how == "script":
        call(p, KILL_BOSS, boss.obj)
    elif how == "absolute":
        call(p, SET_BOSS_ABSOLUTE_DEATH, boss.obj)
    else:
        for i, mx in enumerate(boss.max):
            p.writef(boss.damage_addr(i), mx)
    print("kill (%s) at t=0" % how, flush=True)
    start = time.time()
    shot = None
    n = 0
    while time.time() - start < seconds:
        t = time.time() - start
        try:
            obj = call(p, 0x013439F0, timeout=1.0)
            dead = call(p, GET_DEAD_BOSS, timeout=1.0)
            active = call(p, GET_ACTIVE_BOSS, timeout=1.0)
        except TimeoutError:
            obj = dead = active = -1
        mode, load, hp = p.read32(GAME_MODE), p.read32(STAGE_LOAD_STATE), p.readf(PLAYER_HP)
        counts = [p.read32(0x000F8000 + off) for _fn, off in probe.PROBES.values()]
        life = p.readf(base + 0x44) if obj == boss.obj else -1.0
        print("t=%5.1f life %6.1f mode %#06x load %#x hp %5.1f boss obj %#x active %#x dead-rec %#x probes %s" % (
            t, life, mode, load, hp, obj & 0xFFFFFFFF, active & 0xFFFFFFFF, dead & 0xFFFFFFFF, counts), flush=True)
        if (shot is None or shot.poll()) and n < 30:
            shot = AsyncSnap(p, os.path.join(snapdir, "kill_%03d.png" % int(t)))
            n += 1
        time.sleep(1.0)


if __name__ == "__main__":
    main()
