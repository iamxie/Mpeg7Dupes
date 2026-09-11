"""Unit tests for making one signature.

    sh tests/tools.sh          # or: uv run tests/unit/test_sigmake.py

ffmpeg and ffprobe are shell stand-ins from fakesig.py, so nothing here
decodes video; what is under test is the flow around them: what is written
where, what is accepted, what the index records, and which tool is called.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sigmake  # noqa: E402
import sigstore  # noqa: E402
from fakesig import fake_signature, signature_env, write_fake_tools  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.sig_dir = self.dir / "sig"
        self.sig_dir.mkdir()
        self.template = self.dir / "template.sig"
        self.template.write_bytes(fake_signature(frames=63, segments=2))
        self.log = self.dir / "calls.log"
        self.ffmpeg, self.ffprobe = write_fake_tools(self.dir)
        self.saved_environ = dict(os.environ)
        os.environ.update(signature_env(self.template, self.log))
        self.con = sigstore.open_db(self.dir / "index.sqlite")

    def tearDown(self):
        self.con.close()
        os.environ.clear()
        os.environ.update(self.saved_environ)
        self.tmp.cleanup()

    def a_video(self, name="a.mp4", data=b"video bytes"):
        path = self.dir / name
        path.write_bytes(data)
        return path

    def calls(self, tool):
        """Invocations of one stand-in. For ffmpeg only the productions: the
        real detector, when a test lets it run, samples frames through ffmpeg
        as well, and those are not signatures."""
        if not self.log.exists():
            return []
        return [line for line in self.log.read_text().splitlines()
                if line.startswith(tool + " ")
                and (tool != "ffmpeg" or "signature=filename=" in line)]

    def make(self, video, **over):
        args = dict(fps=5.0, crop_bars=False, detector="", ffmpeg=self.ffmpeg,
                    ffprobe=self.ffprobe, ffmpeg_version="fake ffmpeg")
        args.update(over)
        return sigmake.make(video, self.con, self.sig_dir, **args)


class Produce(Base):
    def test_a_good_run_puts_a_whole_signature_in_place(self):
        video = self.a_video()
        header = sigmake.produce(video, self.sig_dir, "out.sig", "", 5.0, self.ffmpeg)
        self.assertEqual(header["frames"], 63)
        self.assertEqual((self.sig_dir / "out.sig").read_bytes(),
                         self.template.read_bytes())
        self.assertEqual([p.name for p in self.sig_dir.iterdir()], ["out.sig"])

    def test_ffmpeg_writes_to_a_temporary_name_not_the_final_one(self):
        sigmake.produce(self.a_video(), self.sig_dir, "out.sig", "", 5.0, self.ffmpeg)
        call = self.calls("ffmpeg")[0]
        self.assertIn("signature=filename=.out.sig.", call)
        self.assertIn(".part", call)

    def test_a_failed_run_leaves_nothing_and_keeps_the_old_signature(self):
        (self.sig_dir / "out.sig").write_bytes(b"the previous good signature")
        os.environ["FAKE_FFMPEG"] = "fail"
        with self.assertRaises(sigmake.SignatureError) as caught:
            sigmake.produce(self.a_video(), self.sig_dir, "out.sig", "", 5.0, self.ffmpeg)
        self.assertIn("exited 1", str(caught.exception))
        self.assertEqual((self.sig_dir / "out.sig").read_bytes(),
                         b"the previous good signature")
        self.assertEqual([p.name for p in self.sig_dir.iterdir()], ["out.sig"])

    def test_a_truncated_result_is_refused_and_the_old_signature_kept(self):
        """ffmpeg exited 0 and left a file that is not a whole signature.
        Written straight to the final name, this was a cache hit later."""
        (self.sig_dir / "out.sig").write_bytes(b"the previous good signature")
        os.environ["FAKE_FFMPEG"] = "truncate"
        with self.assertRaises(sigmake.SignatureError) as caught:
            sigmake.produce(self.a_video(), self.sig_dir, "out.sig", "", 5.0, self.ffmpeg)
        self.assertIn("no usable signature", str(caught.exception))
        self.assertEqual((self.sig_dir / "out.sig").read_bytes(),
                         b"the previous good signature")
        self.assertEqual([p.name for p in self.sig_dir.iterdir()], ["out.sig"])

    def test_an_empty_result_is_refused(self):
        os.environ["FAKE_FFMPEG"] = "empty"
        with self.assertRaises(sigmake.SignatureError):
            sigmake.produce(self.a_video(), self.sig_dir, "out.sig", "", 5.0, self.ffmpeg)
        self.assertEqual(list(self.sig_dir.iterdir()), [])

    def test_the_crop_rides_in_front_of_the_fps_filter(self):
        sigmake.produce(self.a_video(), self.sig_dir, "out.sig",
                        "crop=iw:180:0:40", 5.0, self.ffmpeg)
        self.assertIn("-vf crop=iw:180:0:40,fps=5.0,signature=filename=",
                      self.calls("ffmpeg")[0])

    def test_hwaccel_is_passed_when_asked_for(self):
        sigmake.produce(self.a_video(), self.sig_dir, "out.sig", "", 5.0,
                        self.ffmpeg, hwaccel="videotoolbox")
        self.assertIn("-hwaccel videotoolbox", self.calls("ffmpeg")[0])


class Probe(Base):
    def test_reads_the_duration(self):
        self.assertEqual(sigmake.probe_duration(self.ffprobe, self.a_video()), 12.5)

    def test_a_failing_probe_is_a_signature_error(self):
        os.environ["FAKE_PROBE"] = "fail"
        with self.assertRaises(sigmake.SignatureError):
            sigmake.probe_duration(self.ffprobe, self.a_video())

    def test_no_duration_is_a_signature_error(self):
        os.environ["FAKE_DURATION"] = "N/A"
        with self.assertRaises(sigmake.SignatureError):
            sigmake.probe_duration(self.ffprobe, self.a_video())


class Decide(Base):
    """The detector is stubbed: what is under test is the mapping from what it
    says to a decision, and that it is handed the caller's tools."""

    def stub(self, result=None, raises=None):
        seen = {}

        def analyse(path, **kw):
            seen.update(kw)
            if raises:
                raise raises
            return result

        sigmake.detect_bars = SimpleNamespace(
            analyse=analyse, POINTS=6, PER_POINT=12, THRESHOLD=5.0,
            MIN_FRACTION=0.02, DETECTOR_VERSION="1")
        return seen

    def tearDown(self):
        import detect_bars
        sigmake.detect_bars = detect_bars
        super().tearDown()

    def decide(self, crop_bars=True):
        return sigmake.decide_crop(self.a_video(), crop_bars, "/my/ffmpeg", "/my/ffprobe")

    def test_disabled_asks_nothing(self):
        seen = self.stub({"status": "ok", "bar_fraction": 0.2})
        self.assertEqual(self.decide(crop_bars=False).state, "disabled")
        self.assertEqual(seen, {})

    def test_bars_found(self):
        self.stub({"status": "ok", "bar_fraction": 0.2, "picture_height": 180,
                   "top": 40, "bottom": 40, "height": 260})
        decision = self.decide()
        self.assertEqual(decision.state, "detected")
        self.assertEqual(decision.crop, "crop=iw:180:0:40")

    def test_no_bars(self):
        self.stub({"status": "ok", "bar_fraction": 0.0, "picture_height": 260,
                   "top": 0, "bottom": 0, "height": 260})
        decision = self.decide()
        self.assertEqual((decision.state, decision.crop), ("none", ""))

    def test_cannot_tell_is_uncertain_not_none(self):
        """A video too still to read keeps an uncropped signature, and the
        index has to say that is why."""
        self.stub({"status": "too static", "height": 260, "middle": 1.0})
        self.assertEqual(self.decide().state, "uncertain")
        self.stub({"status": "too short", "height": 260})
        self.assertEqual(self.decide().state, "uncertain")

    def test_unreadable_is_a_failure_not_no_bars(self):
        self.stub({"status": "unreadable"})
        decision = self.decide()
        self.assertEqual(decision.state, "failed")
        self.assertIn("cannot read", decision.detail)

    def test_a_raising_detector_is_a_failure(self):
        self.stub(raises=RuntimeError("boom"))
        self.assertEqual(self.decide().state, "failed")

    def test_the_detector_gets_the_callers_tools(self):
        """It used to take whatever was on PATH, so --ffmpeg changed which
        ffmpeg fingerprinted a video and not which one looked for bars."""
        seen = self.stub({"status": "ok", "bar_fraction": 0.0, "picture_height": 1,
                          "top": 0, "bottom": 0, "height": 1})
        self.decide()
        self.assertEqual((seen["ffmpeg"], seen["ffprobe"]), ("/my/ffmpeg", "/my/ffprobe"))
        self.assertEqual((seen["points"], seen["per_point"]), (6, 12))

    def test_a_failed_decision_fails_the_file(self):
        self.stub({"status": "unreadable"})
        with self.assertRaises(sigmake.SignatureError) as caught:
            sigmake.build(self.a_video(), self.sig_dir, content_hash="abc",
                          fps=5.0, crop_bars=True, detector="1",
                          ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)
        self.assertIn("bar detection failed", str(caught.exception))
        self.assertEqual(list(self.sig_dir.iterdir()), [])


