# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
find_reuse.py
=============

Takes a video, or a folder of them, and scans another folder for files that
contain any of them.

Sources are your originals and candidates are the files to search. The
reporting threshold defaults to matched frames divided by the shorter video's
frame count, so a short excerpt of a long original can be reported. Select
--min-source-coverage to retain the source-only rule used through v0.2.1.
The two coverage options are mutually exclusive; see README.md#coverage-options.

    uv run tools/find_reuse.py --source ./my_clips --candidates ./downloads

    ./downloads/a.mp4   used interview.mp4, starting at 02:53
    ./downloads/k.mp4   used interview.mp4, starting at 01:10
    ./downloads/a.mp4   used timelapse.mp4, starting at 11:40
    scanned 128 candidates against 10 sources, 3 matches over 40%

Why this wrapper exists
-----------------------
mpeg7dupes reads signatures, not video: it has no decoder. And decoding is by
far the slow part. Measured on 96 files, generating the signatures took 19
minutes and comparing them took 45 seconds. So the signatures have to be kept
and reused, which makes checking a second reference against the same folder
almost free. This script generates them, caches them, does the arithmetic and
prints the answer.

The cache is `sigstore.py`, a SQLite index beside the signature directory. It
identifies a video by what it contains rather than by where it sits, so a
rename, a move to another disk, or the same NAS mounted at a different point on
another machine all cost nothing; and it answers "is this still the file I
recorded" from a stat, so a second run over an unchanged folder reads no video
data at all. Two byte-for-byte identical candidates share one signature and are
compared once, though both are reported: each really does contain the source.

Point --db and --sig-dir at one store from several tools and they share the
work; the index names files relative to the directory, so one without the
other finds nothing.

Coverage is approximate
-----------------------
The terminal and report show matched duration and both side percentages as
walk-count estimates, not confidence scores. Boundary extension can inflate
them, and the shared match count is not adjusted to each timeline at another
speed. The selected denominator controls the threshold and crop-fallback gate;
it does not change signature generation or the C comparison.

The start used to be the other one. It was reported as a range, because what
this read out of the CSV was the seed the walk grew from, and subtracting the
two seeds gives where the reference's frame 0 would sit, which lands early by
however much of the head was skipped. mpeg7dupes has reported the real
boundaries since the columns were added, and this script simply predated them
by an hour and a half and never caught up. It reads them now, so the start is a
single timestamp, and so is the span that was taken out of the reference.
Checked on synthetic clips cut at known points, with the speed ratio voted at
1.0: exact in three cases of four, three frames early on both sides in one.

Overruns keep their raw frame count and an explicit warning; they are not
clamped into a plausible measurement, and the report does not seek to their
positions. Low-motion and repetitive scenes can be far off even at ratio 1.0.
The known short-source false-match, uncertain-crop and reframe limitations
travel with each JSON record and HTML report.

Precedence
----------
    command line > the toml named by --config > built-in defaults

Usage
-----
    uv run tools/find_reuse.py --source mine.mp4 --candidates ./downloads
    uv run tools/find_reuse.py --source mine.mp4 --candidates ./downloads \\
        --min-coverage 30 --show-misses

--json writes the whole run to a file, which is the thing to build on: the
lines above are a summary of it, and the CSV underneath is mpeg7dupes' own
interface, carrying no frame counts for the videos and so unable to express
coverage at all. tools/render_report.py turns that file into an HTML table.

    uv run tools/find_reuse.py --source mine.mp4 --candidates ./downloads \\
        --json reuse.json
    uv run tools/render_report.py reuse.json --out report.html
