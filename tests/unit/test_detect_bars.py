"""The bar detector's decision, from sampled frames to a crop.

    sh tests/tools.sh          # or: uv run tests/unit/test_detect_bars.py

ffprobe and ffmpeg are replaced here: probe reports a fixed height and
duration, and each sampling call gets frames built by hand for the window it
asked for, told apart by its -ss. So what runs is the detector's own analyse(),
start to finish, on pictures whose bars are known.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import detect_bars  # noqa: E402

H = 64          # rows in the stand-in picture
BAR = 10        # rows of bar, top and bottom, where a scene has them
DURATION = 100.0
STILL, BLACK, TEXT = 120, 16, 230


def moving(j: int) -> int:
    """A value that changes from frame to frame, as picture does."""
    return 60 + 80 * (j % 2)


def analyse(scene):
    """detect_bars.analyse on frames drawn by scene(window, frame, row)."""
    def run(cmd, **_):
        at = float(cmd[cmd.index("-ss") + 1])
        window = round((at / DURATION - 0.1) / 0.8 * (detect_bars.POINTS - 1))
        frames = b"".join(
            bytes(scene(window, j, r) for r in range(H) for _ in range(detect_bars.WIDTH))
            for j in range(detect_bars.PER_POINT))
        return SimpleNamespace(stdout=frames, returncode=0)

    with mock.patch.object(detect_bars, "probe", lambda path, ffprobe="ffprobe": (H, DURATION)), \
            mock.patch.object(detect_bars.subprocess, "run", run):
        return detect_bars.analyse(
            "stand-in.mp4", detect_bars.POINTS, detect_bars.PER_POINT,
            detect_bars.THRESHOLD, detect_bars.MIN_FRACTION)


def edges(result):
    return result["status"], result.get("top"), result.get("bottom")


def in_bar(r: int) -> bool:
    return r < BAR or r >= H - BAR


class Decision(unittest.TestCase):
    def test_a_still_shot_with_a_moving_clip_spliced_in_has_no_bars(self):
        """The validation set's false crops, drawn small. Five windows land on
        a still shot; the sixth lands on a spliced-in clip whose middle moves
        and whose top and bottom happen to be still, but show something else.
        Detector version 1 called those rows bars and cropped the picture."""
        def scene(window, j, r):
            if window < 5:
                return STILL
            return 20 if in_bar(r) else moving(j)
        self.assertEqual(edges(analyse(scene)), ("ok", 0, 0))

    def test_bars_on_moving_picture_are_found(self):
        def scene(window, j, r):
            return BLACK if in_bar(r) else moving(j + window)
        self.assertEqual(edges(analyse(scene)), ("ok", BAR, BAR))

    def test_bars_on_a_still_shot_with_a_moving_advertisement_are_found(self):
        """combo on a still source: one window moves, and the bars are the
        same in all six, so that one window is enough evidence."""
        def scene(window, j, r):
            if in_bar(r):
                return BLACK
            return STILL if window < 5 else moving(j)
        self.assertEqual(edges(analyse(scene)), ("ok", BAR, BAR))

    def test_lettering_inside_a_bar_does_not_end_it(self):
        def scene(window, j, r):
            if in_bar(r):
                return TEXT if r in (3, 4, 5, H - 6, H - 5) else BLACK
            return moving(j + window)
        self.assertEqual(edges(analyse(scene)), ("ok", BAR, BAR))

    def test_a_shot_that_is_still_throughout_cannot_be_told(self):
        def scene(window, j, r):
            return BLACK if in_bar(r) else STILL
        self.assertEqual(analyse(scene)["status"], "too static")


if __name__ == "__main__":
    unittest.main(verbosity=2)
