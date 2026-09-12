"""Single-source -n distributes real pairs, including ones with no CSV row."""
import csv
import io
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

BIN = os.environ.get("MPEG7DUPES")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@unittest.skipUnless(BIN, "set MPEG7DUPES, or run make test")
class Parallel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copyfile(FIXTURES / "base.bin", self.root / "source.bin")
        self.names = [f"candidate-{i}.bin" for i in range(12)]
        for i, name in enumerate(self.names):
            shutil.copyfile(FIXTURES / ("unrelated.bin" if i % 3 == 0 else "base.bin"), self.root / name)
        (self.root / "source.list").write_text("source.bin\n")
        (self.root / "candidates.list").write_text("\n".join(self.names) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_scan(self, jobs, ledger, **kw):
        return subprocess.run([BIN, "-vv", "-j", str(jobs), "-n", "source.list",
            "-l", "candidates.list", "-s", ledger], cwd=self.root,
            stderr=subprocess.PIPE, text=True, timeout=60,
            **({"stdout": subprocess.PIPE} | kw))

    def pairs(self, name):
        return [line for line in (self.root / name).read_text().splitlines()
                if not line.startswith("#")]

    def test_worker_distribution_csv_equivalence_and_resume(self):
        serial = self.run_scan(1, "serial.ledger")
        parallel = self.run_scan(4, "parallel.ledger")
        for run in (serial, parallel):
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        traces = re.findall(r"Worker (\d+): pair (\d+),(\d+)", parallel.stderr)
        self.assertEqual(sorted((int(i), int(j)) for _, i, j in traces),
                         [(0, j) for j in range(1, 13)])
        if os.cpu_count() > 1:
            self.assertGreater(len({worker for worker, _, _ in traces}), 1)
        self.assertEqual(sorted(serial.stdout.splitlines()), sorted(parallel.stdout.splitlines()))
        expected = {name + "\tsource.bin" for name in self.names}
        self.assertEqual(set(self.pairs("parallel.ledger")), expected)
        self.assertEqual(len(self.pairs("parallel.ledger")), len(expected))
        self.assertEqual(set(self.pairs("serial.ledger")), expected)
        rows = list(csv.DictReader(io.StringIO(parallel.stdout)))
        self.assertLess(len(rows), len(expected), "fixture must include no-match completions")
        original = (self.root / "parallel.ledger").read_text().splitlines()
        saved = self.pairs("parallel.ledger")[::2]
        (self.root / "parallel.ledger").write_text("\n".join(
            [line for line in original if line.startswith("#")] + saved) + "\n")
        resumed = self.run_scan(4, "parallel.ledger")
        self.assertEqual(resumed.returncode, 0, resumed.stderr[-2000:])
        self.assertEqual(set(self.pairs("parallel.ledger")), expected)
        self.assertEqual(len(self.pairs("parallel.ledger")), len(expected))
        resumed_rows = list(csv.DictReader(io.StringIO(resumed.stdout)))
        self.assertEqual(sorted(resumed_rows, key=lambda r: r["Second signature"]),
                         sorted([r for r in rows if r["Second signature"] + "\tsource.bin" not in saved],
                                key=lambda r: r["Second signature"]))
        finished = self.run_scan(4, "parallel.ledger")
        self.assertEqual(finished.returncode, 0, finished.stderr[-2000:])
        self.assertNotIn("Worker ", finished.stderr)
        self.assertEqual(len(finished.stdout.splitlines()), 1)

    def test_output_failure_does_not_record_pairs(self):
        with open("/dev/full", "w") as output:
            done = self.run_scan(4, "failed.ledger", stdout=output)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.pairs("failed.ledger"), [])


if __name__ == "__main__":
    unittest.main()
