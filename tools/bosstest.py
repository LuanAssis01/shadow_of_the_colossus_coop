"""Single-instance test of the colossus pose mirror: record the colossus for SEC seconds from a savestate,
reload the same state and replay the recording through bosssync while the local AI keeps running; report how
well the shown pose follows the recording.   python bosstest.py [STATE_SLOT] [SEC]"""
import sys
import time

from hook import install as install_hook
from pine import Pine
import bosssync

slot = int(sys.argv[1]) if len(sys.argv) > 1 else 2
DX = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0     # offset applied to the replayed position
DZ = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
sec = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
p = Pine()
link = bosssync.BossLink(p)


def fresh():
    p.load_state(slot)
    time.sleep(3.0)
    install_hook(p)
    link.boss = None
    link.next_scan = 0.0
    for _ in range(10):
        if link.refresh():
            return
        time.sleep(0.5)
    raise SystemExit("no colossus in this state")


import pad


def wake():
    pad.set_pad(p, stick=(0.0, 1.0))
    t = time.time()
    while time.time() - t < 12.0:
        time.sleep(0.5)
    pad.release(p)


fresh()
wake()
rec = []
t0 = time.time()
while time.time() - t0 < sec:
    s = link.sample()
    rec.append((time.time() - t0, s))
    time.sleep(1 / 30)
print("recorded %d samples; damage at the end %s" % (len(rec), [round(d, 1) for d in rec[-1][1][4]][:6]))
moves = {s[0] for _t, s in rec if s}
print("distinct motions", len(moves), "first/last pos", [round(v, 1) for v in rec[0][1][2]], [round(v, 1) for v in rec[-1][1][2]])

fresh()
wake()
link.install()
start = time.time()
i = 0
err_pos, err_motion, n = [], 0, 0
while True:
    now = time.time() - start
    if now > sec:
        break
    while i + 1 < len(rec) and rec[i + 1][0] <= now:
        i += 1
    mid, frame, pos, quat, _dmg, _awake = rec[i][1]
    pos = (pos[0] + DX, pos[1], pos[2] + DZ)
    link.apply_pose(mid, frame, pos, quat)
    time.sleep(1 / 30)
    cur = link.sample()
    if cur:
        n += 1
        err_pos.append(((cur[2][0] - pos[0]) ** 2 + (cur[2][2] - pos[2]) ** 2) ** 0.5)
        err_motion += cur[0] != mid
link.uninstall()
err_pos.sort()
print("replay: %d ticks, position error mean %.2f p95 %.2f max %.2f, motion differs in %d%% of ticks" % (
    n, sum(err_pos) / n, err_pos[int(n * .95)], err_pos[-1], 100 * err_motion // n))
