"""UDP bridge: send the local Wander's state, drive the clone puppet from the remote's.

  python3 netbridge.py echo [DX DZ]        local test: the puppet mirrors the local
                                           Wander, offset (DX, DZ) world units
  python3 netbridge.py record FILE [SEC]   save the local Wander's samples (30 Hz) to FILE
  python3 netbridge.py replay FILE [DX DZ] drive the puppet from FILE, print jitter metrics
  python3 netbridge.py sendfile FILE HOST PORT [LOSS% [JITTER_MS]]
                                           replay FILE over UDP as a fake remote player
                                           (no emulator; env OFFSET="dx,dz")
  python3 netbridge.py send HOST PORT      only send the local state
  python3 netbridge.py recv PORT           only drive the puppet (env DURATION=SEC: stop and
                                           print tracking stats)
  python3 netbridge.py host PORT           wait for peers (any number, up to the spawned clones),
                                           exchange state and relay each peer to the others
  python3 netbridge.py join HOST PORT      connect to a host and exchange state

Needs the hook, a spawned clone and its puppet script (hook.py install, spawn.py,
puppet.py). A state packet is 44 bytes, 30 per second:
  seq u32 | motion u32 | frame f32 | pos 3f | quat 4f | pid u32
`pid` identifies the player the state belongs to (0 = the host, k = the k-th client); clients leave
it 0 and the host stamps it when relaying, so every machine can keep one clone per remote player.
`motion` is the index of the current motion in the character's motion table
(class+0x2ac, 0xdc bytes per entry), which is stable across machines, unlike heap
addresses; 0xFFFFFFFF means "not in the table" and leaves the puppet's motion alone.
"""
import json
import os
import random
import socket
import struct
import sys
import time

from hook import MAILBOX
from pine import Pine
from pad import release as pad_release
from pad import set_pad
from puppet import MB_CLONES, clone_address, make_puppet, write_state

PLAYER_OBJ_HANDLE_GROUP = 0x01465940   # group whose first object is the player
RATE = 30.0
PACKET = struct.Struct("<IIf3f4fI")
MOTION_STRIDE = 0xDC
MOTION_TABLE_MAX = 4096
CLASS = 0x90


def local_player(p):
    """Player object: the only kind-2 character with a layout entry (see chars.py)."""
    from chars import find_chars
    clones = {clone_address(p, i) for i in range(4)}
    return next(x for x, kind, _pos in find_chars(p) if kind == 2 and x not in clones)


def read_local(p, player):
    """(motion index, frame, pos, quat) of the local Wander in one PINE round trip."""
    cl = player + CLASS
    quat_ptr = p.read32(cl + 0x13C)
    addrs = [cl + 0x1C0, cl + 0x2AC, cl + 0x1C8, player + 0xA0, player + 0xA4, player + 0xA8] + \
            [quat_ptr + 4 * i for i in range(4)]
    motion, table, frame, x, y, z, qx, qy, qz, qw = p.read32_many(addrs)
    f = lambda v: struct.unpack("<f", struct.pack("<I", v))[0]
    index = (motion - table) // MOTION_STRIDE if motion >= table and (motion - table) % MOTION_STRIDE == 0 else 0xFFFFFFFF
    if index != 0xFFFFFFFF and index >= MOTION_TABLE_MAX:
        index = 0xFFFFFFFF
    return index, f(frame), (f(x), f(y), f(z)), (f(qx), f(qy), f(qz), f(qw))


def pack(seq, index, frame, pos, quat):
    return PACKET.pack(seq, index, frame, *pos, *quat, 0)


