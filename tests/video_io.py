"""Real ffmpeg regressions for short files, stream choice and autorotation.

Run by make smoke, not by the no-ffmpeg unit suite.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import detect_bars
import sigmake
from video_fixtures import rotate_90


class VideoIO(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def ffmpeg(self, *args):
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', *map(str, args)],
                       check=True, capture_output=True)

    def test_real_short_signatures_can_be_compared(self):
        for frames in (1, 2, 5):
            with self.subTest(frames=frames):
                video = self.root / f"short-{frames}.mp4"
                self.ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=5',
                            '-frames:v', str(frames), '-c:v', 'libx264', video)
                for name in ('a.sig', 'b.sig'):
                    header = sigmake.produce(video, self.root, name, '', 5, 'ffmpeg')
                    self.assertEqual(header['frames'], frames)
                binary = os.environ['MPEG7DUPES']
                done = subprocess.run([binary, '-j', '1', 'a.sig', 'b.sig'], cwd=self.root,
                                      capture_output=True, text=True, timeout=15)
                self.assertEqual(done.returncode, 0, done.stderr)
                # Safety is the contract; short, textured clips may match.
                if frames <= 2:
                    self.assertEqual(len(done.stdout.splitlines()), 1)

    def test_multiple_tracks_sample_the_first_track(self):
        video = self.root / 'multi.mp4'
        first = self.root / 'first.mp4'
        self.ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=10',
                    '-f', 'lavfi', '-i', 'testsrc2=size=128x96:rate=10',
                    '-t', '2', '-map', '0:v:0', '-map', '1:v:0',
                    '-c:v', 'libx264', '-preset', 'ultrafast', video)
        self.ffmpeg('-i', video, '-map', '0:v:0', '-c', 'copy', first)
        height, duration = detect_bars.probe(video)
        self.assertEqual(height, 48)
        self.assertEqual(detect_bars.movement(video, height, duration, 2, 8),
                         detect_bars.movement(first, height, duration, 2, 8))

    def test_rotated_height_matches_actual_decoded_frame(self):
        plain, rotated = self.root / 'plain.mp4', self.root / 'rotated.mp4'
        self.ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=10', '-t', '2',
                    '-c:v', 'libx264', '-preset', 'ultrafast', plain)
        rotate_90(plain, rotated)
        height, duration = detect_bars.probe(rotated)
        self.assertEqual(height, 64)
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(rotated),
                              '-map', '0:v:0', '-vf', 'scale=16:ih,format=gray',
                              '-frames:v', '8', '-f', 'rawvideo', '-'],
                             check=True, capture_output=True).stdout
        self.assertEqual(len(raw), 8 * 16 * height)
        noise, drift = detect_bars.movement(rotated, height, duration, 2, 8)
        self.assertEqual((len(noise), len(drift)), (height, height))


if __name__ == '__main__':
    unittest.main(verbosity=2)
