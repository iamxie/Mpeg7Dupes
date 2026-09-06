# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
find_reuse.py
=============

Takes one video as the reference and scans a folder for files that contain it.

The situation this is for: you made a short film, and someone tells you a
company cut it into one of their videos. You do not need to know exactly how
many seconds they took. You need to know that it is over some proportion, and
roughly where, which is enough to go and comment under their video.

    uv run tools/find_reuse.py --source mine.mp4 --candidates ./downloads

    ./downloads/a.mp4   used mine.mp4, starting at 02:53
    ./downloads/k.mp4   used mine.mp4, starting between 00:00 and 01:10
    scanned 128 candidates, 2 over 40%

Why this wrapper exists
-----------------------
mpeg7dupes reads signatures, not video: it has no decoder. And decoding is by
far the slow part. Measured on 96 files, generating the signatures took 19
minutes and comparing them took 45 seconds. So the signatures have to be kept
and reused, which makes checking a second reference against the same folder
almost free. This script generates them, caches them, does the arithmetic and
prints the answer.

Two numbers not to quote
------------------------
1. Coverage is overstated. Measured: where the other video used 30% of the
   reference, this reports 62%, and the less they used the worse it gets. So
   the output says only that a threshold was passed, never a percentage. It
   also means --min-coverage 40 fires at around 20% of real use. That bias is
   deliberate: better to look at a few extra videos than to miss one.
2. The start is a range, not a point. What the comparison reports is where
   frame 0 of the reference would sit in the other video, so it lands early by
   however much of the reference's head was skipped. The real start is
   therefore between that offset and one reference-length later, and the output
   says so. When the whole reference was used, nothing was skipped and the
   offset is exact; that case is reported as a single timestamp, and was right
   to the second on all 36 videos it was measured against.

Precedence
----------
    command line > the toml named by --config > built-in defaults

Usage
-----
    uv run tools/find_reuse.py --source mine.mp4 --candidates ./downloads
    uv run tools/find_reuse.py --source mine.mp4 --candidates ./downloads \\
        --min-coverage 30 --all