TELEPORT_DISTANCE = float(__import__("os").environ.get("TELEPORT_DISTANCE", "5.0"))
TELEPORT_HEIGHT = float(__import__("os").environ.get("TELEPORT_HEIGHT", "8.0"))
# A full teleport (ResetCharacterObj) restarts motion, velocity and cloth, so it is only used for the
# first sample and for discontinuities; every normal correction is a light "move" (see puppet.FLAG_MOVE).
MOVE_DISTANCE = float(__import__("os").environ.get("MOVE_DISTANCE", "0.35"))   # horizontal error that triggers a move
# 0.6 was the best measured trade-off: a move also resets the clone's fall speed, so a tight limit fights the
# engine's gravity on falls (0.25 -> 1.9 m error), a loose one lets climbs lag (1.0 -> 1.2 m).
MOVE_HEIGHT = float(__import__("os").environ.get("MOVE_HEIGHT", "0.6"))
MOVE_INTERVAL = float(__import__("os").environ.get("MOVE_INTERVAL", "0.1"))
_last_pos = {}
_last_seq = {}
_last_teleport = {}
# When the remote player stands still the clone must stand where it does: the animation's root motion
# drifts a little at every stop (0.4-1.3 m seen in play) and errors below the old teleport distance were never fixed.
SETTLE_ERROR = float(__import__("os").environ.get("SETTLE_ERROR", "0.25"))
SETTLE_PACKETS = 8          # target unmoved for this many packets (~0.27 s) counts as standing still
_still = {}
_last_target = {}


def apply_remote(p, clone, data, slot=0):
    """Publish a remote sample: the first one (or a big discontinuity) teleports, drift is fixed with
    light moves, everything else is just the per-frame pose / animation."""
    seq, index, frame, x, y, z, qx, qy, qz, qw, _pad = PACKET.unpack(data)
    if seq <= _last_seq.get(clone, -1) and seq + 1000 > _last_seq.get(clone, -1):
        return  # old or duplicate datagram (small tolerance lets a restarted sender through)
    _last_seq[clone] = seq
    table = p.read32(clone + CLASS + 0x2AC)
    motion = table + index * MOTION_STRIDE if index != 0xFFFFFFFF else 0
    # The animation's root motion walks the puppet by itself; two kinds of correction exist:
    #  * move  (FLAG_MOVE): the teleport without ResetCharacterObj - the base position changes but motion,
    #    velocity and cloth keep going. Used for every normal correction, also vertical ones: climbs and
    #    other physics-driven animations leave the clone on the floor otherwise ("sliding").
    #  * teleport: full reset, only for the first sample or a discontinuity (level load, long lag).
    cx, cy, cz = (p.readf(clone + 0xA0 + 4 * i) for i in range(3))
    err2 = (cx - x) ** 2 + (cz - z) ** 2
    dy = abs(cy - y)
    first = TELEPORT_DISTANCE < 0 or clone not in _last_pos
    far = err2 > TELEPORT_DISTANCE ** 2 or dy > TELEPORT_HEIGHT
    _last_pos[clone] = (x, y, z)
    now = time.time()
    jump = first or far
    # standing-still detector on the TARGET position: settle small leftovers once it stops
    prev = _last_target.get(clone)
    _last_target[clone] = (x, y, z)
    moved = prev is None or (prev[0] - x) ** 2 + (prev[2] - z) ** 2 > 0.02 ** 2
    _still[clone] = 0 if moved else _still.get(clone, 0) + 1
    settle = _still[clone] >= SETTLE_PACKETS and (err2 > SETTLE_ERROR ** 2 or dy > SETTLE_ERROR)
    move = (not jump and (err2 > MOVE_DISTANCE ** 2 or dy > MOVE_HEIGHT or settle)
            and now - _last_teleport.get(clone, -1e9) > MOVE_INTERVAL)
    if jump or move:
        _last_teleport[clone] = now
    TELEPORTS[0] += jump
    MOVES[0] += move
    write_state(p, slot, pos=(x, y, z), quat=(qx, qy, qz, qw), motion=motion, frame=frame, blend=frame, seq=seq,
                teleport=jump, move=move)


DISCONNECT_SECONDS = float(os.environ.get("DISCONNECT_SECONDS", "3.0"))
PARK_HEIGHT = 3000.0   # far above the world: the engine skips its geometry, nothing is drawn or collided


