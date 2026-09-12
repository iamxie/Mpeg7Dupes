"""Unit tests for the Python tools.

    sh tests/tools.sh          # or: uv run tests/unit/test_tools.py

Covers the pure functions behind the JSON record and the HTML report. Nothing
here runs ffmpeg or mpeg7dupes; the parts that shell out are covered by the
suites that have a binary to point at.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import find_reuse  # noqa: E402
import render_report  # noqa: E402


def a_match(**over):
    """A match record with every field, so a test can vary one thing."""
    base = {
        "source": "S1.mp4", "source_path": "source/S1.mp4",
        "candidate": "C1.mp4", "candidate_path": "comp/C1.mp4",
        "fps": 5.0, "matchframes": 350, "source_frames": 350,
        "candidate_frames": 9200, "coverage_percent": 100.0,
        "framerateratio": 1.0, "matched_seconds": 70.0,
        "source_seconds": 70.0, "candidate_seconds": 1840.0,
        "source_crop": "", "candidate_crop": "",
        "start_seconds": 173.0, "end_seconds": 243.0,
        "source_begin_seconds": 0.0, "source_end_seconds": 70.0,
        "whole": 1, "overrun": False, "overrun_reason": "",
    }
    base.update(over)
    return base


class WhereOf(unittest.TestCase):
    def test_exact_start_is_one_timestamp(self):
        self.assertEqual(find_reuse.where_of(a_match()), "starting at 02:53")

    def test_another_speed_is_said_with_what_it_means(self):
        """Any ratio but 1.0 is reported with the ratio and what it does to
        the numbers: the coverage counts the slower clip's frames and the
        positions hold to the ratio's grid."""
        where = find_reuse.where_of(a_match(framerateratio=1.2333))
        self.assertIn("02:53", where)
        self.assertIn("1.23", where)
        self.assertIn("slower clip", where)
        self.assertIn("grid", where)

    def test_overrun_refuses_to_give_a_position(self):
        """A match longer than the video it was found in describes nothing."""
        where = find_reuse.where_of(a_match(overrun=True))
        self.assertIn("unreliable", where)
        self.assertNotIn("02:53", where)

    def test_hours_are_shown(self):
        self.assertEqual(find_reuse.where_of(a_match(start_seconds=3725.0)),
                         "starting at 1:02:05")


class VideoList(unittest.TestCase):
    def entry(self, seconds):
        return {"seconds": seconds, "frames": int(seconds * 5), "crop": ""}

    def test_rows_are_processed(self):
        got = find_reuse.video_list({"a.bin": self.entry(10.0)},
                                    {"a.bin": ["comp/a.mp4"]}, 5.0)
        self.assertEqual(got[0]["status"], "processed")

    def test_sorted_by_path(self):
        entries = {"b.bin": self.entry(10.0), "a.bin": self.entry(20.0)}
        paths = {"b.bin": ["comp/a.mp4"], "a.bin": ["comp/z.mp4"]}
        got = find_reuse.video_list(entries, paths, 5.0)
        self.assertEqual([v["path"] for v in got], ["comp/a.mp4", "comp/z.mp4"])

    def test_name_comes_from_the_path(self):
        got = find_reuse.video_list({"x.bin": self.entry(10.0)},
                                    {"x.bin": "some/where/Clip One.mp4"}, 5.0)
        self.assertEqual(got[0]["name"], "Clip One.mp4")

    def test_entry_without_a_path_is_dropped(self):
        """A signature that failed has an index entry but was never shown."""
        entries = {"a.bin": self.entry(10.0), "orphan.bin": self.entry(5.0)}
        got = find_reuse.video_list(entries, {"a.bin": "comp/a.mp4"}, 5.0)
        self.assertEqual(len(got), 1)

    def test_every_path_sharing_a_signature_gets_a_row(self):
        """Identical videos share one signature but are separate real files,
        and a report that named only the first would drop the rest."""
        entries = {"a.bin": self.entry(10.0)}
        paths = {"a.bin": ["comp/one.mp4", "comp/two.mp4"]}
        got = find_reuse.video_list(entries, paths, 5.0)
        self.assertEqual([v["path"] for v in got],
                         ["comp/one.mp4", "comp/two.mp4"])

    def test_fps_comes_from_the_run_not_the_entry(self):
        """Every signature in one run is taken at the same rate; that is what
        makes them comparable at all."""
        got = find_reuse.video_list({"a.bin": self.entry(10.0)},
                                    {"a.bin": ["comp/a.mp4"]}, 3.0)
        self.assertEqual(got[0]["fps"], 3.0)


