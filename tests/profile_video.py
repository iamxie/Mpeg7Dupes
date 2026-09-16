"""Real-decoder development fixtures; sampled properties do not change matches."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import video_profile as vp
import render_report


class Profiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        filters = {
            'moving': 'testsrc2=size=160x90:rate=10',
            'still': 'testsrc2=size=160x90:rate=10,trim=end_frame=1,loop=-1:1:0,setpts=N/(10*TB)',
            'darkmoving': "nullsrc=size=160x90:rate=10,geq=lum='16+30*mod(floor(N/5),2)':cb=128:cr=128",
            'darkstill': 'color=0x101010:size=160x90:rate=10',
            'limited': 'nullsrc=size=160x90:rate=10,format=yuv444p,geq=lum=56:cb=128:cr=128,setparams=range=limited',
            'full': 'nullsrc=size=160x90:rate=10,format=yuv444p,geq=lum=47:cb=128:cr=128,setparams=range=full',
            'hdr': 'testsrc2=size=160x90:rate=10,format=yuv420p10le,setparams=color_trc=smpte2084',
            'sdr10': 'color=0x101010:size=160x90:rate=10,format=yuv420p10le,setparams=color_trc=bt709',
            'mixed': "nullsrc=size=160x90:rate=10,geq=lum='if(lt(T,5),20,130+60*mod(floor(N/5),2))':cb=128:cr=128",
        }
        for name, chain in filters.items():
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', chain,
                '-t', '10', '-c:v', 'ffv1', '-threads', '1',
                *(['-color_range', 'pc' if name == 'full' else 'tv'] if name in ('full', 'limited') else []),
                *(['-color_trc', 'smpte2084'] if name == 'hdr' else []),
                str(cls.root / (name + '.mkv'))],
                check=True, capture_output=True)
        cls.profiles = {name: vp.build(cls.root / (name + '.mkv'), cls.root, 'ffmpeg', 'ffprobe') for name in filters}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_four_conditions_and_colour_normalisation(self):
        for name, category in [('moving', 'neither'), ('still', 'low_motion'),
                               ('darkmoving', 'dark'), ('darkstill', 'both'), ('sdr10', 'both')]:
            with self.subTest(name=name):
                self.assertEqual(self.profiles[name]['category'], category, self.profiles[name])
        luma = lambda name: self.profiles[name]['regions']['center']['summary']['mean_yavg']
        self.assertFalse(self.profiles['full']['color']['range_assumed'])
        self.assertFalse(self.profiles['limited']['color']['range_assumed'])
        self.assertAlmostEqual(luma('limited'), luma('full'), delta=1)
        self.assertEqual(self.profiles['hdr']['status'], 'unsupported')

    def test_matched_region_changes_explanation(self):
        p = self.profiles['mixed']
        h = dict(fps=5, overrun=False, source_begin_seconds=6, source_end_seconds=9.8,
                 start_seconds=6, end_seconds=9.8)
        self.assertFalse(vp.assess(h, p, p)['review_recommended'])
        h.update(source_begin_seconds=1, source_end_seconds=3.8, start_seconds=1, end_seconds=3.8)
        self.assertTrue(vp.assess(h, p, p)['review_recommended'])

    def test_scanner_analysis_only_adds_annotations(self):
        records = []
        for option in ('--no-analysis', '--analyze'):
            output = self.root / 'record.json'
            cmd = [sys.executable, str(ROOT / 'tools/find_reuse.py'), '--source', str(self.root / 'still.mkv'),
                   '--candidates', str(self.root / 'still.mkv'), '--sig-dir', str(self.root / 'sig'),
                   '--no-crop-bars', option, '--mpeg7dupes', os.environ['MPEG7DUPES'], '--jobs', '1', '--json', str(output)]
            # Different path, identical content: both files must be assessed and matched.
            copy = self.root / 'copy.mkv'
            copy.write_bytes((self.root / 'still.mkv').read_bytes())
            cmd[cmd.index('--candidates') + 1] = str(copy)
            done = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            records.append(json.loads(output.read_text()))
        before, after = records
        self.assertTrue(after['matches'])
        for record in records:
            self.assertEqual(record['candidates'][0]['status'], 'matched')
        self.assertEqual([{k: v for k, v in h.items() if k != 'assessment'} for h in before['matches']],
                         [{k: v for k, v in h.items() if k != 'assessment'} for h in after['matches']])
        self.assertTrue(after['matches'][0]['assessment']['position_notes'])
        page = render_report.render(after, [])
        self.assertIn('Matched-span analysis: available', page)
        self.assertIn('Sampled central intervals', page)


if __name__ == '__main__':
    unittest.main(verbosity=2)
