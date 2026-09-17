"""Progress must be visible before work finishes, without changing results."""
import io
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import scan_progress
import test_find_reuse as fixture


class Output(io.StringIO):
    def __init__(self):
        super().__init__()
        self.heartbeat = threading.Event()
        self.flushed = False

    def write(self, text):
        if 'still running' in text:
            self.heartbeat.set()
        return super().write(text)

    def flush(self):
        self.flushed = True


class LiveProgress(unittest.TestCase):
    def test_closed_progress_pipe_does_not_abort_work(self):
        class Closed:
            def write(self, text):
                raise BrokenPipeError('closed progress consumer')
        with scan_progress.Progress('source', stream=Closed()) as progress:
            progress('extracting signature')
            progress.finish('signature ready')

    def test_current_stage_is_flushed_and_heartbeat_stops_on_exit(self):
        out = Output()
        with scan_progress.Progress('source 1/99: /video/74.mp4', stream=out,
                                    interval=.02) as progress:
            progress('extracting signature')
            self.assertTrue(out.flushed)
            self.assertIn('source 1/99: /video/74.mp4', out.getvalue())
            self.assertIn('extracting signature', out.getvalue())
            self.assertTrue(out.heartbeat.wait(2), out.getvalue())
        out.heartbeat.clear()
        self.assertFalse(out.heartbeat.wait(.06), 'progress continued after work ended')

    def test_failed_work_is_not_reported_as_complete(self):
        out = Output()
        with self.assertRaisesRegex(RuntimeError, 'decoder broke'):
            with scan_progress.Progress('candidate 2/5', stream=out) as progress:
                progress('extracting signature')
                raise RuntimeError('decoder broke')
        self.assertIn('failed during extracting signature', out.getvalue())
        self.assertNotIn('complete', out.getvalue())


class Scan(unittest.TestCase):
    setUp = fixture.Run.setUp
    tearDown = fixture.Run.tearDown
    run_script = fixture.Run.run_script
    record = fixture.Run.record

    def test_cold_and_hot_progress_names_files_stages_and_cache(self):
        cold = self.run_script()
        self.assertEqual(cold.returncode, 0, cold.stderr)
        record = self.record()
        self.assertIn('source 1/1', cold.stderr)
        self.assertIn(str(self.src / 'mine.mp4'), cold.stderr)
        self.assertIn('hashing video', cold.stderr)
        self.assertIn('extracting signature', cold.stderr)
        self.assertIn('signature generated', cold.stderr)
        self.assertIn('candidate 1/2', cold.stderr)
        self.assertIn('compare 1/1', cold.stderr)
        self.assertIn('2 unique candidates', cold.stderr)
        self.assertIn('[progress] scan complete', cold.stderr)
        hot = self.run_script()
        self.assertEqual(hot.returncode, 0, hot.stderr)
        self.assertIn('signature cache hit', hot.stderr)
        self.assertNotIn('extracting signature', hot.stderr)
        self.assertNotIn('hashing video', hot.stderr)
        self.assertEqual(self.record()['matches'], record['matches'])
        self.assertEqual(self.record()['summary'], record['summary'])
        quiet = self.run_script('--quiet')
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertNotIn('[progress]', quiet.stderr)
        self.assertIn('used mine.mp4', quiet.stdout)
        self.assertEqual(self.record()['matches'], record['matches'])

    def test_failed_signature_keeps_failure_status_and_next_file_progress(self):
        (self.comp / 'bad.mp4').write_bytes(b'not a video')
        done = self.run_script(FAKE_BAD='bad.mp4')
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('signature failed', done.stderr)
        self.assertIn('candidate 3/3', done.stderr)
        self.assertIn('[progress] scan incomplete', done.stderr)
        self.assertEqual(next(v for v in self.record()['candidates']
                              if v['name'] == 'bad.mp4')['status'], 'failed')


if __name__ == '__main__':
    unittest.main(verbosity=2)
