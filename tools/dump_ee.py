"""Dump the 32 MiB EE RAM through PINE and wrap it in a MIPS R5900 ELF for Ghidra/REA.

Usage: python3 dump_ee.py OUT_PREFIX
       python3 dump_ee.py --slice RAM.bin START END OUT.elf
Writes OUT_PREFIX.bin (raw RAM) and OUT_PREFIX.elf (single PT_LOAD from 0x100000);
--slice wraps only [START, END) of an existing dump so Ghidra analyzes less.
"""
import struct
import sys
import time

from pine import Pine

RAM_SIZE = 32 * 1024 * 1024
# Skip the kernel area; game code starts at 0x100000.
LOAD_BASE = 0x00100000


def make_elf(ram, entry, base=LOAD_BASE, end=RAM_SIZE):
    image = ram[base:end]
    ehsize, phsize = 52, 32
    offset = 0x1000
    header = struct.pack(
        "<4sBBBB8sHHIIIIIHHHHHH",
        b"\x7fELF", 1, 1, 1, 0, b"\0" * 8,
        2, 8, 1, entry, ehsize, 0,
        # Same e_flags as SCUS_974.72: MIPS-III, EE (R5900) machine.
        0x20924001, ehsize, phsize, 1, 40, 0, 0,
    )
    phdr = struct.pack("<IIIIIIII", 1, offset, base, base,
                       len(image), len(image), 7, 0x1000)
    pad = b"\0" * (offset - ehsize - phsize)
    return header + phdr + pad + image


def main():
    if sys.argv[1] == "--slice":
        ram = open(sys.argv[2], "rb").read()
        start, end = int(sys.argv[3], 16), int(sys.argv[4], 16)
        open(sys.argv[5], "wb").write(make_elf(ram, start, start, end))
        return
    prefix = sys.argv[1]
    pine = Pine()
    print("PCSX2", pine.version(), "|", pine.game_id(), "|", pine.title(), "|", pine.status())
    t = time.time()
    ram = pine.read_block(0, RAM_SIZE)
    print("dumped %d bytes in %.1fs" % (len(ram), time.time() - t))
    open(prefix + ".bin", "wb").write(ram)
    open(prefix + ".elf", "wb").write(make_elf(ram, 0x00100010))
    print("wrote", prefix + ".bin", prefix + ".elf")


if __name__ == "__main__":
    main()