class Make(Base):
    def test_first_time_produces_and_records(self):
        video = self.a_video()
        made = self.make(video)
        self.assertTrue(made.produced)
        self.assertEqual(made.frames, 63)
        self.assertEqual(made.duration, 12.5)
        self.assertEqual(made.crop_state, "disabled")
        self.assertTrue(made.path.is_file())
        self.assertEqual(len(self.calls("ffmpeg")), 1)
        row = self.con.execute("SELECT crop_state, frames FROM sigs").fetchone()
        self.assertEqual(row, ("disabled", 63))

    def test_second_time_reuses_without_ffmpeg_or_reading_the_video(self):
        video = self.a_video()
        first = self.make(video)
        again = self.make(video)
        self.assertFalse(again.produced)
        self.assertEqual(again.path, first.path)
        self.assertEqual(again.frames, 63)
        self.assertEqual(len(self.calls("ffmpeg")), 1)
        self.assertEqual(len(self.calls("ffprobe")), 1)

    def test_a_renamed_copy_reuses_the_signature(self):
        video = self.a_video()
        first = self.make(video)
        moved = video.with_name("renamed.mp4")
        video.rename(moved)
        again = self.make(moved)
        self.assertFalse(again.produced)
        self.assertEqual(again.filename, first.filename)
        self.assertEqual(len(self.calls("ffmpeg")), 1)

    def test_same_size_rewrite_within_the_second_gets_its_own_signature(self):
        video = self.a_video(data=b"version one")
        first = self.make(video)
        stat = video.stat()
        video.write_bytes(b"version two")
        os.utime(video, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000))
        again = self.make(video)
        self.assertTrue(again.produced)
        self.assertNotEqual(again.filename, first.filename)

    def test_other_settings_are_other_signatures(self):
        video = self.a_video()
        self.make(video)
        self.assertTrue(self.make(video, fps=3.0).produced)
        self.assertTrue(self.make(video, crop_bars=True, detector="1").produced)
        self.assertEqual(len(self.calls("ffmpeg")), 3)

    def test_a_failed_overwrite_keeps_the_old_signature_and_row(self):
        video = self.a_video()
        first = self.make(video)
        before = first.path.read_bytes()
        os.environ["FAKE_FFMPEG"] = "fail"
        with self.assertRaises(sigmake.SignatureError):
            self.make(video, overwrite=True)
        self.assertEqual(first.path.read_bytes(), before)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sigs").fetchone(), (1,))
        os.environ["FAKE_FFMPEG"] = "ok"
        self.assertFalse(self.make(video).produced, "and it is still a hit")

    def test_a_row_without_its_file_is_made_again(self):
        video = self.a_video()
        first = self.make(video)
        first.path.unlink()
        self.assertTrue(self.make(video).produced)

    def test_a_truncated_file_in_the_store_is_made_again(self):
        video = self.a_video()
        first = self.make(video)
        first.path.write_bytes(first.path.read_bytes()[:100])
        self.assertTrue(self.make(video).produced)
        self.assertEqual(first.path.read_bytes(), self.template.read_bytes())

    def test_a_file_without_its_row_is_made_again(self):
        """The rename lands before the row; an interruption between the two
        leaves a whole file the index cannot vouch for."""
        video = self.a_video()
        first = self.make(video)
        self.con.execute("DELETE FROM sigs")
        self.assertTrue(self.make(video).produced)

    def test_a_video_that_cannot_be_read_raises_oserror(self):
        with self.assertRaises(OSError):
            self.make(self.dir / "missing.mp4")

    def test_uncertain_bars_keep_an_uncropped_signature_and_say_so(self):
        import detect_bars
        sigmake.detect_bars = SimpleNamespace(
            analyse=lambda path, **kw: {"status": "too static", "height": 1, "middle": 0},
            POINTS=6, PER_POINT=12, THRESHOLD=5.0, MIN_FRACTION=0.02,
            DETECTOR_VERSION="1")
        try:
            made = self.make(self.a_video(), crop_bars=True, detector="1")
        finally:
            sigmake.detect_bars = detect_bars
        self.assertEqual((made.crop_state, made.crop), ("uncertain", ""))
        self.assertIn("-vf fps=5.0,", self.calls("ffmpeg")[0])
        self.assertEqual(self.con.execute("SELECT crop_state FROM sigs").fetchone(),
                         ("uncertain",))


if __name__ == "__main__":
    unittest.main(verbosity=2)
