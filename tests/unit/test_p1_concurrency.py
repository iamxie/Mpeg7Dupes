"""Independent processes must share cache work without sharing scan lists."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
import test_find_reuse
import test_sigmake

TOOLS = Path(__file__).resolve().parents[2] / 'tools'


class Producers(test_sigmake.Base):
    def test_two_processes_publish_one_cache_key(self):
        self.a_video()
        self.con.close()
        (self.dir / 'index.sqlite').unlink()
        gate = self.dir / 'gate'
        gate.mkdir()
        script = '''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sigstore, sigmake
root = Path(sys.argv[2])
con = sigstore.open_db(root / 'index.sqlite')
(root / 'gate' / str(os.getpid())).touch()
deadline = time.monotonic() + 10
while len(list((root / 'gate').iterdir())) < 2:
    if time.monotonic() > deadline: raise RuntimeError('producer barrier timed out')
    time.sleep(.01)
made = sigmake.make(root / 'a.mp4', con, root / 'sig', fps=5.0, crop_bars=False,
                   detector='', ffmpeg=str(root / 'ffmpeg'), ffprobe=str(root / 'ffprobe'),
                   ffmpeg_version='fake', overwrite=len(sys.argv) > 3)
print(json.dumps({'filename': made.filename, 'produced': made.produced}))
con.close()
'''
        processes = [subprocess.Popen([sys.executable, '-c', script, str(TOOLS), str(self.dir)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for _ in range(2)]
        try:
            results = []
            for process in processes:
                out, err = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, err)
                results.append(json.loads(out))
            self.assertEqual(results[0]['filename'], results[1]['filename'])
            self.assertEqual(sorted(r['produced'] for r in results), [False, True])
            self.assertEqual(len(self.calls('ffmpeg')), 1)
            # A failed overwrite and another process's cache lookup share the
            # same key. The reader must continue to receive the old generation.
            command = [sys.executable, '-c', script, str(TOOLS), str(self.dir)]
            failed = subprocess.Popen(command + ['overwrite'], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True,
                                      env=dict(os.environ, FAKE_FFMPEG='fail'))
            reader = subprocess.Popen(command, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
            processes += [failed, reader]
            out, err = failed.communicate(timeout=20)
            self.assertEqual(failed.returncode, 1, err)
            self.assertIn('ffmpeg exited 1', err)
            out, err = reader.communicate(timeout=20)
            self.assertEqual(reader.returncode, 0, err)
            self.assertEqual(json.loads(out), {'filename': results[0]['filename'],
                                               'produced': False})
            self.assertEqual(len(list(self.sig_dir.glob('*.sig'))), 1)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate()


class Scans(unittest.TestCase):
    setUp = test_find_reuse.Run.setUp
    tearDown = test_find_reuse.Run.tearDown
    run_script = test_find_reuse.Run.run_script
    record = test_find_reuse.Run.record

    def test_concurrent_scans_equal_separate_runs_in_shared_store(self):
        # Both comparisons wait until their lists have been written. This
        # deterministically exposed source.txt being overwritten by the other.
        comparator = '''import csv, os, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('fake comparator'); sys.exit(0)
gate = os.environ.get('COMPARE_GATE')
if gate:
    root = Path(gate)
    (root / str(os.getpid())).touch()
    deadline = time.monotonic() + 10
    while len(list(root.iterdir())) < 2:
        if time.monotonic() > deadline: raise RuntimeError('scan barrier timed out')
        time.sleep(.01)
source = Path(sys.argv[sys.argv.index('-n') + 1]).read_text().strip()
candidates = Path(sys.argv[sys.argv.index('-l') + 1]).read_text().splitlines()
w = csv.writer(sys.stdout)
w.writerow(['First signature','Second signature','matchframes','framerateratio','whole',
            'time 1 [s]','time 2 [s]','begin 1 [s]','end 1 [s]','begin 2 [s]','end 2 [s]'])
for candidate in candidates:
    w.writerow([source,candidate,60,1,1,0,0,0,12,0,12])
'''
        Path(self.m7d).write_text('#!' + sys.executable + '\n' + comparator)
        original_source = self.src
        other = self.dir / 'other-source.mp4'
        other.write_bytes(b'another source')
        expected = []
        for source in (original_source, other):
            self.src = source
            done = self.run_script()
            self.assertEqual(done.returncode, 0, done.stderr)
            expected.append(self.record()['matches'])
        gate = self.dir / 'compare-gate'
        gate.mkdir()
        processes = []
        for i, source in enumerate((original_source, other)):
            cmd = [sys.executable, str(test_find_reuse.SCRIPT), '--source', str(source),
                   '--candidates', str(self.comp), '--sig-dir', str(self.dir / 'sig'),
                   '--no-crop-bars', '--ffmpeg', self.ffmpeg, '--ffprobe', self.ffprobe,
                   '--mpeg7dupes', self.m7d, '--json', str(self.dir / f'parallel-{i}.json')]
            env = dict(self.env, COMPARE_GATE=str(gate))
            processes.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              text=True, env=env))
        try:
            for i, process in enumerate(processes):
                out, err = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, out + err)
                record = json.loads((self.dir / f'parallel-{i}.json').read_text())
                self.assertEqual(record['matches'], expected[i])
            self.assertFalse((self.dir / 'sig' / 'source.txt').exists())
            self.assertFalse((self.dir / 'sig' / 'candidates.txt').exists())
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate()


if __name__ == '__main__':
    unittest.main(verbosity=2)
