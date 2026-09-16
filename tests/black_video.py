"""Generated development footage for the optional colour detector; not an independent dataset."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import detect_bars
import render_report


class BlackVideo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        still = 'testsrc2=size=160x120:rate=5,trim=end_frame=1,loop=-1:1:0,setpts=N/(5*TB)'
        filters = {
            'plain': still,
            'barred': still + ',pad=160:160:0:20:0x080808',
            'dark': 'color=0x202020:size=160x160:rate=5',
            'black': 'color=black:size=160x160:rate=5',
            'fade': still + ',fade=t=in:st=0:d=4,fade=t=out:st=8:d=4,pad=160:160:0:20:black',
            'lettered': still + ',pad=160:160:0:20:black,drawbox=x=20:y=6:w=80:h=6:color=white:t=fill',
            'mixed': 'testsrc2=size=160x160:rate=5,drawbox=x=0:y=0:w=iw:h=20:color=black:t=fill:enable=lt(t\\,5),drawbox=x=0:y=140:w=iw:h=20:color=black:t=fill:enable=lt(t\\,5)',
        }
        for name, chain in filters.items():
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', chain,
                '-t', '12', '-c:v', 'libx264', '-threads', '1', '-crf', '23',
                str(cls.root / (name + '.mp4'))], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def analyse(self, name, mode):
        return detect_bars.analyse(self.root / (name + '.mp4'), detect_bars.POINTS,
            detect_bars.PER_POINT, detect_bars.THRESHOLD, detect_bars.MIN_FRACTION, mode=mode)

    def test_generated_scenarios(self):
        motion = self.analyse('barred', 'motion')
        self.assertEqual(motion['status'], 'too static')
        for name in ['barred', 'fade']:
            with self.subTest(name=name):
                result = self.analyse(name, 'black')
                self.assertEqual(result['status'], 'ok', result)
                self.assertLessEqual(abs(result['top'] - 20), 2, result)
                self.assertLessEqual(abs(result['bottom'] - 20), 2, result)
        for name in ['plain', 'dark', 'mixed']:
            with self.subTest(name=name):
                result = self.analyse(name, 'black')
                self.assertEqual(result['status'], 'ok', result)
                self.assertEqual(result['bar_fraction'], 0, result)
        self.assertEqual(self.analyse('black', 'black')['status'], 'too dark')
        lettered = self.analyse('lettered', 'black')
        self.assertEqual(lettered['status'], 'ok', lettered)
        self.assertLessEqual(lettered['top'], 6, lettered)  # Keep the lettering.

    def test_scanner_cold_hot_and_report_use_the_selected_mode(self):
        out = self.root / 'reuse.json'
        command = [sys.executable, str(ROOT / 'tools/find_reuse.py'),
            '--source', str(self.root / 'plain.mp4'), '--candidates', str(self.root / 'barred.mp4'),
            '--sig-dir', str(self.root / 'sig'), '--crop-mode', 'black',
            '--mpeg7dupes', os.environ['MPEG7DUPES'], '--jobs', '1', '--json', str(out)]
        records = []
        for _ in range(2):
            run = subprocess.run(command, capture_output=True, text=True, timeout=90)
            self.assertEqual(run.returncode, 0, run.stderr)
            record = json.loads(out.read_text())
            self.assertEqual(record['schema'], 'find_reuse/7')
            self.assertEqual(record['settings']['crop_mode'], 'black')
            self.assertEqual(record['tool']['detector']['version'], 'black-1')
            self.assertTrue(record['matches'], run.stdout)
            for video in record['sources'] + record['candidates']:
                self.assertEqual(video['crop_mode'], 'black')
                self.assertEqual(video['signature']['detector'], 'black-1')
                self.assertIn('black_crop', [w['code'] for w in video['warnings']])
            self.assertIn('black mode', render_report.render(record, []))
            records.append(record)
        self.assertEqual(records[0]['sources'], records[1]['sources'])
        self.assertEqual(records[0]['candidates'], records[1]['candidates'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
