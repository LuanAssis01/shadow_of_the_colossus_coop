import struct
import sys
from pathlib import Path
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from mips import Asm, hi_lo


class MipsAssemblerTests(unittest.TestCase):
    def test_hi_lo_compensates_for_a_signed_low_half(self):
        self.assertEqual(hi_lo(0x12348000), (0x1235, 0x8000))
        self.assertEqual(hi_lo(0x00123456), (0x0012, 0x3456))

    def test_forward_branch_is_resolved_relative_to_the_delay_slot(self):
        asm = Asm(0x1000)
        asm.beq("a0", "zero", "target")
        asm.nop()
        asm.label("target")
        asm.jr("ra")
        words = struct.unpack("<3I", asm.assemble())

        self.assertEqual(words[0] & 0xFFFF, 1)
        self.assertEqual(words[1], 0)
        self.assertEqual(words[2] & 0x3F, 0x08)


if __name__ == "__main__":
    unittest.main()