def park(p, clone, slot=0):
    """Peer went silent: lift the clone out of the world and forget its sequence/position so the
    first packet of a reconnecting (restarted) sender teleports it back."""
    cl = clone + CLASS
    quat_ptr = p.read32(cl + 0x13C)
    quat = tuple(p.readf(quat_ptr + 4 * i) for i in range(4))
    pos = [p.readf(clone + 0xA0 + 4 * i) for i in range(3)]
    write_state(p, slot, pos=(pos[0], pos[1] + PARK_HEIGHT, pos[2]), quat=quat, motion=0, teleport=True)
    _last_pos.pop(clone, None)
    _last_seq.pop(clone, None)
    _last_teleport.pop(clone, None)
    _last_target.pop(clone, None)
    _still.pop(clone, None)


class Remotes:
    """One clone (and puppet slot) per remote player id; reuses a parked clone when all are taken."""

    def __init__(self, p):
        self.slots = [(i, clone_address(p, i)) for i in range(4) if clone_address(p, i)]
        self.by_pid, self.last_rx, self.parked = {}, {}, set()

    def slot_for(self, pid):
        if pid not in self.by_pid:
            used = set(self.by_pid.values())
            free = [s for s in self.slots if s[0] not in used]
            if not free:   # reuse the clone of a silent player
                free = [s for s in self.slots if any(q for q, v in self.by_pid.items() if v == s[0] and q in self.parked)]
                if free:
                    self.by_pid = {q: v for q, v in self.by_pid.items() if v != free[0][0]}
            if not free:
                return None
            self.by_pid[pid] = free[0][0]
            print("player %d -> clone slot %d" % (pid, free[0][0]), flush=True)
        return next(s for s in self.slots if s[0] == self.by_pid[pid])


class Tracer:
    """env TRACE=file.csv: one row per tick comparing the remote target with what the first clone
    does, plus a screenshot (SNAPDIR) when an anomaly lasts 6+ ticks:
      M  clone animation differs from the sent one     U  sent animation is not in the table
      V  height error > 0.4                            H  horizontal error > 1.0"""

    def __init__(self, path):
        self.f = open(path, "w")
        self.f.write("t,seq,tgt_motion,tgt_frame,tx,ty,tz,clone_motion,clone_frame,cx,cy,cz,vy,flags\n")
        self.t0, self.run, self.snaps, self.last_snap, self.counts, self.rows = time.time(), {}, 0, 0.0, {}, 0
        self.pending = None

    def sample(self, p, clone, t):
        cl = clone + CLASS
        m, table, frame, cx, cy, cz, vy = p.read32_many(
            [cl + 0x1C0, cl + 0x2AC, cl + 0x1C8, clone + 0xA0, clone + 0xA4, clone + 0xA8, clone + 0xC4])
        f = lambda v: struct.unpack("<f", struct.pack("<I", v))[0]
        cmotion = (m - table) // MOTION_STRIDE if m >= table and (m - table) % MOTION_STRIDE == 0 else -1
        seq, index, tframe, tx, ty, tz = t[0], t[1], t[2], t[3], t[4], t[5]
        flags = ""
        if index == 0xFFFFFFFF:
            flags += "U"
        elif cmotion != index:
            flags += "M"
        if abs(f(cy) - ty) > 0.4:
            flags += "V"
        if ((f(cx) - tx) ** 2 + (f(cz) - tz) ** 2) ** 0.5 > 1.0:
            flags += "H"
        now = time.time() - self.t0
        self.rows += 1
        self.f.write("%.2f,%d,%d,%.1f,%.2f,%.2f,%.2f,%d,%.1f,%.2f,%.2f,%.2f,%.2f,%s\n" % (
            now, seq, index if index != 0xFFFFFFFF else -1, tframe, tx, ty, tz, cmotion, f(frame), f(cx), f(cy), f(cz), f(vy), flags))
        if self.rows % 30 == 0:
            self.f.flush()
        for k in "MUVH":
            self.run[k] = self.run.get(k, 0) + 1 if k in flags else 0
            self.counts[k] = self.counts.get(k, 0) + (k in flags)
        persistent = "".join(k for k in "MUVH" if self.run[k] == 6)
        if persistent and self.snaps < 8 and time.time() - self.last_snap > 8.0 and "SNAPDIR" in os.environ:
            from snap import AsyncSnap
            if self.pending and not self.pending.poll():
                return
            self.pending = None
            self.snaps += 1
            self.last_snap = time.time()
            self.pending = AsyncSnap(p, os.path.join(os.environ["SNAPDIR"], "trace_%03d_%s.png" % (int(now), persistent)))
            print("anomaly %s at t=%.0fs (clone motion %d, sent %d, dy %.2f): screenshot saved" % (
                persistent, now, cmotion, index if index != 0xFFFFFFFF else -1, f(cy) - ty), flush=True)

    def summary(self):
        self.f.flush()
        return "trace: %d ticks, anomalous ticks %s" % (self.rows, self.counts)


