import sys
from pathlib import Path
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import doctor


class DoctorLayoutTests(unittest.TestCase):
    def test_documented_mailbox_map_has_no_overlap(self):
        self.assertEqual(doctor.mailbox_overlaps(), [])

    def test_overlap_detector_reports_the_two_conflicting_ranges(self):
        ranges = (("first", 0x10, 0x20), ("second", 0x18, 0x28), ("third", 0x30, 0x40))
        self.assertEqual(doctor.mailbox_overlaps(ranges), [("first", "second")])


if __name__ == "__main__":
    unittest.main()
