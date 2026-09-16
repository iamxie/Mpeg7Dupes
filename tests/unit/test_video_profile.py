"""Video properties and matched-span explanations must remain separate from decisions."""
import json
import tempfile
import concurrent.futures
import threading
import time
from types import SimpleNamespace
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import video_profile as vp
import sigstore
import test_find_reuse as fixture
import render_report


def samples(seconds=10, dark=False, still=False):
    return [{'time': i / 2, 'yavg': 15 if dark else 140,
             'ylow': 5 if dark else 60, 'yhigh': 25 if dark else 220,
             'ydif': 0 if still else 20, 'dark_pixels_percent': 95 if dark else 5}
            for i in range(seconds * 2)]


def profile(rows, full=None):
    return vp.compile_profile(full or rows, rows, len(rows) / 2,
                              {'range_assumed': False, 'transfer_assumed': False})


class Measures(unittest.TestCase):
    def test_dark_and_motion_are_independent_and_unknown_first_sample_is_not_static(self):
        for dark, still, category in [(False, False, 'neither'), (True, False, 'dark'),
                                     (False, True, 'low_motion'), (True, True, 'both')]:
            with self.subTest(dark=dark, still=still):
                p = profile(samples(dark=dark, still=still))
                self.assertEqual(p['category'], category)
                self.assertEqual(p['regions']['center']['summary']['dark_ratio'], float(dark))
        short = profile(samples(seconds=1, still=True))
        self.assertEqual(short['regions']['center']['summary']['low_motion_ratio'], 0)
        one = profile(samples(seconds=1, still=True)[:1])
        self.assertIsNone(one['regions']['center']['summary']['low_motion_ratio'])

    def test_whole_video_does_not_override_the_matched_span(self):
        rows = samples(seconds=10, dark=True, still=True)
        rows += [dict(r, time=r['time'] + 10) for r in samples(seconds=10)]
        p = profile(rows)
        hit = dict(fps=5, source_begin_seconds=12, source_end_seconds=17.8,
                   start_seconds=12, end_seconds=17.8, overrun=False, requires_review=False)
        result = vp.assess(hit, p, p)
        self.assertEqual(result['source']['dark_ratio'], 0)
        self.assertFalse(result['review_recommended'])
        self.assertEqual(result['source']['end'], 18)
        hit.update(source_begin_seconds=2, source_end_seconds=7.8, start_seconds=2, end_seconds=7.8)
        result = vp.assess(hit, p, p)
        self.assertTrue(result['review_recommended'])
        self.assertTrue(result['content_notes'])
        self.assertTrue(result['position_notes'])

    def test_borders_are_kept_as_context_not_assumed_picture_darkness(self):
        p = profile(samples(), full=samples(dark=True))
        self.assertEqual(p['category'], 'neither')
        self.assertTrue(p['edge_difference'])

    def test_overrun_and_unavailable_profiles_do_not_invent_normality(self):
        hit = dict(fps=5, source_begin_seconds=0, source_end_seconds=99,
                   start_seconds=0, end_seconds=99, overrun=True)
        p = profile(samples(still=True))
        self.assertEqual(vp.assess(hit, p, p)['status'], 'unavailable')
        hit['overrun'] = False
        result = vp.assess(hit, {'status': 'failed', 'reason': 'decoder failed'}, p)
        self.assertNotEqual(result['status'], 'available')
        self.assertIn('decoder failed', ' '.join(result['content_notes']))

    def test_insufficient_motion_observations_are_not_normal(self):
        p = profile(samples(seconds=1)[:1])
        h = dict(fps=5, source_begin_seconds=0, source_end_seconds=.2,
                 start_seconds=0, end_seconds=.2, overrun=False)
        result = vp.assess(h, p, p)
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['position_notes'])

    def test_metadata_rejects_missing_nonfinite_and_discontinuous_samples(self):
        row = 'frame:0 pts:0 pts_time:0\nlavfi.signalstats.YAVG=20\n'
        with self.assertRaises(vp.ProfileError):
            vp.parse_metadata(row)
        valid = ''.join('frame:%d pts:%d pts_time:%s\n' % (i, i, i / 2) +
            ''.join('lavfi.signalstats.%s=%s\n' % (k,v) for k,v in
                    [('YAVG',20),('YLOW',10),('YHIGH',30),('YDIF',0)]) +
            'lavfi.blackframe.pblack=90\n' for i in range(2))
        self.assertEqual(len(vp.parse_metadata(valid)), 2)
        for broken in (valid.replace('YAVG=20', 'YAVG=nan'),
                       valid.replace('pts_time:0.5', 'pts_time:2')):
            with self.assertRaises(vp.ProfileError): vp.parse_metadata(broken)


