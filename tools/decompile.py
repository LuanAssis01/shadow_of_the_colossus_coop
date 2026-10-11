"""Decompile game functions with Ghidra headless (Windows, no REA needed).

  python decompile.py import RAM.bin      create the Ghidra project from an EE RAM image (code 0x100000..0x1600000)
  python decompile.py NAME|0xADDR ...     print the decompiled C of each function (symbols from work/symbols.tsv)

Needs tools/ghidra_12.1.4_PUBLIC (with the Emotion Engine extension) and a JDK 21 in tools/dl/jdk.
The project lives in work/ghidra (not in git).
"""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GHIDRA = os.path.join(HERE, "ghidra_12.1.4_PUBLIC")
PROJECT_DIR = os.path.join(ROOT, "work", "ghidra")
PROJECT = "sotc"
PROGRAM = "core.bin"
SYMBOLS = os.path.join(ROOT, "work", "symbols.tsv")
START, END = 0x00100000, 0x01600000


def env():
    jdk = glob.glob(os.path.join(HERE, "dl", "jdk", "jdk-21*"))
    e = dict(os.environ)
    if jdk:
        e["JAVA_HOME"] = jdk[0]
        e["PATH"] = os.path.join(jdk[0], "bin") + os.pathsep + e["PATH"]
    return e


def headless(*args):
    cmd = [os.path.join(GHIDRA, "support", "analyzeHeadless.bat"), PROJECT_DIR, PROJECT] + list(args)
    return subprocess.run(cmd, env=env(), capture_output=True, text=True)


def do_import(ram):
    os.makedirs(PROJECT_DIR, exist_ok=True)
    core = os.path.join(PROJECT_DIR, PROGRAM)
    with open(ram, "rb") as f:
        f.seek(START)
        open(core, "wb").write(f.read(END - START))
    r = headless("-import", core, "-overwrite", "-noanalysis", "-loader", "BinaryLoader",
                 "-loader-baseAddr", hex(START), "-processor", "r5900:LE:32:default",
                 "-scriptPath", os.path.join(HERE, "gscripts"), "-postScript", "SotcDecompile.java",
                 SYMBOLS, os.path.join(PROJECT_DIR, "import.txt"), "GAMEUTIL_get_player_obj")
    print(r.stdout[-2000:], r.stderr[-2000:])


def decompile(targets):
    out = os.path.join(PROJECT_DIR, "out.txt")
    r = headless("-process", PROGRAM, "-noanalysis", "-scriptPath", os.path.join(HERE, "gscripts"),
                 "-postScript", "SotcDecompile.java", SYMBOLS, out, *targets)
    if r.returncode != 0 or not os.path.exists(out):
        print(r.stdout[-3000:], r.stderr[-3000:])
        return
    print(open(out).read())


if __name__ == "__main__":
    if sys.argv[1] == "import":
        do_import(sys.argv[2])
    else:
        decompile(sys.argv[1:])
