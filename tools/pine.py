"""Minimal PINE (PCSX2 IPC) client for reading and writing EE memory."""
import os
import socket
import struct

# PINE_SLOT selects another emulator instance (see work/pcsx2_b: slot 28012 is
# socket "pcsx2.sock.28012"); the default slot 28011 is plain "pcsx2.sock".
_SLOT = os.environ.get("PINE_SLOT", "")
DEFAULT_SOCKET = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid()),
    ".flatpak/net.pcsx2.PCSX2/xdg-run/pcsx2.sock" + ("." + _SLOT if _SLOT and _SLOT != "28011" else ""),
)

READ8, READ16, READ32, READ64 = 0, 1, 2, 3
WRITE8, WRITE16, WRITE32, WRITE64 = 4, 5, 6, 7
VERSION, SAVE_STATE, LOAD_STATE, TITLE, GAME_ID, UUID, GAME_VERSION, STATUS = range(8, 16)

_READ_SIZE = {READ8: 1, READ16: 2, READ32: 4, READ64: 8}
_WRITE_FMT = {WRITE8: "<B", WRITE16: "<H", WRITE32: "<I", WRITE64: "<Q"}
# Largest batch observed to succeed against PCSX2 2.8.2 (60000 READ64 fails).
MAX_BATCH = 40000


class PineError(RuntimeError):
    pass


class Pine:
    def __init__(self, path=DEFAULT_SOCKET):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)

    def close(self):
        self.sock.close()

    def _recv_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise PineError("PINE socket closed")
            buf += chunk
        return bytes(buf)

    def _call(self, body):
        self.sock.sendall(struct.pack("<I", len(body) + 4) + body)
        size = struct.unpack("<I", self._recv_exact(4))[0]
        reply = self._recv_exact(size - 4)
        if reply[0] != 0:
            raise PineError("PINE command failed")
        return reply[1:]

    def _string(self, opcode):
        data = self._call(bytes([opcode]))
        n = struct.unpack("<I", data[:4])[0]
        return data[4:4 + n].rstrip(b"\0").decode("utf-8", "replace")

    def version(self):
        return self._string(VERSION)

    def title(self):
        return self._string(TITLE)

    def game_id(self):
        return self._string(GAME_ID)

    def status(self):
        return {0: "running", 1: "paused", 2: "shutdown"}.get(
            struct.unpack("<I", self._call(bytes([STATUS])))[0], "unknown")

    def read(self, opcode, addr):
        data = self._call(struct.pack("<BI", opcode, addr))
        return int.from_bytes(data[:_READ_SIZE[opcode]], "little")

    def read32(self, addr):
        return self.read(READ32, addr)

    def readf(self, addr):
        return struct.unpack("<f", struct.pack("<I", self.read32(addr)))[0]

    def write(self, opcode, addr, value):
        self._call(struct.pack("<BI", opcode, addr) + struct.pack(_WRITE_FMT[opcode], value))

    def write32(self, addr, value):
        self.write(WRITE32, addr, value & 0xFFFFFFFF)

    def writef(self, addr, value):
        self.write32(addr, struct.unpack("<I", struct.pack("<f", value))[0])

    def read_block(self, addr, length):
        """Read `length` bytes (multiple of 8) with batched READ64 commands."""
        out = bytearray()
        per_batch = MAX_BATCH
        for base in range(addr, addr + length, per_batch * 8):
            count = min(per_batch, (addr + length - base) // 8)
            body = b"".join(struct.pack("<BI", READ64, base + i * 8) for i in range(count))
            out += self._call(body)[:count * 8]
        return bytes(out)

    def read32_many(self, addrs):
        """Read scattered 32-bit words in one PINE message."""
        body = b"".join(struct.pack("<BI", READ32, a) for a in addrs)
        data = self._call(body)
        return list(struct.unpack("<%dI" % len(addrs), data[:4 * len(addrs)]))

    def write_block(self, addr, data):
        """Write bytes (multiple of 4) with batched WRITE32 commands."""
        per_batch = MAX_BATCH
        words = [struct.unpack_from("<I", data, i)[0] for i in range(0, len(data), 4)]
        for start in range(0, len(words), per_batch):
            chunk = words[start:start + per_batch]
            body = b"".join(
                struct.pack("<BII", WRITE32, addr + (start + i) * 4, w) for i, w in enumerate(chunk))
            self._call(body)