class Scenario:
    """Scripted local player from env SCENARIO (JSON list), run inside the bridge because the
    emulator serves one PINE client at a time. Events, by time in seconds:
      {"t": 2.0, "stick": [0, 1]}          hold the left stick (x, y; y=1 forward)
      {"t": 4.0, "hold": 128}              hold a button mask (0 releases); push edge is automatic
      {"t": 6.0, "snap": "name"}           save SNAPDIR/name.png from a savestate"""

    def __init__(self, path):
        self.events = sorted(json.load(open(path)), key=lambda e: e["t"])
        self.start = time.time()
        self.stick, self.held, self.prev = (0.0, 0.0), 0, 0
        self.queue, self.pending = [], None

    def tick(self, p):
        now = time.time() - self.start
        while self.events and self.events[0]["t"] <= now:
            e = self.events.pop(0)
            self.stick = tuple(e.get("stick", self.stick))
            self.held = e.get("hold", self.held)
            if "snap" in e:
                self.queue.append(os.path.join(os.environ.get("SNAPDIR", "."), e["snap"] + ".png"))
        # screenshots never block the tick loop (a blocked loop freezes the clone and looks like a bug)
        from snap import AsyncSnap
        if self.pending and self.pending.poll():
            self.pending = None
        if not self.pending and self.queue:
            self.pending = AsyncSnap(p, self.queue.pop(0))
        set_pad(p, held=self.held, push=self.held & ~self.prev, release=self.prev & ~self.held, stick=self.stick)
        self.prev = self.held


