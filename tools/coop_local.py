"""Start the two-instance co-op test on one Windows PC (host = instance B, client = instance A).

  python coop_local.py            start both emulators, load the clean shrine state, run the bridges
  python coop_local.py --stop     stop the bridges and close the emulators

Layout (created by hand once, see MULTIPLAYER.md "Windows setup"):
  work/pcsx2_a/emu, work/pcsx2_b/emu   portable copies of PCSX2 (portable.txt + inis/PCSX2.ini with PINE on,
                                       slots 28011 / 28012, the controllers SDL-0 / SDL-1 plus the keyboard)
  work/pcsx2_x/savestates/<serial>.01.p2s   clean state: the Wander standing at the shrine, no hook

Both bridges run in their own process (the emulator serves one PINE client at a time), with a session
length from DURATION (default 3600 s). Window positions: A on the left half of the main monitor, B on the
right half. Logs: work/net_a_win.log (client) and work/net_b_win.log (host).
"""
import ctypes
import glob
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
SLOTS = {"a": 28011, "b": 28012}
STATE_SLOT = int(os.environ.get("STATE_SLOT", "1"))   # savestate slot loaded into both instances
PORT = 5020


def emu_dir(name):
    return os.path.join(ROOT, "work", "pcsx2_" + name, "emu")


def find_iso():
    isos = glob.glob(os.path.join(ROOT, "*.iso"))
    if not isos:
        raise SystemExit("put the game image (SCUS-97472, NTSC-U) in " + ROOT)
    return isos[0]


def pids(name):
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance Win32_Process -Filter \"Name='pcsx2-qt.exe'\" | "
                          "Where-Object { $_.ExecutablePath -like '*pcsx2_%s*' }).ProcessId" % name],
                         capture_output=True, text=True).stdout.split()
    return [int(x) for x in out]


def place_window(pid, x, y, w, h):
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            found.append(hwnd)
        return True
    user32.EnumWindows(visit, 0)
    for hwnd in found:
        user32.MoveWindow(hwnd, x, y, w, h, True)
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x43)   # topmost once, to come in front of the editor
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x43)


def start_emulator(name):
    if pids(name):
        return
    subprocess.Popen([os.path.join(emu_dir(name), "pcsx2-qt.exe"), "-batch", "--", find_iso()], cwd=emu_dir(name))


def connect(slot, timeout=90.0):
    sys.path.insert(0, TOOLS)
    import pine
    end = time.time() + timeout
    while True:
        try:
            return pine.Pine(slot)      # PCSX2 on Windows serves PINE over TCP, port = slot
        except OSError:
            if time.time() > end:
                raise SystemExit("emulator %d does not answer PINE (EnablePINE in its ini?)" % slot)
            time.sleep(1.0)


def load_clean_state(name):
    slot = int(os.environ.get("STATE_SLOT_" + name.upper(), STATE_SLOT))   # e.g. STATE_SLOT_A=1 STATE_SLOT_B=2
    state_dir = os.path.join(ROOT, "work", "pcsx2_" + name, "savestates")
    os.makedirs(state_dir, exist_ok=True)
    target = os.path.join(state_dir, "SCUS-97472 (C19A374E).%02d.p2s" % slot)
    if not os.path.exists(target):
        donor = os.path.join(ROOT, "work", "pcsx2_a", "savestates", "SCUS-97472 (C19A374E).%02d.p2s" % slot)
        if not os.path.exists(donor):
            raise SystemExit("no clean state: reach the shrine once in instance A and save slot 1")
        shutil.copyfile(donor, target)
    p = connect(SLOTS[name])
    time.sleep(8.0)                     # the game must be running before a state can be loaded
    p.load_state(slot)
    time.sleep(4.0)
    p.close()


def stop():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'netbridge' -and $_.ProcessId -ne $PID -and $_.Name -eq 'python.exe' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"])
    time.sleep(1.0)
    for name in SLOTS:
        for pid in pids(name):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def main():
    if "--stop" in sys.argv:
        stop()
        return
    duration = os.environ.get("DURATION", "3600")
    stop()
    for name in ("a", "b"):
        start_emulator(name)
        time.sleep(6.0)
    time.sleep(6.0)
    width = ctypes.windll.user32.GetSystemMetrics(0)
    for index, name in enumerate(("a", "b")):
        for pid in pids(name):
            place_window(pid, index * width // 2, 60, width // 2, 760)
    for name in ("a", "b"):
        load_clean_state(name)
    env = dict(os.environ, DURATION=duration, PYTHONUNBUFFERED="1")
    logs = {"b": open(os.path.join(ROOT, "work", "net_b_win.log"), "w"),
            "a": open(os.path.join(ROOT, "work", "net_a_win.log"), "w")}
    def bridge_env(name):
        extra = {"PINE_SLOT": str(SLOTS[name])}
        for key in ("DEBUG_DAMAGE_AT", "DEBUG_KILL_EVERY", "DEBUG_KILL_BOSS_AT", "PULL"):
            if os.environ.get(key + "_" + name.upper()):
                extra[key] = os.environ[key + "_" + name.upper()]
        if os.environ.get("SCENARIO_" + name.upper()):          # scripted controller for this instance
            extra["SCENARIO"] = os.environ["SCENARIO_" + name.upper()]
        return dict(env, **extra)

    subprocess.Popen([sys.executable, "netbridge.py", "host", str(PORT)], cwd=TOOLS, stdout=logs["b"],
                     stderr=subprocess.STDOUT, env=bridge_env("b"))
    time.sleep(2.0)
    subprocess.Popen([sys.executable, "netbridge.py", "join", "127.0.0.1", str(PORT)], cwd=TOOLS,
                     stdout=logs["a"], stderr=subprocess.STDOUT, env=bridge_env("a"))
    print("co-op running for %s s: A (left) = client, B (right) = host; logs in work/net_*_win.log" % duration)


if __name__ == "__main__":
    main()