"""

import argparse
import csv
import datetime
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import io
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from reuse_record import crop_description, video_warnings

# Sibling script. Both live in tools/, and Python puts the script's own
# directory on the path, so this resolves when find_reuse.py is run from
# anywhere. Guarded because copying find_reuse.py out on its own is a
# reasonable thing to do, and losing bar detection should cost a warning
# rather than a traceback.
try:
    import detect_bars
except ImportError:
    detect_bars = None

# Not guarded: the store is what decides whether a signature may be reused, and
# there is no sensible fallback for that. Without it this would have to
# re-fingerprint everything on every run, silently.
import sigstore  # noqa: E402
from tool_settings import validate
import sigmake  # noqa: E402
import video_profile
from scan_progress import Progress
from reuse_record import (  # noqa: E402
    SCHEMA, EXIT_COMPLETE, EXIT_INCOMPLETE, EXIT_UNUSABLE, PROCESSED, FAILED,
    SKIPPED, MATCHED, NEEDS_REVIEW, CHECKED, NOT_COMPARED, LIMITS, as_clock, where_of,
    COVERAGE_CONFLICT, coverage_rule, coverage_values, coverage_label, coverage_limit)

DEFAULTS = {
    "fps": 5.0,
    "min_coverage": 40.0,
    "min_source_coverage": None,
    # Measured over 96 videos: raising this from 60 to 290 took two whole
    # families of edit, black bars and black bars with text, from never
    # detected to always detected, and added no false positives. Below it they
    # are missed entirely rather than partly.
    "thxh": 290,
    # Crop bars off before fingerprinting. Measured over 4560 pairs: without
    # it the best any threshold managed was 717 of 720 true duplicates and no
    # arrangement separated the classes; with it, 720 of 720 with no false
    # positives and 13.9 points of daylight between them. That is the corpus
    # the settings were tuned on, not an independent one. See benchmark.md.
    "crop_bars": True,
    "crop_mode": "motion",
    "crop_fallback": False,
    "analyze": True,
    # Let mpeg7dupes' coarse filter skip pairs of segments that cannot match.
    # On the tuning corpus it lost nothing and took two thirds off the
    # comparison time; --no-coarse-filter passes -d 10001 so a run can check
    # that on other material. See find_reuse.toml.
    "coarse_filter": True,
    "jobs": 0,  # 0 leaves it to mpeg7dupes, which uses every core
    "overwrite": False,
    "extensions": ["mp4", "mkv", "avi", "mov", "wmv", "flv", "ts", "m4v", "webm", "mpg", "mpeg"],
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffprobe",
    "mpeg7dupes": "mpeg7dupes",
}

# The index lives inside the signature directory, so a sig-dir carries its own
# index and moving one moves both. Overridable with --db for a store shared
# between tools.
INDEX_NAME = "index.sqlite"



class ScanParser(argparse.ArgumentParser):
    def error(self, message):
        if '--min-coverage' in message and '--min-source-coverage' in message:
            message = COVERAGE_CONFLICT
        super().error(message)


def build_parser() -> argparse.ArgumentParser:
    p = ScanParser(
        description="Find which videos in a folder contain a given source video.",
        epilog=f"exit status: {EXIT_COMPLETE} when every requested file was "
               f"processed, whether or not anything matched; {EXIT_INCOMPLETE} "
               f"when a source or candidate could not be processed and the "
               f"rest was, in which case the output and the JSON say which; "
               f"{EXIT_UNUSABLE} for bad arguments, a missing program, no "
               f"videos to compare, or a failure of mpeg7dupes itself.")
    p.add_argument("--source", required=True, metavar="PATH",
                   help="The video to look for, or a folder of them.")
    p.add_argument("--candidates", required=True, metavar="PATH",
                   help="Folder to search, walked recursively, or one file.")
    coverage = p.add_mutually_exclusive_group()
    coverage.add_argument("--min-coverage", type=float, metavar="PCT",
                          help="Minimum coverage of the shorter video, in percent. Default 40. "
                               "See README.md#coverage-options.")
    coverage.add_argument("--min-source-coverage", type=float, metavar="PCT",
                          help="Use source-only coverage instead (legacy rule). "
                               "Cannot combine with --min-coverage; see README.md#coverage-options.")
    p.add_argument("--sig-dir", metavar="DIR",
                   help="Where to cache signatures. Defaults to .signatures under the candidates.")
    p.add_argument("--db", metavar="FILE",
                   help="The signature store's index. Defaults to "
                        "index.sqlite inside --sig-dir. Point several tools at "
                        "one index and one --sig-dir and they share the work: "
                        "signatures are keyed on what a video contains, so a "
                        "rename, a move or another machine costs nothing.")
    p.add_argument("--fps", type=float, metavar="N",
                   help="Sampling rate. Source and candidates must match. Default 5.")
    p.add_argument("--thxh", type=int, metavar="N",
                   help="Frame similarity threshold. Default 290.")
    p.add_argument("--jobs", type=int, metavar="N", help="Cores for the comparison. Default all.")
    p.add_argument("--show-misses", action="store_true",
                   help="Also list the candidates that did not match. Off by "
                        "default, because on a large folder the misses bury "
                        "the hits, but it is the only way to tell a candidate "
                        "that was checked and cleared from one that was never "
                        "read at all.")
    p.add_argument("--quiet", action="store_true",
                   help="Hide detailed progress and heartbeats; keep results and warnings.")
    p.add_argument("--no-crop-bars", dest="crop_bars", action="store_false",
                   default=None,
                   help="Do not look for bars along the top and bottom, and "
                        "do not crop them off before fingerprinting. On by "
                        "default; turning it off costs accuracy on any video "
                        "that has them.")
    p.add_argument("--crop-mode", choices=("motion", "black"),
                   help="Bar detection: motion (default), or opt-in black for plain bars on still footage.")
    p.add_argument("--crop-fallback", action="store_true", default=None,
                   help="Retry below-threshold full-frame pairs with fixed 5%% top/bottom cross views. "
                        "Requires --no-crop-bars; added matches need review.")
    analysis = p.add_mutually_exclusive_group()
    analysis.add_argument("--analyze", action="store_true", default=None,
                          help="Analyze sampled darkness and visual change (default on); explain matches without changing decisions.")
    analysis.add_argument("--no-analysis", dest="analyze", action="store_false", default=None,
                          help="Skip visual-property analysis; preserve the original matching workflow.")
    p.add_argument("--no-coarse-filter", dest="coarse_filter",
                   action="store_false", default=None,
                   help="Pass -d 10001 to mpeg7dupes, which turns off the "
                        "coarse filter that skips pairs of segments that cannot "
                        "match. On by default. Turning it off makes the "
                        "comparison about three times as slow; it is there to "
                        "check that the filter loses nothing on your material, "
                        "and the record says which way the run was made.")
    p.add_argument("--json", metavar="FILE",
                   help="Also write the whole run here as JSON: every source, "
                        "every candidate, and every match with its numbers. "
                        "The printed lines are a summary of this; anything "
                        "built on the results should read the JSON instead of "
                        "parsing them. See tools/render_report.py.")
    p.add_argument("--overwrite", action="store_true", default=None,
                   help="Recompute signatures and enabled analysis that already exist.")
    p.add_argument("--config", metavar="FILE", help="toml settings file.")
    p.add_argument("--ffmpeg", metavar="PATH", help="Path to ffmpeg.")
    p.add_argument("--ffprobe", metavar="PATH", help="Path to ffprobe.")
    p.add_argument("--mpeg7dupes", metavar="PATH", help="Path to mpeg7dupes.")
    return p


def load_settings(args: argparse.Namespace) -> dict:
    """Command line beats the toml, which beats the built-in defaults."""
    settings = dict(DEFAULTS)

    config = Path(args.config) if args.config else Path(__file__).with_name("find_reuse.toml")
    if args.config and not config.is_file():
        raise ValueError(f"no such config: {config}")
    if config.is_file():
        with config.open("rb") as fh:
            configured = tomllib.load(fh)
            if 'min_coverage' in configured and 'min_source_coverage' in configured:
                raise ValueError(f"{config}: {COVERAGE_CONFLICT} "
                                 "TOML keys min_coverage and min_source_coverage also require choosing only one.")
            if 'min_source_coverage' in configured:
                settings['min_coverage'] = None
            for key, value in configured.items():
                if key not in settings:
                    raise ValueError(f"unknown setting in {config}: {key}")
                settings[key] = value

    for key in settings:
        value = getattr(args, key, None)
        # `is not None`, not truthiness: --no-crop-bars has to be able to say
        # False and override a toml that says true. Every flag here defaults to
        # None for the same reason, so an absent one leaves the toml alone.
        if value is not None:
            settings[key] = value
    # A CLI coverage choice replaces the entire lower-priority choice, not
    # merely its numeric value. The shipped TOML must not block source mode.
    if getattr(args, 'min_source_coverage', None) is not None:
        settings['min_coverage'] = None
    elif getattr(args, 'min_coverage', None) is not None:
        settings['min_source_coverage'] = None
    validate(settings)
    return settings


class ScanError(RuntimeError):
    pass


def die(message: str) -> None:
    """Nothing was compared: the arguments, the programs or the inputs are not
    usable. Distinct from a run that processed what it could."""
    raise ScanError(message)


def need(program: str, what: str) -> str:
    found = sigmake.resolve_tool(program)
    if not found:
        die(f"cannot find {what}: {program}")
    return found


def make_signature(src: Path, con, sig_dir: Path, settings: dict,
                   detector: str, ffmpeg: str, failures: list,
                   role: str, *, progress=None) -> dict | None:
    """The signature for this video, made only if the store has not got it.

    Two levels of cache, and each skips a different expense. The store answers
    "is this still the file I recorded" from a stat, so an unchanged library
    need not hash video bytes; and it keys signatures on what a video contains, so a
    renamed or moved or copied file keeps the one it already has.

    The making is sigmake's: ffmpeg writes to a temporary name, the result is
    checked, and only then is it renamed into place, so a failure leaves no
    half-signature behind to be found by a later run. Signature bytes are read
    for the recorded SHA-256 even on a cache hit. A video whose bars could
    not be looked for is skipped, not fingerprinted as one without bars.
    """
    try:
        made = sigmake.make(
            src, con, sig_dir, fps=settings["fps"],
            crop_bars=settings["crop_bars"], detector=detector,
            ffmpeg=settings["ffmpeg"], ffprobe=settings["ffprobe"],
            ffmpeg_version=ffmpeg, overwrite=settings["overwrite"],
            crop_mode=settings.get("crop_mode", "motion"), progress=progress)
    except sigmake.ToolError:
        raise
    except (OSError, sigmake.SignatureError) as exc:
        # Recorded, not only printed: a file that could not be processed has
        # to be in the record, or the run looks complete when it is not.
        stage = "read" if isinstance(exc, OSError) else "fingerprint"
        failures.append({"role": role, "name": src.name, "path": str(src),
                         "stage": stage, "reason": str(exc)})
        print(f"\n  failed   {src.name}: {stage}, {exc}", file=sys.stderr)
        return None

    if progress:
        progress("recording signature checksum")
    entry = {"hash": made.content_hash, "filename": made.filename,
            "seconds": round(made.duration, 3), "frames": made.frames, "fps": settings["fps"],
            "crop": made.crop, "crop_state": made.crop_state,
            "crop_mode": settings.get("crop_mode", "motion") if settings["crop_bars"] else "disabled",
            "signature": {"filename": made.filename,
                          "sha256": sha256_file(made.path),
                          "view": "crop5" if made.crop_state == "fixed" else
                                  "full" if not settings["crop_bars"] else "auto-" + settings["crop_mode"],
                          "ffmpeg": made.ffmpeg, "detector": made.detector}}
    print(f"\n  {src.name}: {crop_description(entry)}", flush=True)
    for warning in video_warnings(entry, role, coverage_rule(settings)[0]):
        print(f"  note     {src.name}: {warning['message']}", file=sys.stderr)
    if progress:
        progress(f"signature {'generated' if made.produced else 'cache hit'}: "
                 f"{made.frames} frames at {settings['fps']:g} fps; "
                 f"video {as_clock(made.duration)}")
    return entry


def collect_videos(root: Path, settings: dict) -> list[Path]:
    if root.is_file():
        return [root]
    wanted = {"." + e.lower().lstrip(".") for e in settings["extensions"]}
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in wanted)


def video_list(entries: dict, paths: dict, fps: float) -> list[dict]:
    """The measured facts about every video that got a signature.

    `paths` maps a signature to one path or to several, since identical videos
    share a signature. Every path it names gets a row: they are all real files
    the caller asked about.
    """
    rows = []
    for key, entry in entries.items():
        found = paths.get(key)
        if not found:
            continue
        for path in ([found] if isinstance(found, str) else found):
            rows.append({"name": Path(path).name, "path": path,
                         "status": PROCESSED,
                         "seconds": round(entry["seconds"], 3),
                         "frames": entry["frames"], "fps": fps,
                         "crop": entry["crop"],
                         "crop_mode": entry.get("crop_mode", "unknown"),
                         "content_hash": entry.get("hash"),
                         "content_hash_algorithm": "blake2b-128",
                         "signature": entry.get("signature", {}),
                         "analysis": video_profile.public(entry.get("_profile", {"status": "unavailable"})),
                         **({"crop_fallback": entry["crop_fallback"]} if "crop_fallback" in entry else {}),
                         # What the bar detector concluded: disabled,
                         # detected, none or uncertain. A record from before
                         # this field was kept says unknown.
                         "crop_state": entry.get("crop_state", "unknown")})
    return sorted(rows, key=lambda video: video["path"])


def failure_rows(failures: list, role: str) -> list[dict]:
    """The files of one role that could not be processed, as record rows."""
    return [{"name": f["name"], "path": f["path"], "status": FAILED,
             "failure": {"stage": f["stage"], "reason": f["reason"]}}
            for f in failures if f["role"] == role]


def tool_version(mpeg7dupes: str) -> str:
    """What the comparison binary calls itself, for tracing a result later."""
    try:
        done = subprocess.run([mpeg7dupes, "--version"],
                              capture_output=True, text=True, timeout=10)
        return done.stdout.strip().splitlines()[0] if done.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


def sha256_file(path: Path) -> str | None:
    """Trace the exact bytes; an unavailable tool in a fatal record is unknown."""
    try:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError:
        return None


def comparison_args(settings: dict) -> list[str]:
    """Used both to invoke C and record every comparison setting explicitly."""
    flags = ["-f", "csv", "-m", "longest", "-i", "0", "-k", "1",
             "-b", "0.1", "-x", str(settings["thxh"]),
             "-d", "9000" if settings["coarse_filter"] else "10001",
             "-c", "60000"]
    if settings.get("jobs", 0):
        flags += ["-j", str(settings["jobs"])]
    return flags


def tool_identity(settings: dict) -> dict:
    binary = Path(settings["mpeg7dupes"])
    return {"mpeg7dupes": tool_version(str(binary)),
            "binary_path": str(binary), "binary_sha256": sha256_file(binary),
            "python": sys.version,
            "analysis": {"version": video_profile.VERSION, "recipe_id": video_profile.RECIPE_ID,
                         "sha256": sha256_file(Path(video_profile.__file__))},
            "scanner_sha256": sha256_file(Path(__file__)),
            "record_rules_sha256": sha256_file(Path(__file__).with_name("reuse_record.py")),
            "signature_producer_sha256": sha256_file(Path(sigmake.__file__)),
            "detector": {"version": detect_bars.detector_id(settings.get("crop_mode", "motion")) if detect_bars and settings["crop_bars"] else None,
                         "mode": settings.get("crop_mode", "motion") if settings["crop_bars"] else "disabled",
                         "sha256": sha256_file(Path(detect_bars.__file__)) if detect_bars else None,
                         "enabled": settings["crop_bars"]}}


def summarise(sources: list[dict], candidates: list[dict],
              hits: list[dict]) -> dict:
    """Counts of files by what happened to them, and whether the run was
    complete. Files, not signatures: every path the caller named counts."""
    count = lambda rows, *states: sum(1 for r in rows if r["status"] in states)
    failed = count(sources, FAILED, NOT_COMPARED) + count(candidates, FAILED, NOT_COMPARED)
    return {
        "complete": failed == 0,
        "exit_status": EXIT_COMPLETE if failed == 0 else EXIT_INCOMPLETE,
        "sources_requested": len(sources),
        "sources_processed": count(sources, PROCESSED),
        "sources_failed": count(sources, FAILED),
        "sources_not_compared": count(sources, NOT_COMPARED),
        "candidates_requested": len(candidates),
        "candidates_matched": count(candidates, MATCHED),
        "candidates_needs_review": count(candidates, NEEDS_REVIEW),
        "candidates_checked": count(candidates, CHECKED),
        "candidates_failed": count(candidates, FAILED),
        "candidates_skipped": count(candidates, SKIPPED),
        "candidates_not_compared": count(candidates, NOT_COMPARED),
        "matches": len(hits),
        "review_matches": sum(bool(h.get("requires_review")) for h in hits),
    }


def build_record(settings: dict, sources: list[dict], candidates: list[dict],
                 hits: list[dict], misses: list[str],
                 summary: dict | None = None) -> dict:
    """Assemble the machine-readable record of one run.

    Everything printed to the terminal is a summary of this. Anything built on
    the results should read this instead of parsing those lines, and the same
    goes for the CSV underneath: that is mpeg7dupes' own interface, it carries
    no frame counts for the videos and so cannot express coverage at all.

    Every requested file is in sources or candidates with a status; a failed
    one carries the stage and the reason. matches holds only the pairs at or
    above the threshold, which limits says outright.
    """
    for role, rows in (("source", sources), ("candidate", candidates)):
        for video in rows:
            video["warnings"] = video_warnings(video, role, coverage_rule(settings)[0])
    return {
        "schema": SCHEMA,
        "path_base": str(Path.cwd().resolve()),
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "tool": settings.get("_tool_identity") or tool_identity(settings),
        "settings": {"fps": settings["fps"], "thxh": settings["thxh"],
                     "mode": "longest", "min_coverage": settings["min_coverage"],
                     "min_source_coverage": settings.get("min_source_coverage"),
                     "coverage_basis": coverage_rule(settings)[0],
                     "crop_bars": settings["crop_bars"],
                     "crop_fallback": settings.get("crop_fallback", False),
                     "analyze": settings.get("analyze", True),
                     "crop_mode": settings.get("crop_mode", "motion") if settings["crop_bars"] else "disabled",
                     "coarse_filter": settings["coarse_filter"],
                     "comparison_args": comparison_args(settings),
                     "jobs_requested": settings.get("jobs", 0)},
        "summary": summary if summary is not None
                   else summarise(sources, candidates, hits),
        "limits": dict(LIMITS, coverage=coverage_limit(coverage_rule(settings)[0])),
        "sources": sources,
        "candidates": candidates,
        "matches": hits,
        "misses": sorted(misses),
    }


class ComparisonError(RuntimeError):
    def __init__(self, message, results, completed):
        super().__init__(message)
        self.results = results
        self.completed = completed


def parse_comparison(output, source, candidates):
    """Only accept complete, finite rows for pairs in this invocation."""
    reader = csv.DictReader(io.StringIO(output), strict=True)
    required = {"First signature", "Second signature", "matchframes",
                "framerateratio", "whole"} | {
        f"{field} {side} [s]" for field in ("time", "begin", "end") for side in (1, 2)}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("missing comparison CSV header or columns")
    results = {}
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("incomplete comparison CSV row")
        first, second = row["First signature"], row["Second signature"]
        if first == source and second in candidates:
            other, mine, theirs = second, "1", "2"
        elif second == source and first in candidates:
            other, mine, theirs = first, "2", "1"
        else:
            raise ValueError(f"unexpected comparison pair: {first!r}, {second!r}")
        key = source, other
        if key in results:
            raise ValueError(f"duplicate comparison pair: {key!r}")
        frames, ratio = float(row["matchframes"]), float(row["framerateratio"])
        values = {name: float(row[f"{field} {side} [s]"]) for name, field, side in (
            ("t_source", "time", mine), ("t_other", "time", theirs),
            ("source_begin", "begin", mine), ("source_end", "end", mine),
            ("other_begin", "begin", theirs), ("other_end", "end", theirs))}
        if (not all(math.isfinite(v) for v in (frames, ratio, *values.values()))
                or frames < 0 or not frames.is_integer() or ratio <= 0
                or any(v < 0 for v in values.values())
                or values["source_end"] < values["source_begin"]
                or values["other_end"] < values["other_begin"]
                or row["whole"] not in ("0", "1")):
            raise ValueError("invalid comparison CSV numbers")
        values.update(matchframes=frames,
                      framerateratio=ratio if mine == "1" else round(1.0 / ratio, 6),
                      whole=int(row["whole"]))
        if not math.isfinite(values["framerateratio"]) or values["framerateratio"] <= 0:
            raise ValueError("invalid comparison speed ratio")
        results[key] = values
    return results


def compare(sig_dir: Path, source_bins: list[str], candidate_bins: list[str],
            settings: dict, *, source_paths=None, progress_enabled=True,
            label="compare") -> dict[tuple[str, str], dict]:
    """A private pair of lists for each scan; one invocation per source."""
    results, completed = {}, []
    with tempfile.TemporaryDirectory(prefix="mpeg7dupes-compare-") as directory:
        candidates = Path(directory) / "candidates.txt"
        source = Path(directory) / "source.txt"
        candidates.write_text("\n".join(candidate_bins) + "\n")
        for i, source_bin in enumerate(source_bins, 1):
            source.write_text(source_bin + "\n")
            cmd = [settings["mpeg7dupes"], *comparison_args(settings),
                   "-l", str(candidates), "-n", str(source)]
            paths = (source_paths or {}).get(source_bin, [source_bin])
            with Progress(f"{label} {i}/{len(source_bins)}: {paths[0]!r}",
                          enabled=progress_enabled) as progress:
                progress(f"comparing against {len(candidate_bins)} unique candidates")
                try:
                    done = subprocess.run(cmd, cwd=sig_dir, capture_output=True, text=True)
                    if done.returncode != 0:
                        raise ValueError(f"mpeg7dupes failed ({done.returncode}): "
                                         + done.stderr.strip()[:600])
                    parsed = parse_comparison(done.stdout, source_bin, set(candidate_bins))
                except (OSError, ValueError, csv.Error) as exc:
                    raise ComparisonError(str(exc), results, completed) from exc
                progress.finish(f"comparison complete: {len(candidate_bins)} unique candidates")
            results.update(parsed)
            completed.append(source_bin)
    return results


def compare_crop_fallback(sig_dir, con, source_entries, source_paths, entries,
                          shown, settings, ffmpeg_ver, results, *, progress_enabled=True):
    """Retry only completed full-frame misses, grouping candidates per source.

    Two cross directions, never crop/crop. Keep both measurements and the
    original full-frame result. A failed branch cannot produce a checked miss;
    completed earlier sources and all baseline hits remain available.
    """
    audit = {"enabled": True, "complete": False, "recipe": sigmake.FIXED_CROP_ID,
             "pairs": []}
    completed = []
    basis, threshold = coverage_rule(settings)
    fixed_settings = dict(settings, crop_bars=True, crop_mode="fixed5")

    def fixed(entry, paths, role):
        if "crop_fallback" not in entry:
            failures = []
            with Progress(f"crop fallback {role}: {paths[0]!r}",
                          enabled=progress_enabled) as progress:
                made = make_signature(Path(paths[0]), con, sig_dir, fixed_settings,
                                      sigmake.FIXED_CROP_ID, ffmpeg_ver, failures, role,
                                      progress=progress)
                progress.finish("fixed view ready" if made else "fixed view failed")
            if made is None:
                entry["crop_fallback"] = {"status": FAILED, "failure": failures[0]}
                raise sigmake.SignatureError(failures[0]["reason"])
            if made["hash"] != entry["hash"] or made["frames"] != entry["frames"]:
                reason = "video content or sampled frame count changed between full and fixed views"
                entry["crop_fallback"] = {"status": FAILED, "failure": {"reason": reason}}
                raise sigmake.SignatureError(reason)
            entry["crop_fallback"] = dict(made, status=PROCESSED)
        return entry["crop_fallback"]

    try:
        for source in sorted(source_entries):
            base = source_entries[source]
            pending = [candidate for candidate in sorted(entries)
                       if (source, candidate) not in results or
                       coverage_values(results[source, candidate]["matchframes"],
                                       base["frames"], entries[candidate]["frames"])
                       [basis + "_coverage_percent"] < threshold]
            if not pending:
                completed.append(source)
                continue
            pairs = {}
            for candidate in pending:
                item = {"source_signature": source, "candidate_signature": candidate,
                        "complete": False, "full_result": results.get((source, candidate)),
                        "directions": []}
                audit["pairs"].append(item)
                pairs[candidate] = item
            cropped_source = fixed(base, source_paths[source], "source")
            cropped_candidates = {c: fixed(entries[c], shown[c], "candidate") for c in pending}
            for source_view, candidate_view in (("crop5", "full"), ("full", "crop5")):
                source_entry = cropped_source if source_view == "crop5" else base
                candidate_entries = cropped_candidates if candidate_view == "crop5" else entries
                names = [candidate_entries[c]["filename"] for c in pending]
                measured = compare(sig_dir, [source_entry["filename"]], names, settings,
                                   source_paths={source_entry["filename"]: source_paths[source]},
                                   progress_enabled=progress_enabled,
                                   label=f"crop fallback {source_view}/{candidate_view}")
                for candidate in pending:
                    candidate_entry = candidate_entries[candidate]
                    found = measured.get((source_entry["filename"], candidate_entry["filename"]))
                    pairs[candidate]["directions"].append({
                        "source_view": source_view, "candidate_view": candidate_view,
                        "source_signature": source_entry["signature"],
                        "candidate_signature": candidate_entry["signature"],
                        "source_crop": source_entry["crop"], "candidate_crop": candidate_entry["crop"],
                        "result": found})
            for candidate, pair in pairs.items():
                pair["complete"] = True
                evidence = [e for e in pair["directions"] if e["result"] is not None]
                if not evidence:
                    continue
                # This selects a display result across views, not the C
                # algorithm's temporal candidates. Keep the other evidence.
                selected = max(evidence, key=lambda e: e["result"]["matchframes"])
                baseline = results.get((source, candidate))
                if baseline and baseline["matchframes"] >= selected["result"]["matchframes"]:
                    continue
                results[source, candidate] = {
                    **selected["result"],
                    **{k: v for k, v in selected.items() if k != "result"},
                    "requires_review": True, "view_evidence": pair["directions"]}
            completed.append(source)
    except (OSError, sqlite3.Error, sigmake.SignatureError, sigmake.ToolError, ComparisonError) as exc:
        error = {"stage": "crop_fallback", "reason": str(exc)}
        audit["error"] = error
        return audit, completed, error
    audit["complete"] = True
    return audit, completed, None


def write_record(path, record):
    """Leave the previous record intact if serialization or writing fails."""
    payload = json.dumps(record, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def scan(args, settings, sources, requested) -> int:
    started = time.monotonic()
    basis, threshold = coverage_rule(settings)
    source_root = Path(args.source)
    candidates_root = Path(args.candidates)
    if not source_root.exists():
        die(f"no such source: {source_root}")
    if not candidates_root.exists():
        die(f"no such candidates path: {candidates_root}")
    if source_root.is_file() and source_root.suffix.lower() == ".bin":
        die("--source must be a video: a signature carries no duration, "
            "so there is nothing to take a proportion of.")

    sig_dir = Path(args.sig_dir) if args.sig_dir else (
        candidates_root if candidates_root.is_dir() else candidates_root.parent) / ".signatures"

    settings["mpeg7dupes"] = need(settings["mpeg7dupes"], "mpeg7dupes")
    settings["ffmpeg"] = need(settings["ffmpeg"], "ffmpeg")
    settings["ffprobe"] = need(settings["ffprobe"], "ffprobe")
    settings["_tool_identity"] = tool_identity(settings)

    if settings["crop_bars"] and detect_bars is None:
        die("--crop-bars needs detect_bars.py beside this script. Pass "
            "--no-crop-bars to make a set that is knowingly uncropped, "
            "rather than one that might be either.")
    # Only meaningful when cropping, and the store leaves it out of the key
    # otherwise, so an uncropped set is not retired by a retuned detector.
    detector = detect_bars.detector_id(settings["crop_mode"]) if settings["crop_bars"] else ""
    sig_dir.mkdir(parents=True, exist_ok=True)
    con = sigstore.open_db(Path(args.db) if args.db else sig_dir / INDEX_NAME)
    moved = sigstore.note_sig_dir(con, sig_dir)
    if moved:
        print(f"  note     this index last described {moved}; the signatures "
              f"it names are looked for in {sig_dir} now", file=sys.stderr)
    ffmpeg_ver = sigstore.ffmpeg_version(settings["ffmpeg"])
    if not settings["coarse_filter"]:
        print("coarse filter off: every pair of segments is walked (-d 10001), "
              "about three times as slow")

    if not sources:
        die(f"no videos under {source_root}")
    print(f"sources     {len(sources)}", flush=True)
    progress_enabled = not getattr(args, "quiet", False)
    if progress_enabled:
        print(f"[progress] inventory: {len(sources)} sources, {len(requested)} candidates requested; "
              f"cache {str(sig_dir)!r}", file=sys.stderr, flush=True)
        print(f"[progress] settings: fps={settings['fps']:g}, "
              f"coverage>={threshold:g}% of {coverage_label(basis)}, "
              f"thxh={settings['thxh']}, coarse filter={'on' if settings['coarse_filter'] else 'off'}, "
              f"crop={settings['crop_mode'] if settings['crop_bars'] else 'disabled'}, "
              f"analysis={'on' if settings['analyze'] else 'off'}, "
              f"crop fallback={'on' if settings['crop_fallback'] else 'off'}, "
              f"comparison jobs requested={settings['jobs'] or 'auto'}",
              file=sys.stderr, flush=True)
    # Signatures are content-addressed, so two copies of one video share a
    # filename. Every map below is therefore keyed on the signature and the
    # paths hang off it, rather than the other way round. A list of paths, not
    # one: a copy of a source is a source the caller named, and is reported.
    failures: list[dict] = []
    source_entries: dict[str, dict] = {}
    source_paths: dict[str, list[str]] = {}
    for i, video in enumerate(sources, 1):
        with Progress(f"source {i}/{len(sources)}: {str(video)!r}",
                      enabled=progress_enabled) as progress:
            entry = make_signature(video, con, sig_dir, settings, detector,
                                   ffmpeg_ver, failures, "source", progress=progress)
            progress.finish("signature ready" if entry else "signature failed")
        if entry:
            source_entries[entry["filename"]] = entry
            # As walked, like the candidates below: a report has to point at
            # the file where the caller said it was.
            source_paths.setdefault(entry["filename"], []).append(str(video))
    con.commit()
    print()

    already = {v.resolve() for v in sources}
    # A candidate that is one of the sources is skipped, and says so in the
    # record: it would only ever match itself.
    skipped = [v for v in requested if v.resolve() in already]
    videos = [v for v in requested if v.resolve() not in already]


    entries: dict[str, dict] = {}
    # Display the path as it was walked, not the resolved one. Identifying a
    # file needs the real path, so the same video reached two ways is not
    # signed twice, but what is reported has to be where the caller said to
    # look, or a symlink sends them somewhere they never named.
    #
    # A list, not one path: identical videos share one signature, and a report
    # that named only the first of them would quietly drop the rest.
    shown: dict[str, list[str]] = {}
    if source_entries:
        print(f"candidates  {len(videos)}, signatures cached in {sig_dir}")
        for i, video in enumerate(videos, 1):
            with Progress(f"candidate {i}/{len(videos)}: {str(video)!r}",
                          enabled=progress_enabled) as progress:
                entry = make_signature(video, con, sig_dir, settings, detector,
                                       ffmpeg_ver, failures, "candidate", progress=progress)
                progress.finish("signature ready" if entry else "signature failed")
            if entry:
                entries[entry["filename"]] = entry
                shown.setdefault(entry["filename"], []).append(str(video))
        con.commit()
        print()

    signed = sum(len(paths) for paths in shown.values())
    duplicates = signed - len(shown)
    if duplicates:
        print(f"  {duplicates} of them are byte-for-byte copies of another, so "
              f"{len(shown)} signatures cover all {signed}")

    # Pass 1 measures each original video, independently of its signature views.
    # Analysis failures must not erase a valid signature or prevent comparison.
    analysis_failures = []
    analysis_total = len(source_entries) + len(entries)
    analysis_index = 0
    for inventory, paths in ((source_entries, source_paths), (entries, shown)):
        for key, entry in inventory.items():
            if not settings["analyze"]:
                entry["_profile"] = {"status": "disabled"}
                continue
            path = paths[key][0]
            analysis_index += 1
            with Progress(f"analysis {analysis_index}/{analysis_total}: {path!r}",
                          enabled=progress_enabled) as progress:
                try:
                    entry["_profile"] = video_profile.make(
                        Path(path), con, sig_dir, content_hash=entry["hash"],
                        ffmpeg=settings["ffmpeg"], ffprobe=settings["ffprobe"],
                        ffmpeg_version=ffmpeg_ver, overwrite=settings["overwrite"], progress=progress)
                except (video_profile.ProfileError, sigmake.ToolError, OSError, sqlite3.Error) as exc:
                    entry["_profile"] = {"status": "failed", "reason": str(exc)}
                    analysis_failures.extend({"path": p, "reason": str(exc)} for p in paths[key])
                progress.finish(video_profile.describe(entry['_profile']))
            print(f"  analysis {path}: {video_profile.describe(entry['_profile'])}", flush=True)

    results, completed = {}, []
    error = None
    if source_entries and entries:
        try:
            results = compare(sig_dir, sorted(source_entries), sorted(entries), settings,
                              source_paths=source_paths, progress_enabled=progress_enabled)
            completed = sorted(source_entries)
        except ComparisonError as exc:
            results, completed = exc.results, exc.completed
            error = {"stage": "compare", "reason": str(exc)}
    else:
        error = {"stage": "prepare", "reason": "no usable sources and candidates to compare"}
    baseline_completed = list(completed)
    fallback = {"enabled": bool(settings.get("crop_fallback")), "complete": False, "pairs": []}
    if settings.get("crop_fallback") and error is None:
        fallback, completed, error = compare_crop_fallback(
            sig_dir, con, source_entries, source_paths, entries, shown,
            settings, ffmpeg_ver, results, progress_enabled=progress_enabled)
    comparison_complete = (error is None and not any(f["role"] == "source" for f in failures))
    compared_paths = [p for key in completed for p in source_paths[key]]
    con.close()

    hits, matched, reviewed, overran = [], set(), set(), 0
    best: dict[str, float] = {}
    for (source_bin, name), found in results.items():
        source_entry = source_entries[source_bin]
        frames = int(found["matchframes"])
        # A match cannot be longer than the shorter of the two videos it was
        # found in. Nothing in the current comparison produces one that is, but
        # a version of it did, and the failure was silent: the length and the
        # position both described a region that was not there. The raw number
        # stays in the record beside the reason; nothing is clamped to look
        # right. The tolerance covers a frame or two the walk carries past the
        # ends.
        ceiling = min(source_entry["frames"], entries[name]["frames"])
        overrun = frames > max(ceiling * 1.02, ceiling + 2)
        measures = coverage_values(frames, source_entry["frames"], entries[name]["frames"])
        coverage = measures[basis + "_coverage_percent"]
        best[name] = max(best.get(name, 0.0), coverage)
        if coverage < threshold:
            continue
        if overrun:
            overran += 1
        (reviewed if found.get("requires_review") else matched).add(name)
        # Where the match begins in their video, measured, not inferred: the
        # first frame the comparison accepted. Rounded on the way out, since
        # every one of these is a frame index divided by a frame rate.
        #
        # One row per pair of files, not per pair of signatures. Identical
        # videos share a signature and are compared once, but each of them
        # really is the file the caller asked about, and each has to be named.
        for source_path in source_paths[source_bin]:
            for path in shown[name]:
                hits.append({
                    "source": Path(source_path).name,
                    "source_path": source_path,
                    "candidate": Path(path).name,
                    "candidate_path": path,
                    # settings, not the entry: every signature in one run is
                    # taken at the same rate, which is what makes them
                    # comparable at all.
                    "fps": settings["fps"],
                    "matchframes": frames,
                    "source_frames": source_entry["frames"],
                    "candidate_frames": entries[name]["frames"],
                    "coverage_percent": round(coverage, 1),
                    "coverage_basis": basis,
                    **{key: round(value, 1) for key, value in measures.items()},
                    "framerateratio": found["framerateratio"],
                    "matched_seconds": round(frames / settings["fps"], 3),
                    "source_seconds": round(source_entry["seconds"], 3),
                    "candidate_seconds": round(entries[name]["seconds"], 3),
                    "source_crop": found.get("source_crop", source_entry["crop"]),
                    "candidate_crop": found.get("candidate_crop", entries[name]["crop"]),
                    "source_view": found.get("source_view", source_entry["signature"]["view"]),
                    "candidate_view": found.get("candidate_view", entries[name]["signature"]["view"]),
                    "source_signature": found.get("source_signature", source_entry["signature"]),
                    "candidate_signature": found.get("candidate_signature", entries[name]["signature"]),
                    "requires_review": found.get("requires_review", False),
                    "view_evidence": found.get("view_evidence", []),
                    "start_seconds": round(found["other_begin"], 3),
                    "end_seconds": round(found["other_end"], 3),
                    # And the same span inside the source, which says which
                    # part of your video was taken, not just where it landed
                    # in theirs.
                    "source_begin_seconds": round(found["source_begin"], 3),
                    "source_end_seconds": round(found["source_end"], 3),
                    "whole": found["whole"],
                    "overrun": overrun,
                    "overrun_reason": (
                        f"{frames} frames matched, but the shorter of the two "
                        f"videos has only {ceiling}" if overrun else ""),
                })
                # Pass 2 uses both reported spans, including the final signature frame.
                hits[-1]["assessment"] = video_profile.assess(
                    hits[-1], source_entry["_profile"], entries[name]["_profile"])

    # Sorted by source first, because the question being asked is which of my
    # clips were taken, not what is inside each of their videos.
    hits.sort(key=lambda h: (h["source"], h["candidate_path"]))
    print()
    for hit in hits:
        print(f"{hit['candidate_path']}   used {hit['source']}, {where_of(hit)}")
        print(f"  estimated overlap {as_clock(hit['matched_seconds'])}; "
              f"source {hit['source_coverage_percent']:.1f}%, "
              f"candidate {hit['candidate_coverage_percent']:.1f}% "
              f"(walk-count estimates; threshold uses {coverage_label(basis)})")
        for group in ("content_notes", "position_notes"):
            for note in hit["assessment"][group]:
                print(f"  {group.removesuffix('_notes')}: {note}")
    if hits:
        print(f"  note     {LIMITS['endpoints']}", file=sys.stderr)
    print(f"  note     {LIMITS['reframe']}", file=sys.stderr)
    misses = [path for name in entries if name not in matched | reviewed
              for path in shown[name]] if comparison_complete else []
    if args.show_misses and comparison_complete:
        if misses:
            print(f"  note     {LIMITS['misses']}", file=sys.stderr)
        for name in sorted(entries, key=lambda n: shown[n][0]):
            if name not in matched | reviewed:
                for path in shown[name]:
                    print(f"{path}   no match reaching the threshold, best "
                          f"{best.get(name, 0.0):.0f}% of {coverage_label(basis)}")
    for failure in failures:
        print(f"{failure['path']}   could not be processed: "
              f"{failure['stage']}, {failure['reason']}")
    for video in skipped:
        print(f"{video}   skipped, it is one of the sources")

    # Files, not signatures: identical candidates share one signature but the
    # caller asked about each file, and each was answered for.
    processed = signed if completed else 0
    source_count = len(compared_paths)
    print(f"\nscanned {processed} candidate{'s' if processed != 1 else ''} "
          f"against {source_count} source{'s' if source_count != 1 else ''}, "
          f"{len(hits)} match{'es' if len(hits) != 1 else ''} over "
          f"{threshold:.0f}% of {coverage_label(basis)}")
    if reviewed:
        print(f"  Needs review: {sum(h['requires_review'] for h in hits)} crop fallback matches")
    if overran:
        print(f"{overran} of them ran past the end of the video they were found "
              f"in, so their length and position mean nothing. The match itself "
              f"still stands: something is shared, just not where it says.")
    if failures:
        print(f"{len(failures)} file{'s' if len(failures) != 1 else ''} could "
              f"not be processed, so this run is incomplete: exit status "
              f"{EXIT_UNUSABLE if error else EXIT_INCOMPLETE}. The lines above and the JSON say which.")

    candidate_rows = video_list(entries, shown, settings["fps"])
    matched_paths = {path for name in matched for path in shown[name]}
    reviewed_paths = {path for name in reviewed for path in shown[name]}
    for row_ in candidate_rows:
        row_["status"] = (MATCHED if row_["path"] in matched_paths else
                          NEEDS_REVIEW if row_["path"] in reviewed_paths else
                          CHECKED if comparison_complete else NOT_COMPARED)
        row_["comparison_complete"] = comparison_complete
        if not comparison_complete:
            row_["reason"] = "not compared against every requested source"
        row_["best_coverage_percent"] = round(best.get(
            next(n for n, ps in shown.items() if row_["path"] in ps), 0.0), 1)
    candidate_rows += failure_rows(failures, "candidate")
    candidate_rows += [{"name": v.name, "path": str(v), "status": SKIPPED,
                        "reason": "it is one of the sources"} for v in skipped]
    represented = {r["path"] for r in candidate_rows}
    candidate_rows += [{"name": v.name, "path": str(v), "status": NOT_COMPARED,
                        "reason": "no usable source"} for v in requested if str(v) not in represented]
    source_rows = video_list(source_entries, source_paths, settings["fps"])
    source_rows += failure_rows(failures, "source")
    summary = summarise(source_rows, candidate_rows, hits)

    if error:
        summary.update(complete=False, exit_status=EXIT_UNUSABLE)
        print(f"incomplete: {error['stage']}, {error['reason']}", file=sys.stderr)
    for row in candidate_rows:
        if row["status"] == NOT_COMPARED:
            print(f"{row['path']}   not compared: {row['reason']}")
    comparison_record_complete = summary["complete"]
    if analysis_failures:
        summary["complete"] = False
        if summary["exit_status"] == EXIT_COMPLETE:
            summary["exit_status"] = EXIT_INCOMPLETE
        print(f"Analysis incomplete: {len(analysis_failures)} file(s); comparison results retained.", file=sys.stderr)
    summary["analysis_failed"] = len(analysis_failures)
    if args.json:
        record = build_record(settings, source_rows, candidate_rows, hits,
                              misses, summary)
        record["comparison"] = {"complete": comparison_record_complete, "sources_completed": compared_paths}
        record["analysis"] = {"enabled": settings["analyze"],
                              "complete": not analysis_failures and not failures and bool(source_entries and entries),
                              "failures": analysis_failures,
                              "unsupported": sum(v.get("analysis", {}).get("status") == "unsupported"
                                                 for v in source_rows + candidate_rows)}
        if settings.get("crop_fallback"):
            record["fallback"] = fallback
            record["comparison"]["full_frame_sources_completed"] = [
                p for key in baseline_completed for p in source_paths[key]]
        if error:
            record["error"] = error
        try:
            write_record(args.json, record)
        except OSError as exc:
            print(f"cannot write {args.json}: {exc}", file=sys.stderr)
            return EXIT_UNUSABLE
        print(f"wrote {args.json}")
    if progress_enabled:
        state = "complete" if summary["complete"] else "incomplete"
        print(f"[progress] scan {state} | elapsed {as_clock(time.monotonic() - started)}",
              file=sys.stderr, flush=True)
    return summary["exit_status"]


def main() -> int:
    args = build_parser().parse_args()
    try:
        settings = load_settings(args)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"invalid settings: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE
    sources, candidates = [], []
    try:
        sources = collect_videos(Path(args.source), settings)
        candidates = collect_videos(Path(args.candidates), settings)
        return scan(args, settings, sources, candidates)
    except (OSError, sqlite3.Error, ValueError, ScanError, SystemExit, sigmake.ToolError) as exc:
        # Fatal preparation/storage errors still account for the inventory.
        print(f"scan failed: {exc}", file=sys.stderr)
        if args.json:
            def inventory(videos):
                return [{"name": v.name, "path": str(v), "status": NOT_COMPARED,
                         "reason": "scan failed before comparison"}
                        for v in videos]
            record = build_record(settings, inventory(sources), inventory(candidates), [], [])
            record["summary"].update(complete=False, exit_status=EXIT_UNUSABLE)
            record["error"] = {"stage": "tools" if isinstance(exc, sigmake.ToolError) else "prepare", "reason": str(exc)}
            record["comparison"] = {"complete": False, "sources_completed": []}
            record["analysis"] = {"enabled": settings.get("analyze", True), "complete": False,
                                  "reason": "scan failed before analysis completed"}
            try:
                write_record(args.json, record)
            except OSError as write_error:
                print(f"cannot write {args.json}: {write_error}", file=sys.stderr)
        return EXIT_UNUSABLE


if __name__ == "__main__":
    sys.exit(main())
