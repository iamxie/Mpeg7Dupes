# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
detect_bars.py
==============

Reports whether a video has bars added along its top and bottom, and where the
real picture starts and ends.

Bars matter here because of what they do to a comparison. They are usually the
same 140 or so pixels of black in every video that carries them, which is a
fifth of the frame, so two unrelated videos that both have bars agree on a fifth
of every frame before anything else is considered. `benchmark.md` measures the
result: raising `-x` from 290 to 310 takes the highest coverage reached by an
unrelated pair from 20.9 to 50.1 per cent, and the twelve worst offenders all
have bars.

Why not ffmpeg's cropdetect
---------------------------
cropdetect looks for rows that are dark. That finds a plain black bar exactly,
and misses the case worth catching: a bar with advertising text in it. The text
is bright, so cropdetect stops at the first line of it. On this corpus it read a
1920x1360 lettered video as `crop=1920:1254:0:48` where the answer is
`crop=1920:1080:0:140`, recovering 48 of 140 rows.

This looks for rows that do not change. A bar is painted on after the fact, so
it is identical in every frame whatever it contains, and the picture underneath
it is not. Text in the bar changes nothing, because the text does not move
either.

How it works
------------
Each frame is scaled to WIDTH pixels across, which lets ffmpeg do the averaging
and leaves one small number per row per column. Frames are sampled at several
points along the video, and each row gets a score: the largest standard
deviation over time of any of its columns, in any one sampling window.

Three choices in that sentence, each of which was wrong first:

  Windows are kept apart, not pooled. Pooled frames measure how much a row
  differs between scenes, which is large everywhere and largest in the picture,
  and a row that is merely consistently dark then reads as still.

  The score is the maximum across windows, not the mean. A bar is still in every
  window, so one window with movement is enough to rule a row out, and a scene
  that happens to hold still must not promote picture rows to bar.

  WIDTH is 16 rather than 1. Averaging a whole row into one number suppresses
  compression noise, which separates bars from lettered bars beautifully, but it
  also averages away anything small: one corpus video is dark along its top edge
  and the only thing moving there is a frame counter in the corner, which
  vanishes at WIDTH 1 and takes the picture's top 49 rows with it.

The boundary is not the first row above the threshold. Compression makes rows
inside a lettered bar twitch, so a single row proves nothing. The bar ends where
the profile rises and stays up, asked as "the next RUN rows are all above the
threshold".

Still is not enough
-------------------
Identical in every frame has two halves, and version 1 of this detector asked
only one of them: did the row move within a window. It never asked whether the
row looked the same in one window as in another. On footage that is still
apart from a short moving stretch, that is not enough. Five windows land on the
still shot, where nothing moves; the sixth lands on the moving stretch, whose
middle moves, which lifts the guard for still footage below, and whose top and
bottom may happen to hold still for that half second. Every edge row is then
still in every window and reads as bar, although the still shot's sky and the
spliced-in clip's ceiling are different pictures. The independent validation
set had three such files, none of them with bars, and version 1 cropped 136,
274 and 482 of their 1080 rows off.

So a row is also picture when its average over a window, in any column,
differs from its average over another window by more than DRIFT. This is not
the pooling rejected above: it can only make a row picture, never bar, so a row
that is merely dark in every window is judged by movement exactly as before.

What that gives up: a bar whose lettering changes during the video, a rotating
slogan say, now reads as picture from the lettering on, when the changing
lettering is RUN rows tall or more. The crop then stops short of it instead of
taking picture with it.

Measured
--------
96 videos, 6 sources cut two ways and edited eight, 36 of them with bars of a
known height:

    has bars, yes or no          96 of 96
    bar height exactly right     95 of 96
    worst error                  1 row

WIDTH 16 holds 96 of 96 with an error of at most 2 rows for every threshold from
2.5 to 6.5, so the value below sits in the middle of a plateau rather than on a
peak. WIDTH 8 works too over a narrower range; WIDTH 1 falls to 95 of 96.

