import sys
from pathlib import Path
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import netbridge
from bosssync import BossSample


class ProtocolTests(unittest.TestCase):
    def test_full_packet_preserves_player_horse_boss_and_progress(self):
        player_pos = (1.0, 2.0, 3.0)
        player_quat = (0.0, 0.0, 0.0, 1.0)
        horse = (7, 8.5, (4.0, 5.0, 6.0), player_quat)
        boss = BossSample(0x44, 12.5, (7.0, 8.0, 9.0), player_quat, (3.0, 4.0), True, 98.0)

        wire = netbridge.pack(42, 5, 6.5, player_pos, player_quat, horse=horse, boss=boss,
                              pending=True, progress=(3, 140.0), demo=netbridge.DEMO_PENDING)

        self.assertEqual(len(wire), netbridge.NET_SIZE)
        seq, motion, frame, x, y, z, *_quat, pid = netbridge.PACKET.unpack(wire[:netbridge.PACKET.size])
        self.assertEqual((seq, motion, frame, x, y, z, pid), (42, 5, 6.5, 1.0, 2.0, 3.0, 0))

        flags, horse_motion, horse_frame, hx, hy, hz, *_ = netbridge.HORSE.unpack(
            wire[netbridge.PACKET.size:netbridge.WIRE_SIZE])
        self.assertEqual((flags, horse_motion, horse_frame, hx, hy, hz), (netbridge.HORSE_PRESENT, 7, 8.5, 4.0, 5.0, 6.0))

        flags, boss_motion, boss_frame, bx, by, bz, *_tail = netbridge.BOSS.unpack(
            wire[netbridge.WIRE_SIZE:netbridge.FULL_SIZE])
        self.assertEqual(flags, netbridge.BOSS_PRESENT | netbridge.BOSS_AUTH | netbridge.BOSS_PENDING | netbridge.DEMO_PENDING)
        self.assertEqual((boss_motion, boss_frame, bx, by, bz), (0x44, 12.5, 7.0, 8.0, 9.0))
        self.assertEqual(netbridge.PROGRESS.unpack(wire[netbridge.FULL_SIZE:]), (3, 140.0))

    def test_empty_extensions_keep_the_wire_at_a_stable_size(self):
        wire = netbridge.pack(0, 0, 0.0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))

        self.assertEqual(len(wire), netbridge.NET_SIZE)
        self.assertEqual(netbridge.PROGRESS.unpack(wire[netbridge.FULL_SIZE:]), (-1, 0.0))

    def test_udp_envelope_round_trips_and_legacy_payload_stays_accepted(self):
        payload = netbridge.pack(42, 5, 6.5, (1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 1.0))
        framed = netbridge.wrap_datagram(payload)

        self.assertEqual(len(framed), len(payload) + netbridge.DATAGRAM_HEADER.size)
        self.assertEqual(netbridge.unwrap_datagram(framed), payload)
        self.assertEqual(netbridge.unwrap_datagram(payload), payload)

    def test_unknown_envelope_version_is_rejected(self):
        payload = b"state"
        framed = netbridge.DATAGRAM_HEADER.pack(netbridge.DATAGRAM_MAGIC, 99, 0, len(payload)) + payload

        with self.assertRaises(netbridge.WireProtocolError):
            netbridge.unwrap_datagram(framed)

    def test_jitter_buffer_waits_then_delivers_samples_in_sequence(self):
        def sample(seq):
            return netbridge.pack(seq, 0, 0.0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))

        buffer = netbridge.RemoteJitterBuffer(delay=0.1)
        first, second = sample(10), sample(11)
        self.assertTrue(buffer.push(first, received=0.0))
        self.assertTrue(buffer.push(second, received=0.02))
        self.assertIsNone(buffer.pop(now=0.09))
        self.assertEqual(buffer.pop(now=0.10), first)
        self.assertEqual(buffer.pop(now=0.13), second)
        self.assertFalse(buffer.push(first, received=0.13))

    def test_jitter_buffer_discards_a_previous_sender_incarnation(self):
        def sample(seq):
            return netbridge.pack(seq, 0, 0.0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))

        buffer = netbridge.RemoteJitterBuffer(delay=0.0)
        old, restarted = sample(5000), sample(0)
        buffer.push(old, received=0.0)
        self.assertEqual(buffer.pop(now=0.0), old)
        self.assertTrue(buffer.push(restarted, received=0.1))
        self.assertEqual(buffer.pop(now=0.1), restarted)


if __name__ == "__main__":
    unittest.main()
