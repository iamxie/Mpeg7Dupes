# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
reuse_record.py
===============

The record find_reuse.py writes and render_report.py reads: its schema, the
statuses a file can end up with, the exit codes, the limits the record has to
state about itself, and the two phrasings both programs share. A data module
rather than an import of the scanner, so the renderer does not load a whole
program to get a handful of names, and so the terminal and the page cannot
describe one match two ways.
"""

# Bump when a field changes meaning or goes away. Readers refuse a schema they
# do not know rather than guess, because every number here is a measurement
# and a misread one is worse than a missing one.
#
# 2: start_seconds became the measured start of the match rather than where
#    the source's frame 0 would fall. Added end_seconds and source_* bounds.
# 3: every requested source and candidate is in the record with a status, a
#    failed one with the stage and reason; a candidate that shares a source's
#    content is listed for every path; matches carry the raw frame count, the
#    frame count of each video, the coverage as compared against the threshold
#    and the speed ratio the comparison found, and an overrun keeps its raw
#    numbers beside the reason instead of a clamped length. start_max_seconds,
#    always null since 2, is gone. summary and limits were added.
# 4: not_compared, atomic fatal-error records and explicit completed-source
#    scope. A matched candidate can have comparison_complete=False; only fully
#    checked candidates can enter misses. error marks fatal failures even when
#    some earlier comparisons completed. The renderer still accepts schema 3.
# 5: explicit scan path base, reproducibility metadata and per-video warnings.
#    Match measurements and threshold membership are unchanged.
# 6: explicit effective crop mode, independently keyed black detector and
#    colour-cropping warnings. Existing match measurements are unchanged.
# 7: cross-view fallback, needs_review candidates, exact view provenance and
#    per-pair fallback completion. Thresholds are unchanged.
SCHEMA = "find_reuse/7"
READABLE_SCHEMAS = ("find_reuse/3", "find_reuse/4", "find_reuse/5", "find_reuse/6", SCHEMA)

# Exit status of find_reuse.py. Fixed here and in --help, tested in
# tests/unit/test_find_reuse.py.
EXIT_COMPLETE = 0      # every requested file was processed; no match is still complete
EXIT_INCOMPLETE = 1    # a source or candidate could not be processed, the rest was
EXIT_UNUSABLE = 2      # bad arguments, a missing program, no videos, or mpeg7dupes failed

# A source or candidate ends up as one of these. A candidate that was
# processed is then either matched or checked, depending on the threshold.
PROCESSED = "processed"
FAILED = "failed"
SKIPPED = "skipped"
MATCHED = "matched"
NEEDS_REVIEW = "needs_review"
CHECKED = "checked"
NOT_COMPARED = "not_compared"

# What the record cannot say, stated in the record so a reader does not have
# to know the tool to know the limits of its numbers.
LIMITS = {
    "crop_fallback": "Optional fixed 5% top/bottom cross-view matches need review. "
                     "Removing picture content and trying more views can add false matches. "
                     "This is not bar detection or general spatial alignment. Both directions "
                     "are recorded; the longest result is displayed, source-cropped first on ties. "
                     "Review-only results must not trigger automatic duplicate deletion.",
    "matches": "Only pairs at or above min_coverage are in matches. A checked "
               "candidate carries its best coverage against any source, and "
               "nothing else about the pairs below the threshold is kept, so "
               "this record cannot be re-thresholded lower than min_coverage.",
    "shape": "One match per pair: the longest contiguous run the comparison "
             "found. A candidate that reuses a source in several separate "
             "places is reported by the longest of them, not their sum.",
    "speed": "framerateratio is the speed of the candidate relative to the "
             "source as the comparison voted it, on a grid of thirtieths. "
             "Positions always need visual verification. Away from 1.0 "
             "the walk keeps the two clips in step at that ratio, "
             "matchframes counts the frames of the slower clip, so "
             "coverage_percent under-reads by the ratio when the candidate "
             "is the faster one, and a position on the faster side can be "
             "off by the length of the match times the gap between the true "
             "ratio and the grid. Such a match is reported with its ratio "
             "and says so. Before build 7 the walk stepped wrongly at any "
             "ratio but 1.0 and such matches were lost or misplaced.",
    "endpoints": "start and end are the first and the last frame the "
                 "comparison accepted, at the sampling rate in fps, so end is "
                 "the time of the last matched frame, not the frame after it. "
                 "Low-motion or repetitive scenes can place the match far "
                 "from the true location, even at ratio 1.0. Verify the video; "
                 "a timestamp is not a guarantee of a frame-accurate cut.",
    "coverage": "coverage_percent is matchframes over the source's own frame "
                "count, an estimate of source use with boundary and speed "
                "errors. It is not the duplicate-finding measure, which divides "
                "by the shorter of the two.",
    "short_source": "Sources shorter than about two minutes have produced false "
                    "matches on simple light/dark layouts. This is a risk "
                    "warning, not a classifier; longer sources are not guaranteed safe.",
    "crop_uncertain": "When cropping is uncertain (for example, a static scene "
                      "in motion mode, darkness in black mode, or too few samples), the signature is "
                      "uncropped and barred copies may be missed.",
    "black_crop": "The optional black mode detects colour, not the meaning of a border. "
                  "Dark picture edges can be cropped; lettering can leave bars behind. "
                  "Only sampled windows are checked. Review the crop before trusting a match or miss.",
    "reframe": "A horizontal-to-vertical reframe or other substantial content "
               "crop is not reliably supported. Video orientation alone does "
               "not identify this transformation.",
    "misses": "No match reaching the threshold was found among completed "
              "comparisons. This does not rule out reuse below the threshold "
              "or reuse the comparison could not detect.",
}

CROP_LABELS = {"disabled": "not enabled", "detected": "detected",
               "none": "none detected", "uncertain": "uncertain",
               "unknown": "unknown (not recorded)"}


def crop_description(video: dict) -> str:
    state = video.get("crop_state", "unknown")
    if state == "fixed":
        return "Fixed 5% top/bottom view (not detected bars): " + video.get("crop", "")
    label = CROP_LABELS.get(state, CROP_LABELS["unknown"])
    if state == "detected" and video.get("crop"):
        label += ", cropped before comparing, " + video["crop"]
    if state == "uncertain":
        label += "; fingerprinted uncropped, barred copies may be missed"
    if video.get("crop_mode") in ("motion", "black"):
        label += "; " + video["crop_mode"] + " mode"
    return "Bars: " + label


def video_warnings(video: dict, role: str) -> list[dict]:
    codes = []
    seconds = video.get("seconds", 0)
    # Container duration may include audio extending past the video track.
    # Prefer the actual sampled video length; legacy records can lack counts.
    if video.get("frames", 0) > 0 and video.get("fps", 0) > 0:
        seconds = video["frames"] / video["fps"]
    if role == "source" and 0 < seconds < 120:
        codes.append("short_source")
    if video.get("crop_state") == "uncertain":
        codes.append("crop_uncertain")
    if video.get("crop_mode") == "black":
        codes.append("black_crop")
    return [{"code": code, "message": LIMITS[code]} for code in codes]


def as_clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def where_of(hit: dict) -> str:
    """The position phrase for one match, from its record alone."""
    prefix = "Needs review (5% crop fallback); " if hit.get("requires_review") else ""
    if hit["overrun"]:
        return prefix + "position unreliable, the match ran past the end of the video"
    where = prefix + f"starting at {as_clock(hit['start_seconds'])}"
    ratio = hit.get("framerateratio", 1.0)
    if ratio != 1.0:
        where += (f"; at speed ratio {ratio:.2f}, so the coverage counts the "
                  f"slower clip's frames; positions have additional speed-grid "
                  f"error and need visual verification")
    return where
