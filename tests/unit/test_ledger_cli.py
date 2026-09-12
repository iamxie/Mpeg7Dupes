"""Ledger must never accept an ambiguous or unfinished completion record."""
import fcntl
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from test_loader import short_signature

BIN = os.environ.get('MPEG7DUPES')


@unittest.skipUnless(BIN, 'set MPEG7DUPES, or run make test')
class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ('a.bin', 'b.bin'):
            (self.root / name).write_bytes(short_signature(2))
        self.ledger = self.root / 'pairs.ledger'

    def tearDown(self):
        self.tmp.cleanup()

    def run_pair(self, *extra, first='a.bin'):
        return subprocess.run([BIN, '-j', '1', '-s', str(self.ledger),
                               *extra, first, 'b.bin'], cwd=self.root,
                              capture_output=True, text=True, timeout=15)

    def test_ambiguous_names_fail_before_ledger_is_written(self):
        for name in ('a\tb.bin', '#a.bin', 'a\nb.bin', 'a\rb.bin'):
            with self.subTest(name=name):
                (self.root / name).write_bytes(short_signature(2))
                done = self.run_pair(first=name)
                self.assertEqual(done.returncode, 64 if "\n" in name or "\r" in name else 1, done.stderr)
                self.assertIn('ledger', done.stderr.lower())
                self.assertFalse(self.ledger.exists())

    def test_close_double_values_are_different_runs(self):
        done = self.run_pair('-b', '0.5000001')
        self.assertEqual(done.returncode, 0, done.stderr)
        done = self.run_pair('-b', '0.5000002')
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('settings', done.stderr)

    def test_truncated_pair_input_and_run_are_discarded_before_append(self):
        for tail in (b'a.bin\tb.bin', b'#input\ta.bin\t999', b'#run\tversion=garbage'):
            with self.subTest(tail=tail):
                self.ledger.unlink(missing_ok=True)
                done = self.run_pair()
                self.assertEqual(done.returncode, 0, done.stderr)
                lines = self.ledger.read_bytes().splitlines(keepends=True)
                # Remove the completed pair, then simulate an interrupted append.
                prefix = b''.join(line for line in lines if line.startswith(b'#'))
                self.ledger.write_bytes(prefix + tail)
                done = self.run_pair()
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(self.ledger.read_bytes(), prefix + b'a.bin\tb.bin\n')
                done = self.run_pair()
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(self.ledger.read_bytes(), prefix + b'a.bin\tb.bin\n')

    def test_second_process_cannot_open_locked_ledger(self):
        with self.ledger.open('a+b') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            done = self.run_pair()
            self.assertEqual(done.returncode, 1, done.stderr)
            self.assertIn('locked', done.stderr.lower())
            self.assertEqual(self.ledger.read_bytes(), b'')

    def test_malformed_complete_records_are_not_silently_reused(self):
        for record in (b'a.bin\tb.bin\textra\n', b'#run\tversion=x\n',
                       b'#input\ta.bin\tnonsense\t0000000000000001\n'):
            with self.subTest(record=record):
                self.ledger.write_bytes(record)
                done = self.run_pair()
                self.assertEqual(done.returncode, 1, done.stderr)
                self.assertIn('Malformed', done.stderr)

    def test_comma_quote_and_unicode_paths_still_resume(self):
        name = '影片,"a".bin'
        (self.root / name).write_bytes(short_signature(2))
        for _ in range(2):
            done = self.run_pair(first=name)
            self.assertEqual(done.returncode, 0, done.stderr)
        pairs = [s for s in self.ledger.read_text().splitlines() if not s.startswith('#')]
        self.assertEqual(len(pairs), 1)



if __name__ == '__main__':
    unittest.main(verbosity=2)
