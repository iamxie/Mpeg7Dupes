# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
find_reuse.py
=============

Takes a video, or a folder of them, and scans another folder for files that
contain any of them.

The situation this is for: you made a short film, and someone tells you a
company cut it into one of their videos. You do not need to know exactly how
many seconds they took. You need to know that it is over some proportion, and
roughly where, which is enough to go and comment under their video.

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

One number not to quote
-----------------------
Coverage runs a little high. Clips using 86%, 50% and 30% of the reference came
back as 88%, 54% and 33%: the walk that extends a match carries a few frames
past each end of what is really shared. So the output says only that a
threshold was passed, never a percentage, and --min-coverage 40 fires at around
36% of real use. Erring that way is deliberate: better to look at a few extra
videos than to miss one.

The start used to be the other one. It was reported as a range, because what
this read out of the CSV was the seed the walk grew from, and subtracting the
two seeds gives where the reference's frame 0 would sit, which lands early by
however much of the head was skipped. mpeg7dupes has reported the real
boundaries since the columns were added, and this script simply predated them
by an hour and a half and never caught up. It reads them now, so the start is a
single timestamp, and so is the span that was taken out of the reference.
Checked on synthetic clips cut at known points, with the speed ratio voted at
1.0: exact in three cases of four, three frames early on both sides in one.

The length is capped at what is possible either way, since a match cannot be
longer than the shorter of the two videos, and one that needed capping is
reported without a position. That guard was written when the comparison could
report 1406 frames of a 900 frame overlap. The cause turned out to be counters
carried from one candidate to the next, which is fixed, and on the 276 pairs
where 11 needed capping before, none do now. It stays because it costs nothing
and what it catches is silent.

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
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

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
import sigmake  # noqa: E402
from reuse_record import (  # noqa: E402
    SCHEMA, EXIT_COMPLETE, EXIT_INCOMPLETE, EXIT_UNUSABLE, PROCESSED, FAILED,
    SKIPPED, MATCHED, CHECKED, LIMITS, as_clock, where_of)

DEFAULTS = {
    "fps": 5.0,
    "min_coverage": 40.0,
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



def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
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
    p.add_argument("--min-coverage", type=float, metavar="PCT",
                   help="Report a candidate at this much of the source, in percent. Default 40.")
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
    p.add_argument("--no-crop-bars", dest="crop_bars", action="store_false",
                   default=None,
                   help="Do not look for bars along the top and bottom, and "
                        "do not crop them off before fingerprinting. On by "
                        "default; turning it off costs accuracy on any video "
                        "that has them.")
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
                   help="Recompute signatures that already exist.")
    p.add_argument("--config", metavar="FILE", help="toml settings file.")
    p.add_argument("--ffmpeg", metavar="PATH", help="Path to ffmpeg.")
    p.add_argument("--ffprobe", metavar="PATH", help="Path to ffprobe.")
    p.add_argument("--mpeg7dupes", metavar="PATH", help="Path to mpeg7dupes.")
    return p


def load_settings(args: argparse.Namespace) -> dict:
    """Command line beats the toml, which beats the built-in defaults."""
    settings = dict(DEFAULTS)

    config = Path(args.config) if args.config else Path(__file__).with_name("find_reuse.toml")
    if config.is_file():
        with config.open("rb") as fh:
            for key, value in tomllib.load(fh).items():
                if key in settings:
                    settings[key] = value

    for key in settings:
        value = getattr(args, key, None)
        # `is not None`, not truthiness: --no-crop-bars has to be able to say
        # False and override a toml that says true. Every flag here defaults to
        # None for the same reason, so an absent one leaves the toml alone.
        if value is not None:
            settings[key] = value
    return settings


def die(message: str) -> None:
    """Nothing was compared: the arguments, the programs or the inputs are not
    usable. Distinct from a run that processed what it could."""
    print(message, file=sys.stderr)
    raise SystemExit(EXIT_UNUSABLE)


def need(program: str, what: str) -> str:
    found = shutil.which(program) or (program if Path(program).is_file() else None)
    if not found:
        die(f"cannot find {what}: {program}")
    return found