def tick_loop(p, player, clone, send, recv, duration=None, local=None):
    """recv() yields (pid, datagram) pairs; every pid gets its own clone/puppet slot."""
    seq = 0
    stats = []
    target = [None]
    end = time.time() + duration if duration else None
    period = 1.0 / RATE
    next_tick = time.time()
    scenario = Scenario(os.environ["SCENARIO"]) if "SCENARIO" in os.environ else None
    remotes = Remotes(p)
    tracer = Tracer(os.environ["TRACE"]) if "TRACE" in os.environ else None
    while True:
        if scenario:
            scenario.tick(p)
        if send is not None:
            send(pack(seq, *(local() if local else read_local(p, player))))
        for pid, data in recv():
            slot = remotes.slot_for(pid)
            if slot is None:
                continue
            index, obj = slot
            if pid in remotes.parked:
                print("player %d back after %.1fs" % (pid, time.time() - remotes.last_rx[pid]), flush=True)
                remotes.parked.discard(pid)
            apply_remote(p, obj, data, index)
            remotes.last_rx[pid] = time.time()
            if index == remotes.slots[0][0]:
                target[0] = PACKET.unpack(data)
                if tracer:
                    tracer.sample(p, obj, target[0])
        for pid, last in list(remotes.last_rx.items()):
            if pid not in remotes.parked and time.time() - last > DISCONNECT_SECONDS:
                index, obj = next(s for s in remotes.slots if s[0] == remotes.by_pid[pid])
                print("player %d silent for %.1fs: parking clone %d" % (pid, DISCONNECT_SECONDS, index), flush=True)
                park(p, obj, index)
                remotes.parked.add(pid)
                if index == remotes.slots[0][0]:
                    target[0] = None
        seq += 1
        first = remotes.slots[0][1]
        if end and target[0]:
            t = target[0]
            stats.append(((p.readf(first + 0xA0) - t[3]) ** 2 + (p.readf(first + 0xA8) - t[5]) ** 2) ** 0.5)
        if end and time.time() > end:
            if scenario:
                pad_release(p)
            if tracer:
                print(tracer.summary(), flush=True)
            if stats:
                stats.sort()
                print("received-state tracking over %d ticks: error mean %.2f p95 %.2f max %.2f | teleports %d" % (
                    len(stats), sum(stats) / len(stats), stats[int(len(stats) * .95)], stats[-1], TELEPORTS[0]), flush=True)
            else:
                print("no state received", flush=True)
            return
        if seq % int(RATE) == 0:
            line = []
            for index, obj in remotes.slots:
                m, t = p.read32_many([obj + CLASS + 0x1C0, obj + CLASS + 0x2AC])
                line.append("clone%d motion %d pos (%.2f, %.2f, %.2f)" % (
                    index, (m - t) // MOTION_STRIDE, p.readf(obj + 0xA0), p.readf(obj + 0xA4), p.readf(obj + 0xA8)))
            print("tick %d: %s | player (%.2f, %.2f)" % (seq, " ; ".join(line), p.readf(player + 0xA0), p.readf(player + 0xA8)),
                  flush=True)
        next_tick += period
        delay = next_tick - time.time()
        if delay > 0:
            time.sleep(delay)
        else:
            next_tick = time.time()


def replay(p, clone, packets, dx, dz):
    """Play recorded samples through the puppet; report how smooth the clone moves."""
    steps, jumps, errs, yerrs = [], 0, [], []
    last = None
    period = 1.0 / RATE
    t = time.time()
    for raw in packets:
        f = list(PACKET.unpack(raw))
        f[3] += dx
        f[5] += dz
        before = len(_last_pos)
        apply_remote(p, clone, PACKET.pack(*f))
        time.sleep(period)
        pos = (p.readf(clone + 0xA0), p.readf(clone + 0xA8))
        yerrs.append(abs(p.readf(clone + 0xA4) - f[4]))
        errs.append(((pos[0] - f[3]) ** 2 + (pos[1] - f[5]) ** 2) ** 0.5)
        if last:
            steps.append(((pos[0] - last[0]) ** 2 + (pos[1] - last[1]) ** 2) ** 0.5)
        last = pos
    def rough(v):
        return sum(abs(b - a) for a, b in zip(v, v[1:])) / max(1, len(v) - 1)

    ref = [PACKET.unpack(r) for r in packets]
    ref_steps = [((b[3] - a[3]) ** 2 + (b[5] - a[5]) ** 2) ** 0.5 for a, b in zip(ref, ref[1:])]
    print("roughness (mean |step change|): clone %.4f  real %.4f" % (rough(steps), rough(ref_steps)))
    steps.sort()
    print("vertical error mean %.2f max %.2f" % (sum(yerrs) / len(yerrs), max(yerrs)))
    print("samples %d  error mean %.2f max %.2f | step median %.3f p95 %.3f max %.3f | teleports %d" % (
        len(errs), sum(errs) / len(errs), max(errs), steps[len(steps) // 2], steps[int(len(steps) * .95)],
        steps[-1], TELEPORTS[0]))


TELEPORTS = [0]
MOVES = [0]


def simulated_local():
    """Stand-in for read_local: replay env SIM (a recording) as the local player, looping,
    starting at sample SIM_START (lets two test instances walk different parts)."""
    data = open(os.environ["SIM"], "rb").read()
    rows = [PACKET.unpack(data[i:i + PACKET.size]) for i in range(0, len(data) - PACKET.size + 1, PACKET.size)]
    state = [int(os.environ.get("SIM_START", "0")) % len(rows)]

    def local():
        r = rows[state[0]]
        state[0] = (state[0] + 1) % len(rows)
        return r[1], r[2], r[3:6], r[6:10]
    return local


def sendfile(path, host, port, loss=0.0, jitter_ms=0.0):
    """Replay a recording over UDP like a remote player would (optionally lossy/jittery)."""
    data = open(path, "rb").read()
    packets = [data[i:i + PACKET.size] for i in range(0, len(data) - PACKET.size + 1, PACKET.size)]
    dx, dz = (float(v) for v in os.environ.get("OFFSET", "0,0").split(","))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    start = time.time()
    pending = []  # (due time, datagram) so jitter can reorder packets
    for i, raw in enumerate(packets):
        f = list(PACKET.unpack(raw))
        f[3] += dx
        f[5] += dz
        if random.random() >= loss / 100.0:
            pending.append((start + i / RATE + random.uniform(0, jitter_ms) / 1000.0, PACKET.pack(*f)))
        now = time.time()
        for item in [x for x in pending if x[0] <= now]:
            sock.sendto(item[1], (host, port))
            pending.remove(item)
        time.sleep(max(0.0, start + (i + 1) / RATE - time.time()))
    for _due, datagram in pending:
        sock.sendto(datagram, (host, port))


def main():
    mode = sys.argv[1]
    if mode == "sendfile":
        sendfile(sys.argv[2], sys.argv[3], int(sys.argv[4]),
                 float(sys.argv[5]) if len(sys.argv) > 5 else 0.0, float(sys.argv[6]) if len(sys.argv) > 6 else 0.0)
        return
    p = Pine()
    clone = p.read32(MAILBOX + MB_CLONES)
    if not clone:
        raise SystemExit("no clone: run spawn.py first")
    player = local_player(p)
    if mode not in ("record", "send"):
        for slot in range(4):
            obj = clone_address(p, slot)
            if obj:
                make_puppet(p, obj, slot)
                # The engine would re-derive the clone's motion from the (absent) pad; leave it alone.
                p.write(4, obj + CLASS + 0xD90, 3)  # WRITE8

    if mode == "record":
        out = open(sys.argv[2], "wb")
        limit = time.time() + float(sys.argv[3]) if len(sys.argv) > 3 else None
        seq = 0
        while limit is None or time.time() < limit:
            out.write(pack(seq, *read_local(p, player)))
            seq += 1
            time.sleep(1.0 / RATE)
        out.close()
        return

    if mode == "replay":
        data = open(sys.argv[2], "rb").read()
        packets = [data[i:i + PACKET.size] for i in range(0, len(data) - PACKET.size + 1, PACKET.size)]
        dx = float(sys.argv[3]) if len(sys.argv) > 3 else 1.6
        dz = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
        replay(p, clone, packets, dx, dz)
        return

    if mode == "echo":
        dx = float(sys.argv[2]) if len(sys.argv) > 2 else 1.6
        dz = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
        pending = []

        def send(data):
            fields = list(PACKET.unpack(data))
            fields[3] += dx
            fields[5] += dz
            pending.append(PACKET.pack(*fields))

        def recv():
            out, pending[:] = [(0, d) for d in pending], []
            return out

        tick_loop(p, player, clone, send, recv, float(os.environ["DURATION"]) if "DURATION" in os.environ else None)
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    server = mode in ("host", "recv")      # many peers; each source address gets a player id
    peers = {}                              # server: address -> pid (1, 2, ...)
    host_addr = None
    if server:
        sock.bind(("0.0.0.0", int(sys.argv[2])))
    else:
        host_addr = (sys.argv[2], int(sys.argv[3]))

    def send(data):
        for addr in (peers if server else [host_addr]):
            sock.sendto(data, addr)          # the local player's own state (pid 0)

    def recv():
        out = []
        while True:
            try:
                data, addr = sock.recvfrom(256)
            except BlockingIOError:
                return out
            if len(data) != PACKET.size:
                continue
            if not server:
                out.append((PACKET.unpack(data)[-1], data))   # the host stamped the origin pid
                continue
            pid = peers.setdefault(addr, len(peers) + 1)
            fields = list(PACKET.unpack(data))
            fields[-1] = pid
            data = PACKET.pack(*fields)
            for other in peers:               # relay to every other peer
                if other != addr:
                    sock.sendto(data, other)
            out.append((pid, data))

    duration = float(os.environ["DURATION"]) if "DURATION" in os.environ else None
    tick_loop(p, player, clone, None if mode == "recv" else send, (lambda: []) if mode == "send" else recv, duration,
              simulated_local() if "SIM" in os.environ else None)


if __name__ == "__main__":
    main()
