"""Regressions for cache identity, publishing and failed bar sampling."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sigmake
import sigstore
import detect_bars
import test_sigmake


class Cache(test_sigmake.Base):
    def test_nearby_fps_have_distinct_names(self):
        self.assertNotEqual(sigstore.fps_tag(5.000001), sigstore.fps_tag(5.000002))
        self.assertEqual(sigstore.fps_tag(5), sigstore.fps_tag(5.0))

    def test_nonfinite_duration_is_not_a_video(self):
        for value in ('nan', 'inf', '-1', '0'):
            with self.subTest(value=value):
                os.environ['FAKE_DURATION'] = value
                with self.assertRaises(sigmake.SignatureError):
                    sigmake.probe_duration(self.ffprobe, self.a_video())

    def test_relative_executable_is_resolved_before_chdir(self):
        old = Path.cwd()
        try:
            os.chdir(self.dir)
            found = sigmake.resolve_tool('./ffmpeg')
            self.assertTrue(Path(found).is_absolute())
        finally:
            os.chdir(old)

    def test_source_changed_during_decode_publishes_nothing(self):
        video = self.a_video()
        original = sigmake.probe_duration
        def change(*args):
            video.write_bytes(b'changed after identifying the original content')
            return original(*args)
        with patch.object(sigmake, 'probe_duration', side_effect=change):
            with self.assertRaises(sigstore.FileChanged):
                self.make(video)
        self.assertEqual(self.con.execute('select count(*) from sigs').fetchone()[0], 0)
        self.assertEqual(list(self.sig_dir.glob('*.sig')), [])

    def test_decode_holds_no_database_write_transaction(self):
        original = sigmake.probe_duration
        def check(*args):
            other = sigstore.open_db(self.dir / 'index.sqlite')
            try:
                other.execute('pragma busy_timeout=100')
                other.execute("insert into meta values ('other-producer','works')")
                other.commit()
            finally:
                other.close()
            return original(*args)
        with patch.object(sigmake, 'probe_duration', side_effect=check):
            self.make(self.a_video())

    def test_failed_index_publish_does_not_replace_old_signature(self):
        video = self.a_video()
        old = self.make(video)
        self.con.commit()
        old_bytes = old.path.read_bytes()
        self.template.write_bytes(test_sigmake.fake_signature(frames=75, segments=2))
        with patch.object(sigmake, 'record', side_effect=OSError('index write failed')):
            with self.assertRaises(OSError):
                self.make(video, overwrite=True)
        self.assertEqual(old.path.read_bytes(), old_bytes)
        again = self.make(video)
        self.assertEqual(again.frames, old.frames)

    def test_sampling_error_is_a_failed_file(self):
        os.environ['FAKE_FFMPEG'] = 'fail'
        decision = sigmake.decide_crop(self.a_video(), True, self.ffmpeg, self.ffprobe)
        self.assertEqual(decision.state, 'failed')
        self.assertIn('ffmpeg', decision.detail)

    def test_truncated_raw_frame_is_failed(self):
        from types import SimpleNamespace
        with patch.object(detect_bars.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=b'x' * 17, stderr=b'')):
            with self.assertRaises(RuntimeError):
                detect_bars.movement('x', 2, 10, 1, 12)

    def test_legacy_collisions_invalidate_both_rows_permanently(self):
        video = self.a_video()
        digest = sigstore.hash_file(video)
        name = sigstore.sig_filename(digest, 5.0, False, '')
        (self.sig_dir / name).write_bytes(self.template.read_bytes())
        for fps in (5.0, 5.000001):
            sigstore.remember_signature(self.con, digest, fps, False, '', '', 63,
                                        'fake', name, 'disabled')
        self.con.close()
        self.con = sigstore.open_db(self.dir / 'index.sqlite')
        first = self.make(video, fps=5.000001)
        self.assertTrue(first.produced)
        second = self.make(video, fps=5.0)
        self.assertTrue(second.produced)

    def test_invalid_header_flags_are_not_cache_hits(self):
        from test_loader import short_signature, field
        for offset, count, value in ((0, 32, 2), (32, 1, 0), (177, 1, 0), (1618, 1, 1)):
            with self.subTest(offset=offset):
                data = short_signature(2)
                field(data, offset, count, value)
                self.template.write_bytes(data)
                self.assertIsNone(sigstore.read_header(self.template))

    def test_rotated_probe_uses_display_height(self):
        from types import SimpleNamespace
        import json
        metadata = {'streams': [{'width': 320, 'height': 180,
                                 'side_data_list': [{'rotation': 90}]}],
                    'format': {'duration': '12.5'}}
        with patch.object(detect_bars.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(metadata))):
            self.assertEqual(detect_bars.probe('x'), (320, 12.5))

    def test_sampler_selects_the_same_stream_as_signature(self):
        from types import SimpleNamespace
        with patch.object(detect_bars.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=b'', stderr=b'')) as run:
            detect_bars.movement('x', 180, 10, 1, 12)
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[cmd.index('-map') + 1], '0:v:0')


if __name__ == '__main__':
    unittest.main(verbosity=2)