"""

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

DEFAULTS = {
    "fps": 5.0,
    "min_coverage": 40.0,
    # Measured over 96 videos: raising this from 60 to 290 took two whole
    # families of edit, black bars and black bars with text, from never
    # detected to always detected, and added no false positives. Below it they
    # are missed entirely rather than partly.
    "thxh": 290,
    "jobs": 0,  # 0 leaves it to mpeg7dupes, which uses every core
    "overwrite": False,
    "extensions": ["mp4", "mkv", "avi", "mov", "wmv", "flv", "ts", "m4v", "webm", "mpg", "mpeg"],
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffprobe",
    "mpeg7dupes": "mpeg7dupes",
}

INDEX_NAME = "index.json"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Find which videos in a folder contain a given source video.")
    p.add_argument("--source", required=True, metavar="FILE",
                   help="The video to look for.")
    p.add_argument("--candidates", required=True, metavar="PATH",
                   help="Folder to search, walked recursively, or one file.")
    p.add_argument("--min-coverage", type=float, metavar="PCT",
                   help="Report a candidate at this much of the source, in percent. Default 40.")
    p.add_argument("--sig-dir", metavar="DIR",
                   help="Where to cache signatures. Defaults to .signatures under the candidates.")
    p.add_argument("--fps", type=float, metavar="N",
                   help="Sampling rate. Source and candidates must match. Default 5.")
    p.add_argument("--thxh", type=int, metavar="N",
                   help="Frame similarity threshold. Default 290.")
    p.add_argument("--jobs", type=int, metavar="N", help="Cores for the comparison. Default all.")
    p.add_argument("--all", action="store_true", help="List the candidates that did not match too.")
    p.add_argument("--overwrite", action="store_true", help="Recompute signatures that already exist.")
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
        if value not in (None, False):
            settings[key] = value
    return settings


def need(program: str, what: str) -> str:
    found = shutil.which(program) or (program if Path(program).is_file() else None)
    if not found:
        sys.exit(f"cannot find {what}: {program}")
    return found


def duration_of(path: Path, ffprobe: str) -> float:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"cannot read the duration of {path}: {out.stderr.strip()}")
    return float(out.stdout.strip())


def sig_name(path: Path) -> str:
    """Name signatures after a hash of the absolute path, to avoid spaces.

    mpeg7dupes reads its list file with fscanf("%s"), which stops at the first
    space, so a signature named after the video would break on many of them.
    """
    return hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16] + ".bin"


def make_signature(src: Path, sig_dir: Path, settings: dict, index: dict) -> dict | None:
    """Compute one signature and return its index entry, or reuse a current one."""
    name = sig_name(src)
    stat = src.stat()
    entry = index.get(name)
    fresh = (entry
             and not settings["overwrite"]
             and entry["size"] == stat.st_size
             and entry["mtime"] == int(stat.st_mtime)
             and entry["fps"] == settings["fps"]
             and (sig_dir / name).is_file()
             and (sig_dir / name).stat().st_size > 0)
    if fresh:
        return entry

    try:
        seconds = duration_of(src, settings["ffprobe"])
    except (RuntimeError, ValueError) as exc:
        print(f"  skipped  {src.name}: {exc}", file=sys.stderr)
        return None

    # Only the filename goes in the filtergraph, with cwd locating the output,
    # because a Windows path in there needs escaping that is easy to get wrong.
    cmd = [settings["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "error",
           "-i", str(src.resolve()), "-map", "0:v:0", "-an",
           "-vf", f"fps={settings['fps']},signature=filename={name}",
           "-f", "null", "-"]
    done = subprocess.run(cmd, cwd=sig_dir, capture_output=True, text=True)
    if done.returncode != 0 or not (sig_dir / name).is_file():
        print(f"  skipped  {src.name}: ffmpeg failed, {done.stderr.strip()[:120]}",
              file=sys.stderr)
        return None

    entry = {"path": str(src.resolve()), "seconds": seconds,
             "frames": round(seconds * settings["fps"]), "fps": settings["fps"],
             "size": stat.st_size, "mtime": int(stat.st_mtime)}
    index[name] = entry
    return entry


def collect_videos(root: Path, settings: dict) -> list[Path]:
    if root.is_file():
        return [root]
    wanted = {"." + e.lower().lstrip(".") for e in settings["extensions"]}
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in wanted)


def as_clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def compare(sig_dir: Path, source_bin: str, candidate_bins: list[str],
            settings: dict) -> dict[str, dict]:
    """Run one comparison and return a result per candidate signature."""
    (sig_dir / "candidates.txt").write_text("\n".join(candidate_bins) + "\n")
    (sig_dir / "source.txt").write_text(source_bin + "\n")

    # -i 0 because any other value truncates the match. -k 1 and -b 0.1 so
    # every candidate comes back and the threshold is applied here instead.
    cmd = [settings["mpeg7dupes"], "-f", "csv", "-m", "full",
           "-i", "0", "-k", "1", "-b", "0.1", "-x", str(settings["thxh"]),
           "-l", "candidates.txt", "-n", "source.txt"]
    if settings["jobs"]:
        cmd += ["-j", str(settings["jobs"])]

    done = subprocess.run(cmd, cwd=sig_dir, capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"mpeg7dupes failed ({done.returncode}):\n{done.stderr.strip()[:600]}")

    results = {}
    for row in csv.DictReader(done.stdout.splitlines()):
        first, second = row["First signature"], row["Second signature"]
        source_first = Path(first).name == source_bin
        other = Path(second if source_first else first).name
        results[other] = {
            "matchframes": float(row["matchframes"]),
            "t_source": float(row["time 1 [s]"] if source_first else row["time 2 [s]"]),
            "t_other": float(row["time 2 [s]"] if source_first else row["time 1 [s]"]),
            "whole": int(row["whole"]),
        }
    return results


def main() -> int:
    args = build_parser().parse_args()
    settings = load_settings(args)

    source = Path(args.source)
    candidates_root = Path(args.candidates)
    if not source.exists():
        sys.exit(f"no such source: {source}")
    if not candidates_root.exists():
        sys.exit(f"no such candidates path: {candidates_root}")
    if source.suffix.lower() == ".bin":
        sys.exit("--source must be a video: a signature carries no duration, "
                 "so there is nothing to take a proportion of.")

    sig_dir = Path(args.sig_dir) if args.sig_dir else (
        candidates_root if candidates_root.is_dir() else candidates_root.parent) / ".signatures"
    sig_dir.mkdir(parents=True, exist_ok=True)

    index_path = sig_dir / INDEX_NAME
    index = json.loads(index_path.read_text()) if index_path.is_file() else {}

    settings["mpeg7dupes"] = need(settings["mpeg7dupes"], "mpeg7dupes")
    settings["ffmpeg"] = need(settings["ffmpeg"], "ffmpeg")
    settings["ffprobe"] = need(settings["ffprobe"], "ffprobe")

    print(f"source      {source.name}")
    source_entry = make_signature(source, sig_dir, settings, index)
    if not source_entry:
        sys.exit("cannot compute the signature of the source")
    source_bin = sig_name(source)
    source_frames = source_entry["frames"]

    videos = [v for v in collect_videos(candidates_root, settings)
              if v.resolve() != source.resolve()]
    if not videos:
        sys.exit(f"no videos under {candidates_root}")

    print(f"candidates  {len(videos)}, signatures cached in {sig_dir}")
    entries = {}
    # Display the path as it was walked, not the resolved one. Identifying a
    # file needs the real path, so the same video reached two ways is not
    # signed twice, but what is reported has to be where the caller said to
    # look, or a symlink sends them somewhere they never named.
    shown = {}
    for i, video in enumerate(videos, 1):
        entry = make_signature(video, sig_dir, settings, index)
        if entry:
            entries[sig_name(video)] = entry
            shown[sig_name(video)] = str(video)
        print(f"\r  signatures {i}/{len(videos)}", end="", flush=True)
    print()
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1))

    if not entries:
        sys.exit("no candidate signatures to compare")

    results = compare(sig_dir, source_bin, sorted(entries), settings)

    hits, misses = [], []
    for name, entry in entries.items():
        found = results.get(name)
        coverage = 100.0 * found["matchframes"] / source_frames if found else 0.0
        if coverage >= settings["min_coverage"]:
            # The offset says where frame 0 of the source would sit in the
            # other video, so it lands early by however much of the source's
            # head was skipped. The real start is within one source length of
            # it. Unless the whole source was used: then nothing was skipped
            # and the offset is the answer, which held to the second on all 36
            # videos it was measured against.
            start = found["t_other"] - found["t_source"]
            exact = found["whole"] == 1 and coverage >= 95.0
            hits.append((shown[name], start,
                         None if exact else start + source_entry["seconds"]))
        else:
            misses.append(shown[name])

    print()
    for path, start, end in sorted(hits):
        where = (f"starting at {as_clock(start)}" if end is None
                 else f"starting between {as_clock(start)} and {as_clock(end)}")
        print(f"{path}   used {source.name}, {where}")
    if args.all:
        for path in sorted(misses):
            print(f"{path}   no sign of {source.name}")
    print(f"\nscanned {len(entries)} candidates, {len(hits)} over "
          f"{settings['min_coverage']:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