def make_signature(src: Path, con, sig_dir: Path, settings: dict,
                   detector: str, ffmpeg: str, failures: list,
                   role: str) -> dict | None:
    """The signature for this video, made only if the store has not got it.

    Two levels of cache, and each skips a different expense. The store answers
    "is this still the file I recorded" from a stat, so an unchanged library
    reads nothing at all; and it keys signatures on what a video contains, so a
    renamed or moved or copied file keeps the one it already has.

    The making is sigmake's: ffmpeg writes to a temporary name, the result is
    checked, and only then is it renamed into place, so a failure leaves no
    half-signature behind to be found by a later run. A video whose bars could
    not be looked for is skipped, not fingerprinted as one without bars.
    """
    try:
        made = sigmake.make(
            src, con, sig_dir, fps=settings["fps"],
            crop_bars=settings["crop_bars"], detector=detector,
            ffmpeg=settings["ffmpeg"], ffprobe=settings["ffprobe"],
            ffmpeg_version=ffmpeg, overwrite=settings["overwrite"])
    except (OSError, sigmake.SignatureError) as exc:
        # Recorded, not only printed: a file that could not be processed has
        # to be in the record, or the run looks complete when it is not.
        stage = "read" if isinstance(exc, OSError) else "fingerprint"
        failures.append({"role": role, "name": src.name, "path": str(src),
                         "stage": stage, "reason": str(exc)})
        print(f"\n  failed   {src.name}: {stage}, {exc}", file=sys.stderr)
        return None

    # The caller draws a progress counter with a carriage return and no
    # newline, so anything printed here has to start its own line or it lands
    # in the middle of that one.
    if made.produced and made.crop_state == "detected":
        print(f"\n  bars     {src.name}: {made.crop}")
    elif made.produced and made.crop_state == "uncertain":
        print(f"\n  note     {src.name}: could not tell whether it has bars, "
              f"fingerprinted uncropped", file=sys.stderr)
    return {"hash": made.content_hash, "filename": made.filename,
            "seconds": made.duration, "frames": made.frames,
            "crop": made.crop, "crop_state": made.crop_state}


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


