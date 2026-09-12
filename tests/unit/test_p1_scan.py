"""Failure records and strict scan settings, exercised through the CLI."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_find_reuse


class Scan(unittest.TestCase):
    setUp = test_find_reuse.Run.setUp
    tearDown = test_find_reuse.Run.tearDown
    run_script = test_find_reuse.Run.run_script
    record = test_find_reuse.Run.record
    statuses = test_find_reuse.Run.statuses

    def test_invalid_settings_fail_before_decoding(self):
        for option, value in (('--min-coverage', 'nan'), ('--min-coverage', '101'),
                              ('--fps', 'inf'), ('--fps', '0'), ('--jobs', '-1'),
                              ('--thxh', '-1'), ('--config', 'missing.toml')):
            with self.subTest(option=option, value=value):
                done = self.run_script(option, value)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertNotIn('Traceback', done.stderr)
                self.assertFalse((self.dir / 'sig').exists())

    def test_invalid_toml_types_are_named(self):
        for value in ('crop_bars = "false"', 'fps = true', 'extensions = "mp4"',
                      'jobs = 1.2', 'thxh = 2147483648', 'typo_setting = 1'):
            with self.subTest(value=value):
                config = self.dir / 'invalid.toml'
                config.write_text(value)
                # No CLI crop override, so exercise the actual TOML value.
                import find_reuse
                args = find_reuse.build_parser().parse_args([
                    '--source', str(self.src), '--candidates', str(self.comp),
                    '--config', str(config)])
                with self.assertRaises((ValueError, SystemExit)):
                    find_reuse.load_settings(args)

    def test_failed_sources_keep_all_uncompared_candidates(self):
        done = self.run_script(FAKE_BAD='mine.mp4')
        self.assertEqual(done.returncode, 2, done.stderr)
        record = self.record()
        self.assertEqual(record['summary']['candidates_requested'], 2)
        self.assertEqual(set(self.statuses(record).values()), {'not_compared'})
        self.assertEqual(record['misses'], [])

    def test_no_usable_candidates_exits_2(self):
        done = self.run_script(FAKE_BAD='/comp/')
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertEqual(set(self.statuses(self.record()).values()), {'failed'})

    def test_compare_failure_writes_record_and_preserves_inventory(self):
        done = self.run_script(FAKE_M7D_FAIL='1')
        self.assertEqual(done.returncode, 2, done.stderr)
        record = self.record()
        self.assertEqual(set(self.statuses(record).values()), {'not_compared'})
        self.assertEqual(record['error']['stage'], 'compare')
        self.assertEqual(record['misses'], [])

    def test_relative_tools_survive_signature_directory(self):
        done = self.run_script('--ffmpeg', './ffmpeg', '--ffprobe', './ffprobe',
                               '--mpeg7dupes', './mpeg7dupes')
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_missing_program_replaces_previous_record_with_failure(self):
        self.assertEqual(self.run_script().returncode, 0)
        done = self.run_script('--mpeg7dupes', './missing-binary')
        self.assertEqual(done.returncode, 2, done.stderr)
        record = self.record()
        self.assertEqual(record['summary']['exit_status'], 2)
        self.assertEqual(record['matches'], [])

    def test_empty_sources_still_account_for_candidates(self):
        (self.src / 'mine.mp4').unlink()
        done = self.run_script()
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertEqual(self.record()['summary']['candidates_requested'], 2)

    def test_unrelated_csv_row_is_rejected_with_record(self):
        path = Path(self.m7d)
        path.write_text(path.read_text().replace('src=$(cat "$sources")', 'src=wrong-source.sig'))
        done = self.run_script()
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn('unexpected comparison pair', self.record()['error']['reason'])
        self.assertEqual(self.record()['misses'], [])

    def test_atomic_json_failure_preserves_old_record(self):
        from unittest.mock import patch
        import find_reuse
        path = self.dir / 'record.json'
        path.write_text('old record')
        with patch.object(find_reuse.os, 'replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                find_reuse.write_record(path, {'matches': []})
        self.assertEqual(path.read_text(), 'old record')
        self.assertEqual(list(self.dir.glob('*.part')), [])

    def test_later_compare_failure_keeps_earlier_matches_and_scope(self):
        (self.src / 'another.mp4').write_bytes(b'another source')
        path = Path(self.m7d)
        counter = self.dir / 'comparison-count'
        script = path.read_text().replace('src=$(cat "$sources")',
            'echo run >> "' + str(counter) + '"\n'
            'if [ "$(wc -l < "' + str(counter) + '")" -eq 2 ]; then exit 1; fi\n'
            'src=$(cat "$sources")')
        path.write_text(script)
        done = self.run_script('--show-misses')
        self.assertEqual(done.returncode, 2, done.stderr)
        rec = self.record()
        self.assertEqual(len(rec['comparison']['sources_completed']), 1)
        self.assertEqual(len(rec['matches']), 1)
        self.assertEqual(rec['misses'], [])
        self.assertEqual(set(self.statuses(rec).values()), {'matched', 'not_compared'})
        self.assertNotIn('no sign of any source', done.stdout)
        import render_report
        page = render_report.render(rec, [])
        self.assertIn('not compared', page)
        self.assertIn('Compared against these sources only', page)

    def test_invalid_csv_numbers_and_shapes_fail_closed(self):
        import find_reuse
        header = ('First signature,Second signature,matchframes,framerateratio,whole,'
                  'time 1 [s],time 2 [s],begin 1 [s],end 1 [s],begin 2 [s],end 2 [s]\n')
        for row in ('s,c,nan,1,1,0,0,0,1,0,1', 's,c,2,0,1,0,0,0,1,0,1',
                    's,c,2,inf,1,0,0,0,1,0,1', 's,c,2,1,1,0,0,5,1,0,1',
                    's,c,2,1,1,0,0,0,1,0', 's,c,2,1,1,0,0,0,1,0,1,extra'):
            with self.subTest(row=row), self.assertRaises(ValueError):
                find_reuse.parse_comparison(header + row, 's', {'c'})


    def test_tool_that_cannot_start_is_not_reported_as_a_bad_video(self):
        Path(self.ffmpeg).write_text('#!/nonexistent-interpreter\n')
        done = self.run_script()
        self.assertEqual(done.returncode, 2, done.stderr)
        record = self.record()
        self.assertEqual(record['error']['stage'], 'tools')
        self.assertIn('cannot start', record['error']['reason'])
        self.assertEqual(len(record['candidates']), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
