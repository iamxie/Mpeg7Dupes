"""A real short excerpt must be found inside a long original in either role."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CoverageVideo(unittest.TestCase):
    def test_excerpt_matches_in_both_roles_and_source_mode_keeps_legacy_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original, excerpt = work / 'original.mp4', work / 'excerpt.mp4'
            def ffmpeg(*args):
                subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', *map(str, args)],
                               check=True, capture_output=True, timeout=60)
            ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=10', '-t', '60',
                   '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', original)
            ffmpeg('-i', original, '-ss', '20', '-t', '12',
                   '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', excerpt)
            def scan(source, candidate, *extra):
                result = subprocess.run([
                    sys.executable, str(ROOT / 'tools/find_reuse.py'),
                    '--source', str(source), '--candidates', str(candidate),
                    '--sig-dir', str(work / 'sig'), '--json', str(work / 'report.json'),
                    '--no-crop-bars', '--no-analysis', '--jobs', '1',
                    '--mpeg7dupes', os.environ['MPEG7DUPES'], *extra],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads((work / 'report.json').read_text())
            new = scan(original, excerpt)
            self.assertEqual(len(new['matches']), 1)
            hit = new['matches'][0]
            self.assertLess(hit['source_coverage_percent'], 40)
            self.assertGreater(hit['candidate_coverage_percent'], 80)
            self.assertEqual(hit['coverage_percent'], hit['candidate_coverage_percent'])
            self.assertAlmostEqual(hit['source_begin_seconds'], 20, delta=1)
            self.assertAlmostEqual(hit['start_seconds'], 0, delta=1)
            sigs = {p.name: p.stat().st_mtime_ns for p in (work / 'sig').glob('*.sig')}
            legacy = scan(original, excerpt, '--min-source-coverage', '40')
            self.assertEqual(legacy['matches'], [])
            self.assertEqual(legacy['candidates'][0]['best_coverage_percent'],
                             hit['source_coverage_percent'])
            reverse = scan(excerpt, original)
            self.assertEqual(len(reverse['matches']), 1)
            self.assertGreater(reverse['matches'][0]['source_coverage_percent'], 80)
            self.assertEqual(sigs, {p.name: p.stat().st_mtime_ns
                                    for p in (work / 'sig').glob('*.sig')})


if __name__ == '__main__':
    unittest.main(verbosity=2)
