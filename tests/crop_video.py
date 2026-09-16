"""Generated crop-view development regressions using real ffmpeg and C."""
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import sigmake
import sigstore
from video_fixtures import rotate_90


class CropVideo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.con = sigstore.open_db(self.root / 'index.sqlite')

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def ffmpeg(self, *args):
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', *map(str, args)],
                       check=True, capture_output=True, timeout=90)

    def test_exact_geometry_and_autorotation_match_explicit_filters(self):
        for width, height, rotate, edge in ((1000, 600, False, 30), (360, 638, False, 32),
                                          (600, 360, True, 30)):
            with self.subTest(width=width, height=height, rotate=rotate):
                video = self.root / f'{width}-{height}.mp4'
                self.ffmpeg('-f', 'lavfi', '-i', f'testsrc2=size={width}x{height}:rate=5',
                            '-t', '2', '-c:v', 'libx264', '-threads', '1', video)
                if rotate:
                    rotated = self.root / 'rotated.mp4'
                    rotate_90(video, rotated)
                    video, height = rotated, width
                made = sigmake.make(video, self.con, self.root, fps=5, crop_bars=True,
                    crop_mode='fixed5', detector=sigmake.FIXED_CROP_ID, ffmpeg='ffmpeg',
                    ffprobe='ffprobe', ffmpeg_version=sigstore.ffmpeg_version('ffmpeg'))
                expected = f'crop=iw:{height - 2 * edge}:0:{edge}:exact=1'
                self.assertEqual(made.crop, expected)
                sigmake.produce(video, self.root, 'expected.sig', expected, 5, 'ffmpeg')
                self.assertEqual(made.path.read_bytes(), (self.root / 'expected.sig').read_bytes())
                self.assertEqual(made.frames, 10)

    def test_lossless_crop_cross_view_recovers_static_picture(self):
        # Fixed seed and block texture: the crop shifts meaningful picture,
        # not bars. FFV1 preserves identical retained pixels across inputs.
        rng = random.Random(7)
        pgm = self.root / 'blocks.pgm'
        pgm.write_bytes(b'P5\n32 20\n255\n' + bytes(rng.randrange(256) for _ in range(640)))
        original, cropped = self.root / 'original.mkv', self.root / 'cropped.mkv'
        self.ffmpeg('-loop', '1', '-framerate', '5', '-i', pgm, '-t', '30',
                    '-vf', 'scale=320:200:flags=neighbor,format=yuv420p', '-c:v', 'ffv1', original)
        self.ffmpeg('-i', original, '-vf', 'crop=320:180:0:10:exact=1', '-c:v', 'ffv1', cropped)
        cmd = [sys.executable, str(ROOT / 'tools/find_reuse.py'), '--source', str(original),
               '--candidates', str(cropped), '--sig-dir', str(self.root / 'sig'),
               '--no-crop-bars', '--crop-fallback', '--jobs', '1',
               '--mpeg7dupes', os.environ['MPEG7DUPES'], '--json', str(self.root / 'record.json')]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        self.assertEqual(done.returncode, 0, done.stderr)
        record = json.loads((self.root / 'record.json').read_text())
        self.assertTrue(record['fallback']['pairs'], done.stdout)
        self.assertEqual(record['candidates'][0]['status'], 'needs_review')
        hit = record['matches'][0]
        self.assertEqual(hit['coverage_percent'], 100)
        self.assertEqual((hit['source_view'], hit['candidate_view']), ('crop5', 'full'))
        self.assertEqual(hit['source_signature']['sha256'], hit['candidate_signature']['sha256'])
        self.assertEqual(hit['source_begin_seconds'], hit['start_seconds'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
