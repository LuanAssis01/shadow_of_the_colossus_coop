"""Recover Elf32_Sym tables that loaded XFF modules leave in EE RAM.

  python3 xff_symbols.py RAM.bin OUT.tsv

Finds runs of 16-byte Elf32_Sym records immediately followed by their string
table, and writes "address<TAB>size<TAB>type<TAB>name" for every named symbol.
"""
import struct
import sys

import numpy as np

STT = {0: "notype", 1: "object", 2: "func", 3: "section", 4: "file"}


def find_tables(ram):
    """Yield (symtab_start, count, strtab_start) for every symtab+strtab pair.

    Loaded XFF symtabs do not start with the null symbol, so look for long runs
    of 16-byte records whose value lies in EE RAM and that end at a NUL-led strtab.
    """
    u = np.frombuffer(ram, dtype="<u4")
    n = len(u) - 4
    tail = u[3:n + 3]
    value = u[1:n + 1]
    ok = (((tail & 0xF) <= 2) & (((tail >> 4) & 0xF) <= 2) & (((tail >> 8) & 0xFF) == 0)
          & ((tail >> 16) < 64) & (value >= 0x100000) & (value < 0x2000000) & (u[:n] < 0x200000))
    hits = set(np.nonzero(ok)[0].tolist())
    for i in sorted(hits):
        if i - 4 in hits:
            continue
        j = i
        while j + 4 in hits:
            j += 4
        count = (j - i) // 4 + 1
        end = (j + 4) * 4
        if count >= 100 and ram[end] == 0 and 32 <= ram[end + 1] < 127:
            yield i * 4, count, end


def main():
    ram = open(sys.argv[1], "rb").read()
    rows = {}
    for start, count, strtab in find_tables(ram):
        named = 0
        for i in range(count):
            name, value, size, info, other, shndx = struct.unpack_from("<IIIBBH", ram, start + 16 * i)
            end = ram.find(b"\0", strtab + name)
            label = ram[strtab + name:end].decode("latin1")
            if label and value and label.isprintable():
                rows[(value, label)] = (size, STT[info & 15])
                named += 1
        print("symtab @0x%08x: %d records, strtab @0x%08x, %d named" % (start, count, strtab, named))
    with open(sys.argv[2], "w") as out:
        for (value, label), (size, kind) in sorted(rows.items()):
            out.write("0x%08x\t0x%x\t%s\t%s\n" % (value, size, kind, label))
    print("wrote %d symbols to %s" % (len(rows), sys.argv[2]))


if __name__ == "__main__":
    main()
