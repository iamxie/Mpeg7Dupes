"""Result interpretation must survive cache hits and moving the HTML output."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote
import re
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr
import io

import test_find_reuse as fixture
import render_report
import find_reuse
import sigstore
import sigmake


class Record(fixture.Run):
    # Reuse the CLI fixture without running the parent's cases a second time.
    def setUp(self):
        super().setUp()
        path = Path(self.ffmpeg)
        path.write_text(path.read_text().replace('#!/bin/sh\n', '#!/bin/sh\n'
            'if [ "$1" = -version ]; then echo "ffmpeg test generator"; exit 0; fi\n'))
    def test_provenance_and_warnings_survive_hot_cache(self):
        first = self.run_script("--show-misses")
        self.assertEqual(first.returncode, 0, first.stderr)
        cold = self.record()
        self.assertEqual(cold["path_base"], str(self.dir))
        self.assertEqual(cold["tool"]["binary_sha256"],
                         hashlib.sha256(Path(self.m7d).read_bytes()).hexdigest())
        flags = cold["settings"]["comparison_args"]
        for flag, value in (("-b", "0.1"), ("-d", "9000"), ("-c", "60000")):
            self.assertEqual(flags[flags.index(flag) + 1], value)
        for video in cold["sources"] + cold["candidates"]:
            sig = video["signature"]
            self.assertEqual(sig["sha256"], hashlib.sha256(
                (self.dir / "sig" / sig["filename"]).read_bytes()).hexdigest())
            self.assertTrue(video["content_hash"])
            self.assertTrue(sig["ffmpeg"])
        self.assertEqual(cold["sources"][0]["warnings"][0]["code"], "short_source")
        # Simulate an older generator in the existing cache. The next scan
        # must describe that generator, not today's ffmpeg on PATH.
        with sqlite3.connect(self.dir / "sig" / "index.sqlite") as con:
            con.execute("UPDATE sigs SET ffmpeg='original generator'")
        calls = self.log.read_text().count("signature=filename=")
        hot_run = self.run_script("--show-misses")
        self.assertEqual(hot_run.returncode, 0, hot_run.stderr)
        hot = self.record()
        self.assertEqual(calls, self.log.read_text().count("signature=filename="), "hot cache decoded video")
        self.assertEqual(cold["limits"], hot["limits"])
        self.assertEqual(cold["sources"][0]["warnings"], hot["sources"][0]["warnings"])
        self.assertTrue(all(v["signature"]["ffmpeg"] == "original generator"
                            for v in hot["sources"] + hot["candidates"]))
        self.assertIn("no match reaching the threshold", hot_run.stdout)
        page = render_report.render(hot, [])
        for phrase in ("simple light/dark", "Low-motion", "reframe", "not enabled"):
            self.assertIn(phrase, page)

    def test_all_crop_states_and_relative_media(self):
        for path in self.comp.glob("*.mp4"):
            path.rename(path.with_name("影片 #?:x\\" + path.name))
        done = self.run_script("--source", "src", "--candidates", "comp")
        self.assertEqual(done.returncode, 0, done.stderr)
        record = self.record()
        for state, label in (("disabled", "not enabled"), ("detected", "detected"),
                             ("none", "none detected"), ("uncertain", "uncertain")):
            record["sources"][0]["crop_state"] = state
            record["sources"][0]["crop"] = "crop=iw:80:0:10" if state == "detected" else ""
            page = render_report.render(record, [])
            self.assertIn("Bars: " + label, page)
            if state == "uncertain":
                self.assertIn("barred copies may be missed", page)
        out = self.dir / "elsewhere" / "report.html"
        out.parent.mkdir()
        rendered = subprocess.run([sys.executable, str(Path(render_report.__file__)),
            str(self.dir / "reuse.json"), "--out", str(out)],
            cwd=self.dir.parent, capture_output=True, text=True)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertNotIn("blank", rendered.stderr)
        page = out.read_text()
        for src in re.findall(r'<video src="([^"]+)"', page):
            self.assertTrue((out.parent / unquote(src.split("#")[0])).exists(), src)
        self.assertIn('class="path">src/mine.mp4', page)
        record["schema"] = "find_reuse/4"
        record.pop("path_base")
        legacy = self.dir / "legacy.json"
        legacy.write_text(json.dumps(record))
        rendered = subprocess.run([sys.executable, str(Path(render_report.__file__)),
            str(legacy), "--out", str(out), "--path-base", str(self.dir)],
            cwd=self.dir.parent, capture_output=True, text=True)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertNotIn("blank", rendered.stderr)
        for src in re.findall(r'<video src="([^"]+)"', out.read_text()):
            self.assertTrue((out.parent / unquote(src.split("#")[0])).exists(), src)

    def test_short_video_warns_even_with_a_longer_container_duration(self):
        done = self.run_script(FAKE_DURATION="180")
        self.assertEqual(done.returncode, 0, done.stderr)
        source = self.record()["sources"][0]
        self.assertEqual(source["seconds"], 180)
        self.assertIn("short_source", [w["code"] for w in source["warnings"]])
        self.assertIn("simple light/dark", done.stderr)

    def test_incomplete_empty_report_does_not_claim_a_threshold_result(self):
        self.run_script(FAKE_M7D_FAIL="1")
        page = render_report.render(self.record(), [])
        self.assertIn("No completed match", page)
        self.assertNotIn("No candidate reached", page)

    def test_crop_decisions_are_identical_on_cold_and_hot_paths(self):
        for state in ("disabled", "detected", "none", "uncertain"):
            with self.subTest(state=state):
                directory = self.dir / state
                directory.mkdir()
                con = sigstore.open_db(directory / "index.sqlite")
                settings = dict(find_reuse.DEFAULTS, crop_bars=state != "disabled",
                                ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)
                crop = sigmake.CropDecision(state, "crop=iw:80:0:10" if state == "detected" else "")
                def make():
                    out, err = io.StringIO(), io.StringIO()
                    with redirect_stdout(out), redirect_stderr(err):
                        entry = find_reuse.make_signature(self.src / "mine.mp4", con,
                            directory, settings, "3", "original ffmpeg", [], "source")
                    return entry, out.getvalue(), err.getvalue()
                try:
                    with patch.dict(os.environ, self.env), patch.object(sigmake, "decide_crop", return_value=crop):
                        cold = make()
                    with patch.object(sigmake, "build", side_effect=AssertionError("cache miss")):
                        hot = make()
                    self.assertIsNotNone(cold[0])
                    self.assertEqual(cold, hot)
                finally:
                    con.close()


# Only inherit setup/helpers; the original CLI cases run in their own module.
for name in dir(fixture.Run):
    if name.startswith("test_"):
        setattr(Record, name, None)

if __name__ == "__main__":
    unittest.main()