class BuildRecord(unittest.TestCase):
    def settings(self):
        return {"fps": 5.0, "thxh": 290, "min_coverage": 40.0,
                "crop_bars": True, "coarse_filter": True,
                "mpeg7dupes": "/nonexistent/mpeg7dupes"}

    def test_carries_the_schema_and_settings(self):
        rec = find_reuse.build_record(self.settings(), [], [], [], [])
        self.assertEqual(rec["schema"], find_reuse.SCHEMA)
        self.assertEqual(rec["settings"]["thxh"], 290)
        self.assertEqual(rec["settings"]["mode"], "longest")
        self.assertIs(rec["settings"]["coarse_filter"], True)

    def test_missing_binary_leaves_the_version_empty(self):
        """Provenance is worth recording, but not worth failing the run for."""
        rec = find_reuse.build_record(self.settings(), [], [], [], [])
        self.assertEqual(rec["tool"]["mpeg7dupes"], "")

    def test_misses_are_sorted(self):
        rec = find_reuse.build_record(self.settings(), [], [], [],
                                      ["comp/z.mp4", "comp/a.mp4"])
        self.assertEqual(rec["misses"], ["comp/a.mp4", "comp/z.mp4"])

    def test_round_trips_through_json(self):
        rec = find_reuse.build_record(self.settings(), [], [], [a_match()], [])
        again = json.loads(json.dumps(rec))
        self.assertEqual(again["matches"][0]["start_seconds"], 173.0)

    def test_states_its_limits(self):
        rec = find_reuse.build_record(self.settings(), [], [], [], [])
        self.assertIn("re-thresholded", rec["limits"]["matches"])
        self.assertIn("longest", rec["limits"]["shape"])

    def test_summary_counts_files_by_what_happened(self):
        sources = [{"path": "s/a.mp4", "status": "processed"},
                   {"path": "s/b.mp4", "status": "failed",
                    "failure": {"stage": "read", "reason": "gone"}}]
        candidates = [{"path": "c/1.mp4", "status": "matched"},
                      {"path": "c/2.mp4", "status": "checked"},
                      {"path": "c/3.mp4", "status": "failed",
                       "failure": {"stage": "fingerprint", "reason": "x"}},
                      {"path": "c/4.mp4", "status": "skipped"}]
        rec = find_reuse.build_record(self.settings(), sources, candidates,
                                      [a_match()], [])
        s = rec["summary"]
        self.assertFalse(s["complete"])
        self.assertEqual(s["exit_status"], 1)
        self.assertEqual((s["sources_requested"], s["sources_failed"]), (2, 1))
        self.assertEqual((s["candidates_matched"], s["candidates_checked"],
                          s["candidates_failed"], s["candidates_skipped"]),
                         (1, 1, 1, 1))

    def test_a_complete_run_with_no_match_is_complete(self):
        rec = find_reuse.build_record(
            self.settings(), [{"path": "s/a.mp4", "status": "processed"}],
            [{"path": "c/1.mp4", "status": "checked"}], [], ["c/1.mp4"])
        self.assertTrue(rec["summary"]["complete"])
        self.assertEqual(rec["summary"]["exit_status"], 0)


class MediaSrc(unittest.TestCase):
    def test_fragment_carries_the_seek(self):
        self.assertEqual(render_report.media_src("comp/C1.mp4", 168.0),
                         "comp/C1.mp4#t=168")

    def test_zero_gets_no_fragment(self):
        self.assertEqual(render_report.media_src("source/S1.mp4"),
                         "source/S1.mp4")

    def test_negative_seek_is_clamped_away(self):
        """A match starting inside the lead-in must not produce #t=-2."""
        self.assertEqual(render_report.media_src("comp/C1.mp4", -2.0),
                         "comp/C1.mp4")

    def test_spaces_and_cjk_are_quoted(self):
        got = render_report.media_src("comp/我的 影片.mp4")
        self.assertNotIn(" ", got)
        self.assertTrue(got.startswith("comp/"))

    def test_windows_separators_become_slashes(self):
        self.assertEqual(render_report.media_src("comp\\C1.mp4"), "comp/C1.mp4")


