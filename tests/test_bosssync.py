import struct
import sys
import time
from pathlib import Path
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from bosssync import BossLink, BossSample, CLASS


def fword(value):
    return struct.unpack("<I", struct.pack("<f", value))[0]


class FakePine:
    def __init__(self, obj, motion, quat_ptr):
        self.obj, self.motion, self.quat_ptr = obj, motion, quat_ptr

    def read32(self, address):
        if address == self.obj + CLASS + 0x13C:
            return self.quat_ptr
        if address == self.motion + 8:
            return 0x44
        raise AssertionError("unexpected read32 at 0x%08x" % address)

    def read32_many(self, addresses):
        self.assertEqual(addresses, [self.obj + CLASS + 0x1C0, self.obj + CLASS + 0x1C8])
        return [self.motion, fword(12.5)]

    def readf(self, address):
        values = {
            self.obj + 0xA0: 10.0,
            self.obj + 0xA4: 20.0,
            self.obj + 0xA8: 30.0,
            self.quat_ptr + 0: 0.0,
            self.quat_ptr + 4: 0.0,
            self.quat_ptr + 8: 0.0,
            self.quat_ptr + 12: 1.0,
        }
        return values[address]

    def assertEqual(self, actual, expected):
        if actual != expected:
            raise AssertionError("%r != %r" % (actual, expected))


class FakeBoss:
    def __init__(self, obj):
        self.obj = obj

    def read_damage(self):
        return [1.0, 2.5]

    def life(self):
        return 98.0, 100.0, 0


class BossSampleTests(unittest.TestCase):
    def test_sample_keeps_all_seven_wire_fields_named(self):
        obj, motion, quat_ptr = 0x100000, 0x120000, 0x130000
        link = BossLink(FakePine(obj, motion, quat_ptr))
        link.boss = FakeBoss(obj)
        link.next_scan = time.time() + 60.0  # avoid resolving a real game object

        sample = link.sample()

        self.assertIsInstance(sample, BossSample)
        self.assertEqual(sample.motion, 0x44)
        self.assertEqual(sample.frame, 12.5)
        self.assertEqual(sample.pos, (10.0, 20.0, 30.0))
        self.assertEqual(sample.quat, (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(sample.damage, (1.0, 2.5))
        self.assertFalse(sample.authoritative)
        self.assertEqual(sample.life, 98.0)
        self.assertEqual(len(sample), 7)


if __name__ == "__main__":
    unittest.main()
