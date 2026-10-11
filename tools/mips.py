"""Tiny MIPS (R5900 subset) assembler for the injected multiplayer hook."""

REGS = {name: i for i, name in enumerate(
    "zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 "
    "s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra".split())}


def _r(name):
    return REGS[name]


def _i(op, rs, rt, imm):
    return (op << 26) | (_r(rs) << 21) | (_r(rt) << 16) | (imm & 0xFFFF)


def _rtype(rs, rt, rd, shamt, funct):
    return (_r(rs) << 21) | (_r(rt) << 16) | (_r(rd) << 11) | (shamt << 6) | funct


def hi_lo(addr):
    """Split an address for lui/addiu (lo is sign-extended)."""
    lo = addr & 0xFFFF
    hi = ((addr >> 16) + (1 if lo & 0x8000 else 0)) & 0xFFFF
    return hi, lo


class Asm:
    def __init__(self, base):
        self.base = base
        self.words = []
        self.labels = {}
        self.fixups = []

    @property
    def pc(self):
        return self.base + 4 * len(self.words)

    def label(self, name):
        self.labels[name] = self.pc

    def emit(self, word):
        self.words.append(word & 0xFFFFFFFF)

    # --- instructions -------------------------------------------------
    def nop(self):
        self.emit(0)

    def lui(self, rt, imm):
        self.emit(_i(0x0F, "zero", rt, imm))

    def addiu(self, rt, rs, imm):
        self.emit(_i(0x09, rs, rt, imm))

    def ori(self, rt, rs, imm):
        self.emit(_i(0x0D, rs, rt, imm))

    def andi(self, rt, rs, imm):
        self.emit(_i(0x0C, rs, rt, imm))

    def lw(self, rt, off, base):
        self.emit(_i(0x23, base, rt, off))

    def sw(self, rt, off, base):
        self.emit(_i(0x2B, base, rt, off))

    def ld(self, rt, off, base):
        self.emit(_i(0x37, base, rt, off))

    def sd(self, rt, off, base):
        self.emit(_i(0x3F, base, rt, off))

    def lwc1(self, ft, off, base):
        """ft is the FPU register number (e.g. 12 for $f12)."""
        self.emit((0x31 << 26) | (_r(base) << 21) | (ft << 16) | (off & 0xFFFF))

    def swc1(self, ft, off, base):
        self.emit((0x39 << 26) | (_r(base) << 21) | (ft << 16) | (off & 0xFFFF))

    def and_(self, rd, rs, rt):
        self.emit(_rtype(rs, rt, rd, 0, 0x24))

    def subu(self, rd, rs, rt):
        self.emit(_rtype(rs, rt, rd, 0, 0x23))

    def sltu(self, rd, rs, rt):
        self.emit(_rtype(rs, rt, rd, 0, 0x2B))

    def addu(self, rd, rs, rt):
        self.emit(_rtype(rs, rt, rd, 0, 0x21))

    def move(self, rd, rs):
        # daddu rd, rs, zero keeps the full 64-bit register like the compiler does.
        self.emit(_rtype(rs, "zero", rd, 0, 0x2D))

    def jr(self, rs):
        self.emit(_rtype(rs, "zero", "zero", 0, 0x08))

    def jalr(self, rs):
        self.emit(_rtype(rs, "zero", "ra", 0, 0x09))

    def jal(self, target):
        self.emit((0x03 << 26) | ((target >> 2) & 0x3FFFFFF))

    def j(self, target):
        self.emit((0x02 << 26) | ((target >> 2) & 0x3FFFFFF))

    def _branch(self, op, rs, rt, label):
        self.fixups.append((len(self.words), label))
        self.emit(_i(op, rs, rt, 0))

    def beq(self, rs, rt, label):
        self._branch(0x04, rs, rt, label)

    def bne(self, rs, rt, label):
        self._branch(0x05, rs, rt, label)

    def li(self, rt, value):
        hi, lo = hi_lo(value)
        self.lui(rt, hi)
        self.addiu(rt, rt, lo)

    # --- output -------------------------------------------------------
    def assemble(self):
        for index, label in self.fixups:
            offset = (self.labels[label] - (self.base + 4 * index + 4)) >> 2
            self.words[index] = (self.words[index] & 0xFFFF0000) | (offset & 0xFFFF)
        return b"".join(w.to_bytes(4, "little") for w in self.words)