class DurationCell(unittest.TestCase):
    def test_reports_no_percentage(self):
        """The tool withholds a figure on purpose; the report must too."""
        self.assertNotIn("%", render_report.duration_cell(a_match()))

    def test_shows_both_lengths_and_the_match(self):
        cell = render_report.duration_cell(a_match())
        self.assertIn("01:10", cell)      # source, and the matched length
        self.assertIn("30:40", cell)      # their video
        self.assertIn("350 frames", cell)

    def test_an_overrun_shows_its_raw_length_and_the_reason(self):
        """Nothing is clamped to look right: the number that cannot be is
        shown with why it cannot."""
        cell = render_report.duration_cell(a_match(
            matchframes=1406, matched_seconds=281.2, overrun=True,
            overrun_reason="1406 frames matched, but the shorter of the two "
                           "videos has only 900"))
        self.assertIn("1406 frames", cell)
        self.assertIn("has only 900", cell)

    def test_another_speed_is_shown_with_what_it_means(self):
        cell = render_report.duration_cell(a_match(framerateratio=0.8))
        self.assertIn("0.80", cell)
        self.assertIn("slower clip", cell)

    def test_the_usual_speed_says_nothing_about_it(self):
        self.assertNotIn("Speed ratio", render_report.duration_cell(a_match()))

    def test_missing_crop_state_is_unknown_on_legacy_matches(self):
        cell = render_report.duration_cell(a_match())
        self.assertEqual(cell.count("unknown (not recorded)"), 2)
        self.assertNotIn("none detected", cell)

    def test_bars_on_theirs(self):
        cell = render_report.duration_cell(
            a_match(candidate_crop="crop=1920:1080:0:140"))
        self.assertIn("Bars on theirs", cell)
        self.assertIn("crop=1920:1080:0:140", cell)

    def test_bars_on_mine_are_reported_too(self):
        """Both sides are detected and cropped, so naming only theirs reads as
        "no bars" on a pair where the source is the one carrying them."""
        cell = render_report.duration_cell(
            a_match(source_crop="crop=iw:180:0:40"))
        self.assertIn("Bars on yours", cell)
        self.assertIn("crop=iw:180:0:40", cell)
        self.assertNotIn("none detected", cell)

    def test_bars_on_both_sides(self):
        cell = render_report.duration_cell(
            a_match(source_crop="crop=iw:180:0:40",
                    candidate_crop="crop=1920:1080:0:140"))
        self.assertIn("Bars on yours", cell)
        self.assertIn("Bars on theirs", cell)


class Row(unittest.TestCase):
    def test_their_video_opens_before_the_match(self):
        cell = render_report.row(a_match(start_seconds=173.0))
        self.assertIn("comp/C1.mp4#t=168", cell)

    def test_my_video_opens_at_zero_when_all_of_it_was_used(self):
        cell = render_report.row(a_match())
        self.assertIn('src="source/S1.mp4"', cell)

    def test_my_video_opens_before_the_part_that_was_taken(self):
        """The boundary says which part of the source was used, so the left
        player can park there instead of always sitting at zero."""
        cell = render_report.row(a_match(source_begin_seconds=30.0))
        self.assertIn("source/S1.mp4#t=25", cell)
        self.assertIn('data-start="25"', cell)

    def test_the_span_taken_from_the_source_is_shown(self):
        cell = render_report.row(a_match(source_begin_seconds=30.0,
                                         source_end_seconds=100.0))
        self.assertIn("00:30", cell)
        self.assertIn("01:40", cell)

    def test_seek_is_also_an_attribute_not_only_a_fragment(self):
        """Chromium ignores #t= when the element preloads metadata only, so
        the fragment alone leaves every player parked at zero."""
        cell = render_report.row(a_match(start_seconds=173.0))
        self.assertIn('data-start="168"', cell)

    def test_overrun_row_has_no_lead_in_hint(self):
        cell = render_report.row(a_match(overrun=True))
        self.assertNotIn("opens at", cell)

    def test_overrun_row_does_not_claim_a_span_either(self):
        cell = render_report.row(a_match(overrun=True))
        self.assertNotIn("taken from", cell)

    def test_overrun_row_does_not_seek_at_all(self):
        """The position means nothing there, so parking on it would mislead."""
        cell = render_report.row(a_match(overrun=True))
        self.assertNotIn("data-start", cell)
        self.assertNotIn("#t=", cell)

    def test_match_inside_the_lead_in_does_not_seek(self):
        cell = render_report.row(a_match(start_seconds=3.0))
        self.assertNotIn("data-start", cell)

    def test_names_are_escaped(self):
        cell = render_report.row(a_match(candidate="<script>.mp4"))
        self.assertNotIn("<script>", cell)


