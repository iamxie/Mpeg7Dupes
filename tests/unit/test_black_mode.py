"""The opt-in detector must not change motion caches or hide sampling failures."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import detect_bars
import find_reuse
import render_report
import sigmake
import sigstore
from fakesig import fake_signature, signature_env, write_fake_tools


class BlackMode(unittest.TestCase):
    def test_modes_have_separate_keys_and_motion_keeps_its_existing_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / 'still.mp4'
            video.write_bytes(b'still with bars')
            template = root / 'template.sig'
            template.write_bytes(fake_signature(frames=63, segments=2))
            ffmpeg, ffprobe = write_fake_tools(root)
            env = signature_env(template, root / 'calls.log')
            con = sigstore.open_db(root / 'index.sqlite')
            try:
                with patch.dict(os.environ, env), patch.object(detect_bars, 'analyse',
                        return_value={'status': 'ok', 'bar_fraction': .25, 'height': 160,
                                      'top': 20, 'bottom': 20, 'picture_height': 120}):
                    made = {}
                    for mode, key in [('motion', '3'), ('black', 'black-1')]:
                        self.assertEqual(detect_bars.detector_id(mode), key)
                        made[mode] = sigmake.make(video, con, root, fps=5, crop_bars=True,
                            crop_mode=mode, detector=key, ffmpeg=ffmpeg, ffprobe=ffprobe,
                            ffmpeg_version='original generator')
                    self.assertNotEqual(made['motion'].filename, made['black'].filename)
                    with patch.object(sigmake, 'build', side_effect=AssertionError('cache miss')):
                        for mode in made:
                            hit = sigmake.make(video, con, root, fps=5, crop_bars=True,
                                crop_mode=mode, detector=detect_bars.detector_id(mode),
                                ffmpeg=ffmpeg, ffprobe=ffprobe, ffmpeg_version='new generator')
                            self.assertEqual(hit.filename, made[mode].filename)
                            self.assertEqual(hit.ffmpeg, 'original generator')
                    with self.assertRaisesRegex(ValueError, 'detector'):
                        sigmake.make(video, con, root, fps=5, crop_bars=True,
                            crop_mode='black', detector='3', ffmpeg=ffmpeg,
                            ffprobe=ffprobe, ffmpeg_version='generator')
            finally:
                con.close()

    def test_black_sampling_errors_and_missing_metadata_are_failures(self):
        for result in [subprocess.CompletedProcess([], 7, b'', b'decoder failed'),
                       subprocess.CompletedProcess([], 0, b'', b'')]:
            with self.subTest(result=result), patch.object(detect_bars, 'probe', return_value=(160, 12)), \
                    patch.object(detect_bars, 'run_tool', return_value=result):
                result = detect_bars.analyse(Path('sample.mp4'), 6, 12, 5, .02, mode='black')
                self.assertEqual(result['status'], 'failed')

    def test_mode_is_validated_and_disabled_stays_disabled(self):
        args = find_reuse.build_parser().parse_args([
            '--source', 'a', '--candidates', 'b', '--crop-mode', 'black', '--no-crop-bars'])
        settings = find_reuse.load_settings(args)
        self.assertEqual(settings['crop_mode'], 'black')
        self.assertFalse(settings['crop_bars'])
        with patch.object(detect_bars, 'analyse', side_effect=AssertionError('disabled')):
            self.assertEqual(sigmake.decide_crop(Path('a'), False, 'ffmpeg', 'ffprobe',
                                                crop_mode='black').state, 'disabled')
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'bad.toml'
            config.write_text('crop_mode = "automatic"\n')
            args.config = str(config)
            args.crop_mode = None
            with self.assertRaisesRegex(ValueError, 'crop_mode'):
                find_reuse.load_settings(args)


if __name__ == '__main__':
    unittest.main()