Version 2 answers the same on those 96. On the 92 files of the independent
validation set it takes the three false crops to no bars and moves nothing else
by a row: 14 of the 18 barred files found within a row, the other four too
still to tell, as with version 1. Every DRIFT from 12 to 70 gives exactly that
on all 188 videos; below 10 the lettering inside a bar starts to split it, and
from 75 the spliced-in clips' still edges start to read as bar again. DRIFT was
chosen after looking at the validation set, which is tuning material for the
detector from version 2 on.

The second validation set, 145 files from ten other sources, was not looked at
when DRIFT was chosen. On its 116 edited copies version 2 cropped none without
bars and missed none. It found the bars within a row on 21, and 2 rows short
on 5, where the encoder smeared the picture into the bar; it left the 65
without bars alone; it declined 10 as too still; it cropped a still band with
the bars on 6, as the next section says; the other 9 have no single right
answer, their bars being there for part of the length or blurred into the
picture. A source's own 128-row
letterbox was found exactly. One source, a clip of moving clouds over a still
city skyline, lost the 103 rows of skyline along its bottom edge.

What it cannot do
-----------------
A video that barely moves has no signal to work with. If the middle of the frame
is as still as a bar would be, this says so rather than guessing. Slideshows, a
locked-off camera on an empty scene and long fades all land there.

When the moving stretch of a mostly still video holds still at its edges and
shows the same thing there as the still shot does, black above and below both
say, nothing in the samples tells those rows from a bar.

Anything across the whole width that never changes is a bar by this
definition: a news ticker whose text stays the same, a caption strip, still
scenery along the bottom of moving footage. It is cropped with the bars. That
costs a comparison nothing when every copy is cropped the same way, which is
what happened to a 119-row ticker on the second validation set; it takes out
whatever the band shows.

Bars whose content moves are not bars by this definition and will not be found:
an animated banner, a live scoreboard, a clock. Those are also not the case that
hurts a comparison, since they differ between videos rather than being the same
black everywhere.

Top and bottom only. Pillarboxing is the same idea rotated and is not
implemented.

Usage
-----
    uv run detect_bars.py video.mp4
    uv run detect_bars.py *.mp4 --csv
