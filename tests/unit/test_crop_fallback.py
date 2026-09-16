"""Cross-view recovery is reviewable, cached separately, and fails closed."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import find_reuse
import render_report
import sigmake
import test_find_reuse as scan_fixture
import test_sigmake as signature_fixture


class Cache(signature_fixture.Base):
    def test_unreadable_fixed_geometry_is_a_controlled_failure(self):
        with patch.object(sigmake.detect_bars, 'probe', return_value=None):
            with self.assertRaisesRegex(sigmake.SignatureError, 'display height'):
                self.make(self.a_video(), crop_bars=True, crop_mode='fixed5', detector='fixed5-1')

    def test_fixed_view_does_not_replace_full_or_detected_view(self):
        video = self.a_video()
        full = self.make(video)
        fixed = self.make(video, crop_bars=True, crop_mode='fixed5', detector='fixed5-1')
        self.assertNotEqual(full.filename, fixed.filename)
        self.assertEqual(fixed.crop_state, 'fixed')
        self.assertEqual(fixed.crop, 'crop=iw:160:0:10:exact=1')
        with patch.object(sigmake, 'decide_crop', return_value=sigmake.CropDecision('none')):
            motion = self.make(video, crop_bars=True, detector='3')
        self.assertEqual(len({full.filename, fixed.filename, motion.filename}), 3)
        with patch.object(sigmake, 'build', side_effect=AssertionError('cache miss')):
            self.assertEqual(self.make(video).filename, full.filename)
            self.assertEqual(self.make(video, crop_bars=True, detector='3').filename, motion.filename)
            self.assertEqual(self.make(video, crop_bars=True, crop_mode='fixed5',
                                       detector='fixed5-1').filename, fixed.filename)
        self.assertEqual(len(self.calls('ffmpeg')), 3)
        self.assertIn('-vf crop=iw:160:0:10:exact=1,fps=5,signature=', self.calls('ffmpeg')[1])

    def test_failed_fixed_overwrite_keeps_both_views(self):
        video = self.a_video()
        full = self.make(video)
        fixed = self.make(video, crop_bars=True, crop_mode='fixed5', detector='fixed5-1')
        with patch.dict(os.environ, {'FAKE_FFMPEG': 'fail'}):
            with self.assertRaises(sigmake.SignatureError):
                self.make(video, crop_bars=True, crop_mode='fixed5', detector='fixed5-1', overwrite=True)
        self.assertTrue(full.path.exists())
        self.assertEqual(self.make(video, crop_bars=True, crop_mode='fixed5',
                                   detector='fixed5-1').filename, fixed.filename)


class Scan(unittest.TestCase):
    setUp = scan_fixture.Run.setUp
    tearDown = scan_fixture.Run.tearDown
    run_script = scan_fixture.Run.run_script
    record = scan_fixture.Run.record
    statuses = scan_fixture.Run.statuses

    def comparator(self):
        # A full-frame hit, a below-threshold pair, and both directional
        # fallback results. Log actual pairs rather than private list names.
        Path(self.m7d).write_text('''#!/usr/bin/env python3
import csv, json, os, sys
from pathlib import Path
a = sys.argv
if '--version' in a:
    print('fake comparison'); sys.exit(0)
s = Path(a[a.index('-n') + 1]).read_text().strip()
cs = Path(a[a.index('-l') + 1]).read_text().splitlines()
w = csv.writer(sys.stdout)
w.writerow(['First signature','Second signature','matchframes','framerateratio','whole',
            'time 1 [s]','time 2 [s]','begin 1 [s]','end 1 [s]','begin 2 [s]','end 2 [s]'])
for c in cs:
    sc, cc = 'fixed5-1' in s, 'fixed5-1' in c
    with open(os.environ['FAKE_LOG'] + '.pairs', 'a') as f:
        f.write(json.dumps([s,c]) + '\\n')
    if os.environ.get('FAIL_CROSS') and cc: sys.exit(7)
    hit = sc or cc or c.startswith(os.environ.get('FULL_HIT_HASH', 'NEVER'))
    if os.environ.get('ALL_HIT'): hit = True
    if os.environ.get('ALL_MISS'): hit = False
    if os.environ.get('OMIT_MISS') and not hit: continue
    if os.environ.get('REVERSE_ONLY') and sc: hit = False
    w.writerow([s,c,60 if hit else 10,1,1,0,0,0,11.8,0,11.8])
''')

    def test_both_cross_directions_review_and_full_hit_is_not_retried(self):
        import sigstore
        self.comparator()
        full_hash = sigstore.hash_file(self.comp / 'one.mp4')
        done = self.run_script('--crop-fallback', FULL_HIT_HASH=full_hash)
        self.assertEqual(done.returncode, 0, done.stderr)
        rec = self.record()
        self.assertEqual(self.statuses(rec), {'one.mp4': 'matched', 'two.mp4': 'needs_review'})
        self.assertEqual(rec['summary']['review_matches'], 1)
        hit = next(h for h in rec['matches'] if h['candidate'] == 'two.mp4')
        self.assertTrue(hit['requires_review'])
        self.assertEqual((hit['source_view'], hit['candidate_view']), ('crop5', 'full'))
        self.assertEqual(len(hit['view_evidence']), 2)
        self.assertEqual(hit['source_crop'], 'crop=iw:160:0:10:exact=1')
        pairs = [json.loads(line) for line in Path(str(self.log) + '.pairs').read_text().splitlines()]
        self.assertEqual(len(pairs), 4)
        self.assertEqual(sum(c.startswith(full_hash) for s, c in pairs), 1)
        self.assertEqual(rec['misses'], [])
        page = render_report.render(rec, [])
        self.assertIn('Needs review', page)
        self.assertIn('View on yours: Fixed 5% top/bottom view', page)
        self.assertIn(hit['source_crop'], page)
        self.assertIn('Needs review', done.stdout)
        before = self.log.read_text().count('signature=filename=')
        self.assertEqual(self.run_script('--crop-fallback', FULL_HIT_HASH=full_hash).returncode, 0)
        self.assertEqual(self.log.read_text().count('signature=filename='), before)

    def test_empty_csv_pairs_are_retried_and_reverse_direction_can_recover(self):
        self.comparator()
        done = self.run_script('--crop-fallback', OMIT_MISS='1', REVERSE_ONLY='1')
        self.assertEqual(done.returncode, 0, done.stderr)
        rec = self.record()
        self.assertEqual(len(rec['matches']), 2)
        self.assertTrue(all(h['candidate_view'] == 'crop5' for h in rec['matches']))
        self.assertEqual(set(self.statuses(rec).values()), {'needs_review'})

    def test_failed_second_branch_preserves_full_hits_and_is_not_a_miss(self):
        import sigstore
        self.comparator()
        done = self.run_script('--crop-fallback', FAIL_CROSS='1',
                               FULL_HIT_HASH=sigstore.hash_file(self.comp / 'one.mp4'))
        self.assertEqual(done.returncode, 2, done.stderr)
        rec = self.record()
        self.assertFalse(rec['summary']['complete'])
        self.assertEqual(rec['misses'], [])
        self.assertEqual(rec['error']['stage'], 'crop_fallback')
        self.assertEqual(self.statuses(rec)['one.mp4'], 'matched')
        self.assertFalse(rec['fallback']['complete'])
        self.assertEqual(rec['comparison']['sources_completed'], [])

    def test_fallback_requires_explicit_full_frame_baseline(self):
        args = find_reuse.build_parser().parse_args(['--source','a','--candidates','b','--crop-fallback'])
        with self.assertRaisesRegex(ValueError, 'no-crop-bars'):
            find_reuse.load_settings(args)

    def test_full_hits_do_not_generate_or_compare_fixed_views(self):
        self.comparator()
        done = self.run_script('--crop-fallback', ALL_HIT='1')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.record()['fallback']['pairs'], [])
        self.assertNotIn('fixed5-1', self.log.read_text())

    def test_completed_cross_misses_remain_checked(self):
        self.comparator()
        done = self.run_script('--crop-fallback', ALL_MISS='1', OMIT_MISS='1')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(set(self.statuses(self.record()).values()), {'checked'})
        self.assertEqual(len(self.record()['misses']), 2)

    def test_fixed_decode_failure_keeps_full_hit_and_cannot_be_a_miss(self):
        import sigstore
        self.comparator()
        ffmpeg = Path(self.ffmpeg)
        ffmpeg.write_text(ffmpeg.read_text().replace('name=""',
            'case "$*" in *crop-vfixed5-1*) echo fixed-decode-failed >&2; exit 1;; esac\nname=""'))
        done = self.run_script('--crop-fallback',
                               FULL_HIT_HASH=sigstore.hash_file(self.comp / 'one.mp4'))
        self.assertEqual(done.returncode, 2, done.stderr)
        rec = self.record()
        self.assertEqual(rec['misses'], [])
        self.assertEqual(self.statuses(rec), {'one.mp4': 'matched', 'two.mp4': 'not_compared'})
        self.assertIn('fixed-decode-failed', rec['error']['reason'])


if __name__ == '__main__':
    unittest.main()