class Cache(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.src = self.root / 'video.mp4'
        self.src.write_bytes(b'original video')
        self.con = sigstore.open_db(self.root / 'index.sqlite')
        self.digest, stat = sigstore.identify(self.src)
        sigstore.remember_file(self.con, self.src, self.digest, stat)
        self.con.commit()
        self.kw = dict(content_hash=self.digest, ffmpeg='ffmpeg', ffprobe='ffprobe', ffmpeg_version='test')

    def tearDown(self):
        self.con.close()
        self.temp.cleanup()

    def make(self, **kw):
        return vp.make(self.src, self.con, self.root, **self.kw, **kw)

    def test_hot_and_moved_cache_reuses_metrics_and_corruption_rebuilds(self):
        with patch.object(vp, 'build', return_value=profile(samples())) as build:
            original = self.make()
            self.assertEqual(self.make(), original)
            self.src = self.src.rename(self.root / 'renamed.mp4')
            self.assertEqual(self.make(), original)
            self.assertEqual(build.call_count, 1)
            (self.root / original['cache']['filename']).write_text('{}')
            rebuilt = self.make()
            self.assertEqual(build.call_count, 2)
            self.assertNotEqual(original['cache']['filename'], rebuilt['cache']['filename'])

    def test_failed_overwrite_and_source_change_keep_previous_generation(self):
        with patch.object(vp, 'build', return_value=profile(samples())):
            original = self.make()
        with patch.object(vp, 'build', side_effect=vp.ProfileError('decode failed')):
            with self.assertRaises(vp.ProfileError): self.make(overwrite=True)
        self.assertEqual(self.make(), original)
        def changed(*args):
            self.src.write_bytes(b'changed video content')
            return profile(samples())
        with patch.object(vp, 'build', side_effect=changed):
            with self.assertRaises(sigstore.FileChanged): self.make(overwrite=True)
        self.assertEqual(self.con.execute('SELECT filename FROM profiles').fetchone()[0], original['cache']['filename'])
        self.assertEqual(len(list(self.root.glob('*.gen-*.json'))), 1)

    def test_recipe_invalidation_is_independent_of_signature_cache(self):
        with patch.object(vp, 'build', return_value=profile(samples())) as build:
            original = self.make()
            with patch.object(vp, 'RECIPE_ID', 'new recipe'):
                other = self.make()
            self.assertNotEqual(original['cache'], other['cache'])
            self.assertEqual(build.call_count, 2)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM sigs').fetchone()[0], 0)

    def test_concurrent_readers_publish_one_generation(self):
        barrier = threading.Barrier(3)
        def slow_build(*args):
            time.sleep(.05)
            return profile(samples())
        def worker(_):
            con = sigstore.open_db(self.root / 'index.sqlite')
            try:
                barrier.wait(timeout=5)
                return vp.make(self.src, con, self.root, **self.kw)['cache']
            finally:
                con.close()
        with patch.object(vp, 'build', side_effect=slow_build) as build:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                caches = list(pool.map(worker, range(3)))
            self.assertEqual(build.call_count, 1)
            self.assertEqual(caches, [caches[0]] * 3)

    def test_explicit_limited_range_takes_precedence_over_pixel_format(self):
        output = SimpleNamespace(returncode=0, stdout=json.dumps({'streams': [
            {'pix_fmt': 'gray', 'color_range': 'tv', 'color_transfer': 'bt709'}]}))
        with patch.object(vp, 'run_tool', return_value=output):
            self.assertEqual(vp.probe(self.src, 'ffprobe')['effective_range'], 'tv')


class Scan(unittest.TestCase):
    setUp = fixture.Run.setUp
    tearDown = fixture.Run.tearDown
    run_script = fixture.Run.run_script
    record = fixture.Run.record

    def test_analysis_failure_preserves_comparison_and_is_reported_separately(self):
        # Existing fake decoder writes signatures but no analysis metadata.
        done = self.run_script('--analyze')
        self.assertEqual(done.returncode, 1, done.stderr)
        rec = self.record()
        self.assertTrue(rec['comparison']['complete'])
        self.assertFalse(rec['analysis']['complete'])
        self.assertTrue(rec['matches'])
        self.assertEqual(rec['matches'][0]['assessment']['status'], 'unavailable')
        page = render_report.render(rec, [])
        self.assertIn('Analysis incomplete', page)
        self.assertNotIn('0 not processed', page)
        rec['matches'] = []
        self.assertIn('No candidate reached', render_report.render(rec, []))

    def test_disabling_analysis_remains_explicit_in_record(self):
        done = self.run_script('--no-analysis')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(self.record()['settings']['analyze'])
        self.assertEqual(self.record()['matches'][0]['assessment']['status'], 'disabled')


if __name__ == '__main__':
    unittest.main()
