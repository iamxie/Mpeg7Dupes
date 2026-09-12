"""Real binary import contract; no ffmpeg needed. make test supplies the binary."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

BIN = os.environ.get("MPEG7DUPES")
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/base.bin"


def field(data, offset, count, value):
    for i in range(count):
        pos = offset + i
        mask = 128 >> (pos % 8)
        data[pos // 8] = (data[pos // 8] & ~mask) | (
            mask if value & (1 << (count - i - 1)) else 0)


def short_signature(frames, start=0):
    data = bytearray((274 + 1344 + 1 + frames * 689 + 7) // 8)
    for offset, count, value in [
        (0, 32, 1), (32, 1, 1), (65, 16, 63), (81, 16, 63),
        (129, 32, frames), (161, 16, 5), (177, 1, 1),
        (178, 32, start), (210, 32, start + frames - 1), (242, 32, 1),
        (306, 32, frames - 1), (338, 1, 1), (339, 32, start),
        (371, 32, start + frames - 1),
    ]:
        field(data, offset, count, value)
    for i in range(frames):
        pos = 274 + 1344 + 1 + i * 689
        field(data, pos, 1, 1)
        field(data, pos + 1, 32, start + i)
        field(data, pos + 33, 8, 255)
    return data


@unittest.skipUnless(BIN, "set MPEG7DUPES, or run make test")
class Loader(unittest.TestCase):
    def run_pair(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("first.bin", "second.bin"):
                (root / name).write_bytes(data)
            return subprocess.run([BIN, "-j", "1", "first.bin", "second.bin"],
                                  cwd=root, capture_output=True, text=True, timeout=15)

    def test_one_and_two_frames_are_safe_and_need_not_match(self):
        for frames in (1, 2):
            with self.subTest(frames=frames):
                r = self.run_pair(short_signature(frames))
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(len(r.stdout.splitlines()), 1)

    def test_unsigned_pts_above_two_to_the_31(self):
        r = self.run_pair(short_signature(2, (1 << 31) + 10))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unsupported_format_and_invalid_data_are_named(self):
        # Two frames, one segment: every mutation leaves the length unchanged.
        for name, offset, count, value in [
            ("regions", 0, 32, 2), ("spatial flag", 32, 1, 0),
            ("region time flag", 177, 1, 0),
            ("segment time flag", 338, 1, 0),
            ("coarse index", 306, 32, 2),
            ("coarse time", 339, 32, 4),
            ("compression", 1618, 1, 1),
            ("frame time flag", 1619, 1, 0),
            ("frame time", 1620, 32, 99),
            ("word", 1660, 8, 255), ("ternary", 1700, 8, 255),
        ]:
            with self.subTest(name=name):
                data = short_signature(2)
                field(data, offset, count, value)
                r = self.run_pair(data)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn("Cannot use signature first.bin", r.stderr)
                self.assertNotIn("AddressSanitizer", r.stderr)

    def test_oversized_reader_input_is_refused_before_allocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.bin"
            with path.open("wb") as f:
                f.truncate((2**31 - 1) // 8 + 1)
            r = subprocess.run([BIN, "-j", "1", str(path), str(FIXTURE)],
                               capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("too large", r.stderr)
            self.assertIn(str(path), r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
