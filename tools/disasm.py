"""Minimal R5900 disassembler for live RAM, with recovered symbol names.

  python3 disasm.py ADDR|NAME [COUNT]
  env RAM=eeMemory.bin   read a RAM image (e.g. from a savestate) instead of the live emulator
"""
import bisect
import os
import struct
import sys

from pine import Pine

REG = ("zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 "
       "s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra").split()
SYMS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "work", "symbols.tsv")

I_OPS = {0x08: "addi", 0x09: "addiu", 0x0A: "slti", 0x0B: "sltiu", 0x0C: "andi", 0x0D: "ori",
         0x0E: "xori", 0x18: "daddi", 0x19: "daddiu"}
MEM_OPS = {0x20: "lb", 0x21: "lh", 0x23: "lw", 0x24: "lbu", 0x25: "lhu", 0x27: "lwu", 0x28: "sb",
           0x29: "sh", 0x2B: "sw", 0x37: "ld", 0x3F: "sd", 0x1E: "lq", 0x1F: "sq"}
FPU_MEM = {0x31: "lwc1", 0x39: "swc1"}
SPECIAL = {0x00: "sll", 0x02: "srl", 0x03: "sra", 0x08: "jr", 0x09: "jalr", 0x0A: "movz",
           0x0B: "movn", 0x21: "addu", 0x23: "subu", 0x24: "and", 0x25: "or", 0x26: "xor",
           0x27: "nor", 0x2A: "slt", 0x2B: "sltu", 0x2D: "daddu", 0x2F: "dsubu", 0x18: "mult",
           0x1A: "div", 0x10: "mfhi", 0x12: "mflo", 0x38: "dsll", 0x3C: "dsll32", 0x3E: "dsrl32",
           0x3F: "dsra32"}


def load_symbols():
    names = {}
    for line in open(SYMS):
        a, _s, _k, n = line.rstrip("\n").split("\t")
        names.setdefault(int(a, 16), n)
    return names


def s16(v):
    return v - 0x10000 if v & 0x8000 else v


def decode(addr, w, names):
    op, rs, rt, rd = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
    imm = w & 0xFFFF
    if w == 0:
        return "nop"
    if op == 0:
        fn = w & 0x3F
        name = SPECIAL.get(fn, "special.%02x" % fn)
        if fn in (0x00, 0x02, 0x03, 0x38, 0x3C, 0x3E, 0x3F):
            return "%s %s,%s,%d" % (name, REG[rd], REG[rt], (w >> 6) & 31)
        if fn == 0x08:
            return "jr %s" % REG[rs]
        if fn == 0x09:
            return "jalr %s" % REG[rs]
        if fn in (0x10, 0x12):
            return "%s %s" % (name, REG[rd])
        return "%s %s,%s,%s" % (name, REG[rd], REG[rs], REG[rt])
    if op in (2, 3):
        t = (addr & 0xF0000000) | ((w & 0x3FFFFFF) << 2)
        return "%s %s" % ("j" if op == 2 else "jal", names.get(t, "0x%08x" % t))
    if op in (4, 5, 0x14, 0x15):
        t = addr + 4 + (s16(imm) << 2)
        return "%s %s,%s,0x%08x" % ({4: "beq", 5: "bne", 0x14: "beql", 0x15: "bnel"}[op], REG[rs], REG[rt], t)
    if op in (6, 7):
        return "%s %s,0x%08x" % ("blez" if op == 6 else "bgtz", REG[rs], addr + 4 + (s16(imm) << 2))
    if op == 1 and rt in (0, 1):
        return "%s %s,0x%08x" % ("bltz" if rt == 0 else "bgez", REG[rs], addr + 4 + (s16(imm) << 2))
    if op == 1:
        return "regimm.%d %s,0x%08x" % (rt, REG[rs], addr + 4 + (s16(imm) << 2))
    if op == 0x0F:
        return "lui %s,0x%x" % (REG[rt], imm)
    if op in I_OPS:
        return "%s %s,%s,%d" % (I_OPS[op], REG[rt], REG[rs], s16(imm) if op not in (0xC, 0xD, 0xE) else imm)
    if op in MEM_OPS:
        return "%s %s,%d(%s)" % (MEM_OPS[op], REG[rt], s16(imm), REG[rs])
    if op in FPU_MEM:
        return "%s $f%d,%d(%s)" % (FPU_MEM[op], rt, s16(imm), REG[rs])
    return ".word 0x%08x" % w


def disasm(p, start, count, names):
    data = p.read_block(start & ~7, (count * 4 + 15) & ~7)
    off = start & 7
    for i in range(count):
        a = start + 4 * i
        w = struct.unpack_from("<I", data, off + 4 * i)[0]
        label = names.get(a)
        if label:
            print("%s:" % label)
        print("  %08x  %08x  %s" % (a, w, decode(a, w, names)))


class RamFile:
    """read_block() over a 32 MiB EE RAM image, so disasm() works offline."""

    def __init__(self, path):
        self.ram = open(path, "rb").read()

    def read_block(self, addr, length):
        addr &= 0x1FFFFFF
        return self.ram[addr:addr + length]

    def read32(self, addr):
        return struct.unpack_from("<I", self.ram, addr & 0x1FFFFFF)[0]


if __name__ == "__main__":
    syms = load_symbols()
    by_name = {v: k for k, v in syms.items()}
    target = sys.argv[1]
    start = by_name[target] if target in by_name else int(target, 16)
    disasm(RamFile(os.environ["RAM"]) if "RAM" in os.environ else Pine(), start, int(sys.argv[2]) if len(sys.argv) > 2 else 40, syms)