def summarise(sources: list[dict], candidates: list[dict],
              hits: list[dict]) -> dict:
    """Counts of files by what happened to them, and whether the run was
    complete. Files, not signatures: every path the caller named counts."""
    count = lambda rows, *states: sum(1 for r in rows if r["status"] in states)
    failed = count(sources, FAILED) + count(candidates, FAILED)
    return {
        "complete": failed == 0,
        "exit_status": EXIT_COMPLETE if failed == 0 else EXIT_INCOMPLETE,
        "sources_requested": len(sources),
        "sources_processed": count(sources, PROCESSED),
        "sources_failed": count(sources, FAILED),
        "candidates_requested": len(candidates),
        "candidates_matched": count(candidates, MATCHED),
        "candidates_checked": count(candidates, CHECKED),
        "candidates_failed": count(candidates, FAILED),
        "candidates_skipped": count(candidates, SKIPPED),
        "matches": len(hits),
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
    return {
        "schema": SCHEMA,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "tool": {"mpeg7dupes": tool_version(settings["mpeg7dupes"])},
        "settings": {"fps": settings["fps"], "thxh": settings["thxh"],
                     "mode": "longest", "min_coverage": settings["min_coverage"],
                     "crop_bars": settings["crop_bars"],
                     "coarse_filter": settings["coarse_filter"]},
        "summary": summary if summary is not None
                   else summarise(sources, candidates, hits),
        "limits": dict(LIMITS),
        "sources": sources,
        "candidates": candidates,
        "matches": hits,
        "misses": sorted(misses),
    }


def compare(sig_dir: Path, source_bins: list[str], candidate_bins: list[str],
            settings: dict) -> dict[tuple[str, str], dict]:
    """Compare every source against every candidate, and nothing else.

    One run per source rather than one run with all of them in the incremental
    list, because that list is also compared against itself: ten sources would
    add forty-five comparisons between clips that are all yours. Splitting them
    means those are never computed rather than computed and discarded, and it
    costs nothing, because either way each run imports every candidate exactly
    once. Candidates are never compared against each other in either
    arrangement; only the incremental list is walked as the outer loop.
    """
    (sig_dir / "candidates.txt").write_text("\n".join(candidate_bins) + "\n")
    results = {}

    for i, source_bin in enumerate(source_bins, 1):
        (sig_dir / "source.txt").write_text(source_bin + "\n")

        # Every option spelled out, so the run means the same on a build with
        # other defaults. -i 0, -k 1 and -b 0.1 so every candidate comes back
        # and the threshold is applied here instead.
        #
        # -m longest, not full. full stops searching as soon as one walk has
        # reached an end in each file, which a shared advertisement satisfies
        # when it sits at the head of one and the tail of the other; the search
        # then ends on the advertisement and never finds the real overlap. Over
        # 4560 pairs at -x 290, full recovered 596 of 720 true duplicates with
        # no false positives against longest's 717, and its worst true pair was
        # reported at 0.1 per cent coverage. See benchmark.md.
        cmd = [settings["mpeg7dupes"], "-f", "csv", "-m", "longest",
               "-i", "0", "-k", "1", "-b", "0.1", "-x", str(settings["thxh"]),
               "-l", "candidates.txt", "-n", "source.txt"]
        if not settings["coarse_filter"]:
            # A word distance tops out at 10000, so at 10001 no pair of
            # segments is rejected: the filter is off.
            cmd += ["-d", "10001"]
        if settings["jobs"]:
            cmd += ["-j", str(settings["jobs"])]

        done = subprocess.run(cmd, cwd=sig_dir, capture_output=True, text=True)
        if done.returncode != 0:
            die(f"mpeg7dupes failed ({done.returncode}):\n{done.stderr.strip()[:600]}")

        for row in csv.DictReader(done.stdout.splitlines()):
            first = Path(row["First signature"]).name
            source_first = first == source_bin
            other = Path(row["Second signature"]).name if source_first else first
            # Which column is the source and which is the candidate is not
            # fixed: the outer loop is parallel and nothing promises an order.
            mine, theirs = ("1", "2") if source_first else ("2", "1")
            results[(source_bin, other)] = {
                "matchframes": float(row["matchframes"]),
                # The comparison's estimate of the speed of the second clip
                # relative to the first, turned round when the source is the
                # second so that the record always carries the candidate's
                # speed relative to the source. Anything but 1.0 is reported
                # with the ratio and what that means for the numbers.
                "framerateratio": (float(row["framerateratio"]) if source_first
                                   else round(1.0 / float(row["framerateratio"]), 6)),
                "t_source": float(row[f"time {mine} [s]"]),
                "t_other": float(row[f"time {theirs} [s]"]),
                # Where the match really starts and ends in each file, as
                # opposed to the seed above, which sits somewhere inside it.
                "source_begin": float(row[f"begin {mine} [s]"]),
                "source_end": float(row[f"end {mine} [s]"]),
                "other_begin": float(row[f"begin {theirs} [s]"]),
                "other_end": float(row[f"end {theirs} [s]"]),
                "whole": int(row["whole"]),
            }
        if len(source_bins) > 1:
            print(f"\r  comparing {i}/{len(source_bins)} sources", end="", flush=True)
    if len(source_bins) > 1:
        print()
    return results


def main() -> int:
    args = build_parser().parse_args()
    settings = load_settings(args)

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
    sig_dir.mkdir(parents=True, exist_ok=True)

    settings["mpeg7dupes"] = need(settings["mpeg7dupes"], "mpeg7dupes")
    settings["ffmpeg"] = need(settings["ffmpeg"], "ffmpeg")
    settings["ffprobe"] = need(settings["ffprobe"], "ffprobe")

    if settings["crop_bars"] and detect_bars is None:
        die("--crop-bars needs detect_bars.py beside this script. Pass "
            "--no-crop-bars to make a set that is knowingly uncropped, "
            "rather than one that might be either.")
    # Only meaningful when cropping, and the store leaves it out of the key
    # otherwise, so an uncropped set is not retired by a retuned detector.
    detector = detect_bars.DETECTOR_VERSION if settings["crop_bars"] else ""
    con = sigstore.open_db(Path(args.db) if args.db else sig_dir / INDEX_NAME)
    moved = sigstore.note_sig_dir(con, sig_dir)
    if moved:
        print(f"  note     this index last described {moved}; the signatures "
              f"it names are looked for in {sig_dir} now", file=sys.stderr)
    ffmpeg_ver = sigstore.ffmpeg_version(settings["ffmpeg"])
    if not settings["coarse_filter"]:
        print("coarse filter off: every pair of segments is walked (-d 10001), "
              "about three times as slow")

    sources = collect_videos(source_root, settings)
    if not sources:
        die(f"no videos under {source_root}")
    print(f"sources     {len(sources)}")
    # Signatures are content-addressed, so two copies of one video share a
    # filename. Every map below is therefore keyed on the signature and the
    # paths hang off it, rather than the other way round. A list of paths, not
    # one: a copy of a source is a source the caller named, and is reported.
    failures: list[dict] = []
    source_entries: dict[str, dict] = {}
    source_paths: dict[str, list[str]] = {}
    for i, video in enumerate(sources, 1):
        entry = make_signature(video, con, sig_dir, settings, detector,
                               ffmpeg_ver, failures, "source")
        if entry:
            source_entries[entry["filename"]] = entry
            # As walked, like the candidates below: a report has to point at
            # the file where the caller said it was.
            source_paths.setdefault(entry["filename"], []).append(str(video))
        print(f"\r  signatures {i}/{len(sources)}", end="", flush=True)
    con.commit()
    print()

    already = {v.resolve() for v in sources}
    requested = collect_videos(candidates_root, settings)
    # A candidate that is one of the sources is skipped, and says so in the
    # record: it would only ever match itself.
    skipped = [v for v in requested if v.resolve() in already]
    videos = [v for v in requested if v.resolve() not in already]
    if not videos:
        die(f"no videos under {candidates_root}"
            + (" that are not sources" if skipped else ""))

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
            entry = make_signature(video, con, sig_dir, settings, detector,
                                   ffmpeg_ver, failures, "candidate")
            if entry:
                entries[entry["filename"]] = entry
                shown.setdefault(entry["filename"], []).append(str(video))
            print(f"\r  signatures {i}/{len(videos)}", end="", flush=True)
        con.commit()
        print()

    signed = sum(len(paths) for paths in shown.values())
    duplicates = signed - len(shown)
    if duplicates:
        print(f"  {duplicates} of them are byte-for-byte copies of another, so "
              f"{len(shown)} signatures cover all {signed}")

    results = {}
    if source_entries and entries:
        results = compare(sig_dir, sorted(source_entries), sorted(entries),
                          settings)

    hits, matched, overran = [], set(), 0
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
        # How much of the source this candidate holds: the source's own frame
        # count is the denominator, which is what the question asks. The
        # duplicate-finding measure in benchmark.md divides by the shorter of
        # the two instead, so its 40 per cent is not this 40 per cent.
        coverage = 100.0 * frames / source_entry["frames"]
        best[name] = max(best.get(name, 0.0), coverage)
        if coverage < settings["min_coverage"]:
            continue
        if overrun:
            overran += 1
        matched.add(name)
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
                    "framerateratio": found["framerateratio"],
                    "matched_seconds": round(frames / settings["fps"], 3),
                    "source_seconds": round(source_entry["seconds"], 3),
                    "candidate_seconds": round(entries[name]["seconds"], 3),
                    "source_crop": source_entry["crop"],
                    "candidate_crop": entries[name]["crop"],
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

    # Sorted by source first, because the question being asked is which of my
    # clips were taken, not what is inside each of their videos.
    hits.sort(key=lambda h: (h["source"], h["candidate_path"]))
    print()
    for hit in hits:
        print(f"{hit['candidate_path']}   used {hit['source']}, {where_of(hit)}")
    misses = [path for name in entries if name not in matched
              for path in shown[name]]
    if args.show_misses:
        for name in sorted(entries, key=lambda n: shown[n][0]):
            if name not in matched:
                for path in shown[name]:
                    print(f"{path}   no sign of any source, best "
                          f"{best.get(name, 0.0):.0f}%")
    for failure in failures:
        print(f"{failure['path']}   could not be processed: "
              f"{failure['stage']}, {failure['reason']}")
    for video in skipped:
        print(f"{video}   skipped, it is one of the sources")

    # Files, not signatures: identical candidates share one signature but the
    # caller asked about each file, and each was answered for.
    processed = signed
    source_count = sum(len(paths) for paths in source_paths.values())
    print(f"\nscanned {processed} candidate{'s' if processed != 1 else ''} "
          f"against {source_count} source{'s' if source_count != 1 else ''}, "
          f"{len(hits)} match{'es' if len(hits) != 1 else ''} over "
          f"{settings['min_coverage']:.0f}%")
    if overran:
        print(f"{overran} of them ran past the end of the video they were found "
              f"in, so their length and position mean nothing. The match itself "
              f"still stands: something is shared, just not where it says.")
    if failures:
        print(f"{len(failures)} file{'s' if len(failures) != 1 else ''} could "
              f"not be processed, so this run is incomplete: exit status "
              f"{EXIT_INCOMPLETE}. The lines above and the JSON say which.")

    candidate_rows = video_list(entries, shown, settings["fps"])
    matched_paths = {path for name in matched for path in shown[name]}
    for row_ in candidate_rows:
        row_["status"] = MATCHED if row_["path"] in matched_paths else CHECKED
        row_["best_coverage_percent"] = round(best.get(
            next(n for n, ps in shown.items() if row_["path"] in ps), 0.0), 1)
    candidate_rows += failure_rows(failures, "candidate")
    candidate_rows += [{"name": v.name, "path": str(v), "status": SKIPPED,
                        "reason": "it is one of the sources"} for v in skipped]
    source_rows = video_list(source_entries, source_paths, settings["fps"])
    source_rows += failure_rows(failures, "source")
    summary = summarise(source_rows, candidate_rows, hits)

    if args.json:
        record = build_record(settings, source_rows, candidate_rows, hits,
                              misses, summary)
        try:
            Path(args.json).write_text(
                json.dumps(record, ensure_ascii=False, indent=1) + "\n")
        except OSError as exc:
            die(f"cannot write {args.json}: {exc}")
        print(f"wrote {args.json}")
    return summary["exit_status"]


if __name__ == "__main__":
    sys.exit(main())