"""

import argparse
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path
from tool_settings import ToolError, run_tool

# Columns kept per frame. See the note above on why this is neither 1 nor the
# full width.
WIDTH = 16
# Movement, in levels of an 8 bit grey value, above which a row counts as
# picture. Middle of the 2.5 to 6.5 plateau.
THRESHOLD = 5.0
# How far a row's average over one window may differ from its average over
# another, in any column, in levels of an 8 bit grey value, before the row
# counts as picture even though it never moved. See "Still is not enough".
# Middle, on a log scale, of the 12 to 70 plateau measured below.
DRIFT = 30.0
# Consecutive rows that must stay above the threshold for the picture to have
# started. Longer than the runs compression produces inside a lettered bar,
# far shorter than any real bar.
RUN = 8
# Neither edge can be more of the frame than this. A frame that looks mostly
# bar is being misread, and the middle rows have to be picture.
MAX_BAR_FRACTION = 0.35
# Frames needed at one sampling point before that window counts.
MIN_WINDOW = 4
# Places along the video to sample, and frames to take at each. Constants
# rather than call-site arguments because they change where a boundary lands,
# so two callers passing different values would crop the same video two ways
# while DETECTOR_VERSION claimed they agreed.
POINTS = 6
PER_POINT = 12
# Bars thinner than this fraction of the height are reported as none: a row or
# two is rounding, not a bar.
MIN_FRACTION = 0.02

# Bump when a change here would crop a video differently: the constants above,
# the sampling, the edge rule, anything that moves a boundary by a row.
#
# A signature taken from a cropped video is only comparable with one cropped
# the same way, so a caller that keeps signatures has to be able to tell which
# detector made them. Callers put this in their cache key; a bump therefore
# invalidates exactly the signatures that are now wrong and leaves the
# uncropped ones alone. Without it a retuned detector goes on silently serving
# signatures taken from a different picture, which is the same class of fault
# as comparing results from two builds of the binary.
#
# A change to a docstring or a message does not need a bump. Bumping anyway
# costs a re-fingerprint; being wrong costs a wrong answer with no symptom.
#
# 2: rows whose content differs between windows are picture (DRIFT).
# 3: select v:0 and use its autorotated height; sampling failures are errors.
# Thresholds and motion classification are unchanged.
DETECTOR_VERSION = "3"


def probe(path, ffprobe="ffprobe"):
    """Height and duration, or None when ffprobe cannot read the file."""
    out = run_tool(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:stream_tags=rotate:stream_side_data=rotation:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True)
    if out.returncode != 0:
        return None
    try:
        d = json.loads(out.stdout)
        stream = d["streams"][0]
        height, duration = int(stream["height"]), float(d["format"]["duration"])
        rotation = stream.get("tags", {}).get("rotate", 0)
        for side in stream.get("side_data_list", []):
            if "rotation" in side:
                rotation = side["rotation"]
        rotation = float(rotation)
        if not math.isfinite(rotation) or rotation % 90:
            return None
        if rotation % 180:
            height = int(stream["width"])
        if height <= 0 or not math.isfinite(duration) or duration <= 0:
            return None
        return height, duration
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def movement(path, height, duration, points, per_point, ffmpeg="ffmpeg"):
    """Two profiles, one number per row: the most any of its columns moved
    within any one window, and the most any column's average differed between
    two windows. None when no window had enough frames."""
    within, means = [], []
    for i in range(points):
        # Spread over the middle 80%, avoiding titles at the very start and
        # credits at the very end, both of which are often still.
        at = duration * (0.1 + 0.8 * i / max(1, points - 1))
        done = run_tool(
            [ffmpeg, "-v", "error", "-ss", f"{at:.2f}", "-i", str(path),
             "-map", "0:v:0", "-an", "-vf", f"scale={WIDTH}:ih:flags=area,format=gray",
             "-frames:v", str(per_point), "-f", "rawvideo", "-"],
            capture_output=True)
        if done.returncode:
            raise RuntimeError(f"ffmpeg sampling failed ({done.returncode}): "
                               + done.stderr.decode(errors="replace").strip()[:200])
        raw = done.stdout
        stride = height * WIDTH
        if len(raw) % stride:
            raise RuntimeError("ffmpeg sampling returned a partial raw frame")
        frames = [raw[j * stride:(j + 1) * stride]
                  for j in range(len(raw) // stride)]
        if len(frames) < MIN_WINDOW:
            continue
        within.append([
            max(statistics.pstdev([f[r * WIDTH + c] for f in frames])
                for c in range(WIDTH))
            for r in range(height)
        ])
        means.append([
            [statistics.fmean([f[r * WIDTH + c] for f in frames])
             for c in range(WIDTH)]
            for r in range(height)
        ])
    if not within:
        return None
    noise = [max(w[r] for w in within) for r in range(height)]
    drift = [max(max(m[r][c] for m in means) - min(m[r][c] for m in means)
                 for c in range(WIDTH))
             for r in range(height)]
    return noise, drift


def edge(picture, reverse):
    """How many rows of bar there are at one edge: the rows before the first
    run of RUN rows that are all picture."""
    rows = list(range(len(picture) - 1, -1, -1) if reverse
                else range(len(picture)))
    for i in range(int(len(picture) * MAX_BAR_FRACTION)):
        window = rows[i:i + RUN]
        if len(window) == RUN and all(picture[r] for r in window):
            return i
    return 0


def analyse(path, points, per_point, threshold, min_fraction,
            ffmpeg="ffmpeg", ffprobe="ffprobe"):
    """One video. 'status' says whether the rest of the result means anything.

    ffmpeg and ffprobe are the programs to run, so a caller that was told
    which ones to use can pass that on; the defaults are whatever PATH has.
    """
    info = probe(path, ffprobe)
    if not info:
        return {"path": str(path), "status": "unreadable"}
    height, duration = info

    try:
        profiles = movement(path, height, duration, points, per_point, ffmpeg)
    except ToolError:
        raise
    except (OSError, RuntimeError) as exc:
        return {"path": str(path), "status": "failed", "reason": str(exc)}
    if profiles is None:
        return {"path": str(path), "status": "too short", "height": height}
    noise, drift = profiles

    # The middle of the frame is picture whatever else is going on. If it is not
    # moving either, nothing here can tell a bar from a quiet shot. Median
    # rather than mean, so one busy strip cannot carry a still frame.
    lo, hi = height // 4, height - height // 4
    middle = statistics.median(noise[lo:hi])
    if middle <= threshold:
        return {"path": str(path), "status": "too static", "height": height,
                "middle": middle}

    # Picture moves within a window or looks different from one window to
    # the next; a bar does neither.
    picture = [n > threshold or d > DRIFT for n, d in zip(noise, drift)]
    top = edge(picture, reverse=False)
    bottom = edge(picture, reverse=True)
    fraction = (top + bottom) / height
    if fraction < min_fraction:
        top = bottom = 0
        fraction = 0.0
    return {
        "path": str(path), "status": "ok", "height": height, "middle": middle,
        "top": top, "bottom": bottom, "picture_height": height - top - bottom,
        "bar_fraction": fraction,
    }


def main():
    p = argparse.ArgumentParser(
        description="Detect bars added to the top and bottom of a video")
    p.add_argument("videos", nargs="+", type=Path)
    p.add_argument("--csv", action="store_true",
                   help="one row per video instead of a report")
    p.add_argument("--points", type=int, default=POINTS, metavar="N",
                   help=f"places along the video to sample (default {POINTS})")
    p.add_argument("--frames", type=int, default=PER_POINT, metavar="N",
                   help=f"frames to take at each place (default {PER_POINT})")
    p.add_argument("--threshold", type=float, default=THRESHOLD, metavar="F",
                   help=f"movement above which a row is picture "
                        f"(default {THRESHOLD})")
    p.add_argument("--min-fraction", type=float, default=MIN_FRACTION, metavar="F",
                   help=f"bars thinner than this fraction of the height are "
                        f"reported as none, a row or two being rounding rather "
                        f"than a bar (default {MIN_FRACTION})")
    p.add_argument("--ffmpeg", default="ffmpeg", metavar="PATH")
    p.add_argument("--ffprobe", default="ffprobe", metavar="PATH")
    args = p.parse_args()

    if args.csv:
        print("file,status,height,top,bottom,picture_height,bar_percent,crop")

    worst = 0
    for path in args.videos:
        try:
            r = analyse(path, args.points, args.frames,
                        args.threshold, args.min_fraction,
                        ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
        except ToolError as exc:
            r = {"path": str(path), "status": "failed", "reason": str(exc)}
        name = Path(r["path"]).name
        crop = ""
        if r["status"] == "ok" and r["bar_fraction"]:
            crop = f"crop=iw:{r['picture_height']}:0:{r['top']}"

        if args.csv:
            if r["status"] != "ok":
                print(f"{name},{r['status']},{r.get('height','')},,,,,")
            else:
                print(f"{name},ok,{r['height']},{r['top']},{r['bottom']},"
                      f"{r['picture_height']},{r['bar_fraction']*100:.1f},{crop}")
            worst = max(worst, 0 if r["status"] == "ok" else 2)
            continue

        if r["status"] == "failed":
            print(f"{name}: {r['reason']}")
            worst = max(worst, 2)
        elif r["status"] == "unreadable":
            print(f"{name}: cannot read")
            worst = max(worst, 2)
        elif r["status"] == "too short":
            print(f"{name}: too short to sample")
            worst = max(worst, 2)
        elif r["status"] == "too static":
            print(f"{name}: too static to tell, the middle of the frame moves "
                  f"{r['middle']:.1f} and a bar would move 0")
            worst = max(worst, 2)
        elif not r["bar_fraction"]:
            print(f"{name}: no bars, all {r['height']} rows are picture")
        else:
            print(f"{name}: bars, {r['top']} rows top and {r['bottom']} bottom "
                  f"of {r['height']}, {r['bar_fraction']*100:.0f}% of the frame")
            print(f"    picture is {r['picture_height']} rows: -vf {crop}")
            worst = max(worst, 1)

    return worst


if __name__ == "__main__":
    sys.exit(main())
