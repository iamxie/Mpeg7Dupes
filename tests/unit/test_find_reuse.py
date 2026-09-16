"""find_reuse.py run as a whole program, against the stand-ins in fakesig.py.

    sh tests/tools.sh          # or: uv run tests/unit/test_find_reuse.py

Nothing here decodes video or compares a signature. What is under test is the
contract of the command: which files end up in the record with which status,
that a failure is recorded and not only printed, that the exit status says
whether the run was complete, and that the terminal, the JSON and the page
agree about all of it.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import render_report  # noqa: E402
from fakesig import (fake_signature, signature_env, write_fake_mpeg7dupes,  # noqa: E402
                     write_fake_tools)

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "find_reuse.py"


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "src"
        self.src.mkdir()
        (self.src / "mine.mp4").write_bytes(b"my clip")
        self.comp = self.dir / "comp"
        self.comp.mkdir()
        (self.comp / "one.mp4").write_bytes(b"first candidate")
        (self.comp / "two.mp4").write_bytes(b"second candidate")
        self.template = self.dir / "template.sig"
        self.template.write_bytes(fake_signature(frames=63, segments=2))
        self.log = self.dir / "calls.log"
        self.ffmpeg, self.ffprobe = write_fake_tools(self.dir)
        self.m7d = write_fake_mpeg7dupes(self.dir)
        self.env = signature_env(self.template, self.log)

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *extra, **env_over):
        if '--analyze' not in extra and '--no-analysis' not in extra:
            extra = ('--no-analysis', *extra)
        env = dict(self.env)
        env.update(env_over)
        cmd = [sys.executable, str(SCRIPT), "--source", str(self.src),
               "--candidates", str(self.comp), "--sig-dir", str(self.dir / "sig"),
               "--no-crop-bars", "--ffmpeg", self.ffmpeg, "--ffprobe",
               self.ffprobe, "--mpeg7dupes", self.m7d, "--json",
               str(self.dir / "reuse.json"), *extra]
        return subprocess.run(cmd, capture_output=True, text=True, env=env,
                              cwd=self.dir)

    def record(self):
        return json.loads((self.dir / "reuse.json").read_text())

    def statuses(self, rec, role="candidates"):
        return {Path(v["path"]).name: v["status"] for v in rec[role]}

    def test_a_complete_run_exits_0_and_records_every_file(self):
        done = self.run_script("--show-misses")
        self.assertEqual(done.returncode, 0, done.stderr)
        rec = self.record()
        self.assertEqual(rec["schema"], "find_reuse/8")
        self.assertEqual(sorted(self.statuses(rec).values()), ["checked", "matched"])
        self.assertEqual(self.statuses(rec, "sources"), {"mine.mp4": "processed"})
        s = rec["summary"]
        self.assertTrue(s["complete"])
        self.assertEqual((s["candidates_requested"], s["candidates_matched"],
                          s["candidates_checked"], s["matches"]), (2, 1, 1, 1))
        hit = rec["matches"][0]
        self.assertEqual((hit["matchframes"], hit["source_frames"],
                          hit["candidate_frames"]), (60, 63, 63))
        self.assertAlmostEqual(hit["coverage_percent"], 95.2, places=1)
        self.assertEqual(hit["framerateratio"], 1.0)
        self.assertFalse(hit["overrun"])
        self.assertIn("used mine.mp4, starting at 00:06", done.stdout)
        self.assertIn("no match reaching the threshold, best 16%", done.stdout)
        self.assertIn("2 candidates against 1 source, 1 match over 40%", done.stdout)

    def test_a_candidate_that_cannot_be_read_is_recorded_and_exits_1(self):
        """This used to be a line on stderr and nothing else: the candidate
        was absent from the JSON and the run reported success."""
        (self.comp / "bad.mp4").write_bytes(b"not really a video")
        done = self.run_script(FAKE_BAD="bad.mp4")
        self.assertEqual(done.returncode, 1, done.stderr)
        rec = self.record()
        self.assertEqual(self.statuses(rec)["bad.mp4"], "failed")
        bad = next(c for c in rec["candidates"] if c["name"] == "bad.mp4")
        self.assertEqual(bad["failure"]["stage"], "fingerprint")
        self.assertIn("ffprobe", bad["failure"]["reason"])
        self.assertFalse(rec["summary"]["complete"])
        self.assertEqual(rec["summary"]["exit_status"], 1)
        self.assertEqual(rec["summary"]["candidates_failed"], 1)
        self.assertIn("bad.mp4", done.stdout)
        self.assertIn("could not be processed", done.stdout)
        self.assertIn("incomplete", done.stdout)
        # the rest of the run still happened
        self.assertEqual(rec["summary"]["matches"], 1)

    def test_a_failed_source_is_recorded_too(self):
        (self.src / "badsource.mp4").write_bytes(b"x")
        done = self.run_script(FAKE_BAD="badsource.mp4")
        self.assertEqual(done.returncode, 1, done.stderr)
        rec = self.record()
        self.assertEqual(self.statuses(rec, "sources")["badsource.mp4"], "failed")
        self.assertEqual(rec["summary"]["sources_failed"], 1)

    def test_no_match_is_still_a_complete_run(self):
        done = self.run_script(FAKE_M7D_FIRST="10")
        self.assertEqual(done.returncode, 0, done.stderr)
        rec = self.record()
        self.assertEqual(rec["matches"], [])
        self.assertEqual(set(self.statuses(rec).values()), {"checked"})
        self.assertIn("0 matches over 40%", done.stdout)

    def test_a_candidate_that_is_a_source_is_skipped_and_said(self):
        (self.comp / "mine.mp4").write_bytes(b"my clip")
        self.src = self.comp / "mine.mp4"
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        rec = self.record()
        self.assertEqual(self.statuses(rec)["mine.mp4"], "skipped")
        self.assertEqual(rec["summary"]["candidates_skipped"], 1)
        self.assertIn("skipped, it is one of the sources", done.stdout)

    def test_two_copies_of_a_source_are_both_reported(self):
        (self.src / "copy.mp4").write_bytes(b"my clip")
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        rec = self.record()
        self.assertEqual(sorted(v["name"] for v in rec["sources"]),
                         ["copy.mp4", "mine.mp4"])
        self.assertEqual(sorted(h["source"] for h in rec["matches"]),
                         ["copy.mp4", "mine.mp4"])

    def test_an_overrun_keeps_its_raw_numbers_and_the_reason(self):
        done = self.run_script(FAKE_M7D_FIRST="300")
        self.assertEqual(done.returncode, 0, done.stderr)
        hit = self.record()["matches"][0]
        self.assertTrue(hit["overrun"])
        self.assertEqual(hit["matchframes"], 300)
        self.assertEqual(hit["matched_seconds"], 60.0)
        self.assertIn("has only 63", hit["overrun_reason"])
        self.assertIn("position unreliable", done.stdout)

    def test_another_speed_ratio_is_carried_and_said(self):
        done = self.run_script(FAKE_M7D_RATIO="1.233333")
        hit = self.record()["matches"][0]
        self.assertAlmostEqual(hit["framerateratio"], 1.2333, places=3)
        self.assertIn("speed ratio 1.23", done.stdout)
        self.assertIn("slower clip", done.stdout)

    def calls(self):
        """The comparisons the stand-in was asked for, --version aside."""
        return [line for line in self.log.read_text().splitlines()
                if line.startswith("mpeg7dupes ") and "--version" not in line]

    def test_the_coarse_filter_stays_on_by_default(self):
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(self.calls())
        for call in self.calls():
            self.assertIn("-d 9000", call)
            self.assertIn("-c 60000", call)
        self.assertIs(self.record()["settings"]["coarse_filter"], True)

    def test_no_coarse_filter_passes_d_10001_and_says_so(self):
        """The switch exists to measure whether the filter loses a match, so a
        run made with it off has to say so wherever its results end up."""
        done = self.run_script("--no-coarse-filter")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(self.calls())
        for call in self.calls():
            self.assertIn("-d 10001", call)
        rec = self.record()
        self.assertIs(rec["settings"]["coarse_filter"], False)
        self.assertIn("coarse filter off", done.stdout)
        self.assertIn("Coarse filter</dt><dd>off", render_report.render(rec, []))

    def test_mpeg7dupes_failing_is_exit_2(self):
        done = self.run_script(FAKE_M7D_FAIL="1")
        self.assertEqual(done.returncode, 2)
        self.assertIn("mpeg7dupes failed", done.stderr)

    def test_a_missing_program_is_exit_2(self):
        done = self.run_script("--mpeg7dupes", "/nonexistent/mpeg7dupes")
        self.assertEqual(done.returncode, 2)

    def test_no_candidates_is_exit_2(self):
        done = self.run_script("--candidates", str(self.dir / "empty"))
        self.assertEqual(done.returncode, 2)

    def test_the_page_agrees_with_the_record_and_the_terminal(self):
        (self.comp / "bad.mp4").write_bytes(b"not really a video")
        done = self.run_script(FAKE_BAD="bad.mp4")
        rec = self.record()
        page = render_report.render(rec, [])
        hit = rec["matches"][0]
        self.assertIn(hit["candidate_path"], page)
        self.assertIn("starting at 00:06", page)
        self.assertIn("starting at 00:06", done.stdout)
        self.assertIn("bad.mp4", page)
        self.assertIn("could not be processed", page)
        self.assertIn("1 match from 1 source against 3 candidates, 1 not processed", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