class Render(unittest.TestCase):
    def record(self, matches=None, missing=None, candidates=None,
               sources=None):
        sources = sources or [{"path": "source/S1.mp4", "status": "processed"}]
        candidates = candidates or [
            {"path": "comp/C1.mp4", "status": "matched"},
            {"path": "comp/C2.mp4", "status": "checked"}]
        matches = matches if matches is not None else [a_match()]
        return {
            "schema": find_reuse.SCHEMA,
            "generated_at": "2026-09-09T14:22:31+08:00",
            "tool": {"mpeg7dupes": "mpeg7dupes v0.1 b3"},
            "settings": {"fps": 5.0, "thxh": 290, "mode": "longest",
                         "min_coverage": 40.0, "crop_bars": True},
            "summary": find_reuse.summarise(sources, candidates, matches),
            "limits": dict(find_reuse.LIMITS),
            "sources": sources,
            "candidates": candidates,
            "matches": matches,
            "misses": ["comp/C2.mp4"],
        }

    def test_a_failed_candidate_is_on_the_page_with_its_reason(self):
        """A page that listed only the matches would read as a complete run."""
        page = render_report.render(self.record(candidates=[
            {"path": "comp/C1.mp4", "status": "matched"},
            {"path": "comp/bad.mp4", "status": "failed",
             "failure": {"stage": "fingerprint",
                         "reason": "ffprobe failed: moov atom not found"}}]), [])
        self.assertIn("could not be processed", page)
        self.assertIn("comp/bad.mp4", page)
        self.assertIn("moov atom not found", page)
        self.assertIn("1 not processed", page)

    def test_a_skipped_candidate_is_on_the_page(self):
        page = render_report.render(self.record(candidates=[
            {"path": "comp/C1.mp4", "status": "matched"},
            {"path": "comp/S1.mp4", "status": "skipped",
             "reason": "it is one of the sources"}]), [])
        self.assertIn("comp/S1.mp4", page)
        self.assertIn("one of the sources", page)

    def test_a_complete_run_has_no_such_warning(self):
        page = render_report.render(self.record(), [])
        self.assertNotIn("could not be processed", page)
        self.assertNotIn("not processed", page)

    def test_one_row_per_match(self):
        page = render_report.render(self.record([a_match(), a_match(
            candidate="C3.mp4", candidate_path="comp/C3.mp4")]), [])
        self.assertEqual(page.count("<tr>"), 3)   # header plus two matches

    def test_empty_result_says_so_instead_of_an_empty_table(self):
        page = render_report.render(self.record([]), [])
        self.assertIn("No candidate reached", page)
        self.assertNotIn("<table>", page)

    def test_settings_are_on_the_page(self):
        page = render_report.render(self.record(), [])
        self.assertIn("-x 290", page)
        self.assertIn("mpeg7dupes v0.1 b3", page)

    def test_shortest_matchable_clip_follows_the_fps(self):
        """50 frames, measured, not the 360 the page used to claim."""
        page = render_report.render(self.record(), [])
        self.assertIn("10 seconds", page)
        self.assertNotIn("72 seconds", page)

    def test_missing_media_is_warned_about(self):
        page = render_report.render(self.record(), ["comp/C1.mp4"])
        self.assertIn("not where this report expects", page)
        self.assertIn("comp/C1.mp4", page)

    def test_no_warning_when_everything_resolves(self):
        self.assertNotIn("not where this report expects",
                         render_report.render(self.record(), []))


class EndToEnd(unittest.TestCase):
    def test_record_written_by_build_record_renders(self):
        """The two halves have to agree on the shape, so join them once here."""
        settings = {"fps": 5.0, "thxh": 290, "min_coverage": 40.0,
                    "crop_bars": True, "coarse_filter": True,
                    "mpeg7dupes": "/nonexistent/mpeg7dupes"}
        record = find_reuse.build_record(
            settings,
            [{"name": "S1.mp4", "path": "source/S1.mp4", "seconds": 70.0,
              "frames": 350, "fps": 5.0, "crop": "", "status": "processed"}],
            [{"name": "C1.mp4", "path": "comp/C1.mp4", "seconds": 1840.0,
              "frames": 9200, "fps": 5.0, "crop": "", "status": "matched"}],
            [a_match()], [])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reuse.json"
            path.write_text(json.dumps(record, ensure_ascii=False))
            back = json.loads(path.read_text())
        page = render_report.render(back, [])
        self.assertIn("comp/C1.mp4#t=168", page)
        self.assertIn("starting at 02:53", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
