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
SCHEMA = "find_reuse/4"
READABLE_SCHEMAS = ("find_reuse/3", SCHEMA)

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
CHECKED = "checked"
NOT_COMPARED = "not_compared"

# What the record cannot say, stated in the record so a reader does not have
# to know the tool to know the limits of its numbers.
LIMITS = {
    "matches": "Only pairs at or above min_coverage are in matches. A checked "
               "candidate carries its best coverage against any source, and "
               "nothing else about the pairs below the threshold is kept, so "
               "this record cannot be re-thresholded lower than min_coverage.",
    "shape": "One match per pair: the longest contiguous run the comparison "
             "found. A candidate that reuses a source in several separate "
             "places is reported by the longest of them, not their sum.",
    "speed": "framerateratio is the speed of the candidate relative to the "
             "source as the comparison voted it, on a grid of thirtieths. At "
             "1.0 coverage and positions mean what they say. At any other "
             "value the walk keeps the two clips in step at that ratio, "
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
                 "Measured on synthetic clips cut at known points with the "
                 "ratio at 1.0: exact in three cases of four and three frames "
                 "early on both sides in one.",
    "coverage": "coverage_percent is matchframes over the source's own frame "
                "count, so it answers how much of the source this candidate "
                "holds. It is not the duplicate-finding measure, which divides "
                "by the shorter of the two.",
}


def as_clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def where_of(hit: dict) -> str:
    """The position phrase for one match, from its record alone."""
    if hit["overrun"]:
        return "position unreliable, the match ran past the end of the video"
    where = f"starting at {as_clock(hit['start_seconds'])}"
    ratio = hit.get("framerateratio", 1.0)
    if ratio != 1.0:
        where += (f"; at speed ratio {ratio:.2f}, so the coverage counts the "
                  f"slower clip's frames and the positions hold to within "
                  f"that ratio's grid")
    return where
