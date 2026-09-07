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

What it cannot do
-----------------
A video that barely moves has no signal to work with. If the middle of the frame
is as still as a bar would be, this says so rather than guessing. Slideshows, a
locked-off camera on an empty scene and long fades all land there.

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
import statistics
import subprocess
import sys
from pathlib import Path

# Columns kept per frame. See the note above on why this is neither 1 nor the
# full width.
WIDTH = 16
# Movement, in levels of an 8 bit grey value, above which a row counts as
# picture. Middle of the 2.5 to 6.5 plateau.
THRESHOLD = 5.0
# Consecutive rows that must stay above the threshold for the picture to have
# started. Longer than the runs compression produces inside a lettered bar,
# far shorter than any real bar.
RUN = 8
# Neither edge can be more of the frame than this. A frame that looks mostly
# bar is being misread, and the middle rows have to be picture.
MAX_BAR_FRACTION = 0.35
# Frames needed at one sampling point before that window counts.
MIN_WINDOW = 4


def probe(path):
    """Height and duration, or None when ffprobe cannot read the file."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=height:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True)
    if out.returncode != 0:
        return None
    try:
        d = json.loads(out.stdout)
        return int(d["streams"][0]["height"]), float(d["format"]["duration"])
    except (KeyError, IndexError, ValueError):
        return None


def movement(path, height, duration, points, per_point):
    """Per row, the most any of its columns moved within any one window."""
    windows = []
    for i in range(points):
        # Spread over the middle 80%, avoiding titles at the very start and
        # credits at the very end, both of which are often still.
        at = duration * (0.1 + 0.8 * i / max(1, points - 1))
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", str(path),
             "-vf", f"scale={WIDTH}:ih:flags=area,format=gray",
             "-frames:v", str(per_point), "-f", "rawvideo", "-"],
            capture_output=True).stdout
        stride = height * WIDTH
        frames = [raw[j * stride:(j + 1) * stride]
                  for j in range(len(raw) // stride)]
        if len(frames) < MIN_WINDOW:
            continue
        windows.append([
            max(statistics.pstdev([f[r * WIDTH + c] for f in frames])
                for c in range(WIDTH))
            for r in range(height)
        ])
    if not windows:
        return None
    return [max(w[r] for w in windows) for r in range(height)]


def edge(noise, threshold, reverse):
    """How many rows of bar there are at one edge."""
    rows = list(range(len(noise) - 1, -1, -1) if reverse
                else range(len(noise)))
    for i in range(int(len(noise) * MAX_BAR_FRACTION)):
        window = rows[i:i + RUN]
        if len(window) == RUN and all(noise[r] > threshold for r in window):
            return i
    return 0


def analyse(path, points, per_point, threshold, min_fraction):
    """One video. 'status' says whether the rest of the result means anything."""
    info = probe(path)
    if not info:
        return {"path": str(path), "status": "unreadable"}
    height, duration = info

    noise = movement(path, height, duration, points, per_point)
    if noise is None:
        return {"path": str(path), "status": "too short", "height": height}

    # The middle of the frame is picture whatever else is going on. If it is not
    # moving either, nothing here can tell a bar from a quiet shot. Median
    # rather than mean, so one busy strip cannot carry a still frame.
    lo, hi = height // 4, height - height // 4
    middle = statistics.median(noise[lo:hi])
    if middle <= threshold:
        return {"path": str(path), "status": "too static", "height": height,
                "middle": middle}

    top = edge(noise, threshold, reverse=False)
    bottom = edge(noise, threshold, reverse=True)
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
    p.add_argument("--points", type=int, default=6, metavar="N",
                   help="places along the video to sample (default 6)")
    p.add_argument("--frames", type=int, default=12, metavar="N",
                   help="frames to take at each place (default 12)")
    p.add_argument("--threshold", type=float, default=THRESHOLD, metavar="F",
                   help=f"movement above which a row is picture "
                        f"(default {THRESHOLD})")
    p.add_argument("--min-fraction", type=float, default=0.02, metavar="F",
                   help="bars thinner than this fraction of the height are "
                        "reported as none, a row or two being rounding rather "
                        "than a bar (default 0.02)")
    args = p.parse_args()

    if args.csv:
        print("file,status,height,top,bottom,picture_height,bar_percent,crop")

    worst = 0
    for path in args.videos:
        r = analyse(path, args.points, args.frames,
                    args.threshold, args.min_fraction)
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

        if r["status"] == "unreadable":
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
