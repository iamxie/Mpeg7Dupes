"""Coverage selects a denominator, consistently from CLI to fallback and report."""
import copy
import html
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import render_report
import test_find_reuse as fixture
import test_crop_fallback as fallback_fixture
from fakesig import fake_signature


class Coverage(unittest.TestCase):
    tearDown = fixture.Run.tearDown
    run_script = fixture.Run.run_script
    record = fixture.Run.record

    def setUp(self):
        fixture.Run.setUp(self)
        (self.comp / 'two.mp4').unlink()
        self.template.write_bytes(fake_signature(frames=900, segments=20))
        long_sig = self.dir / 'long.sig'
        long_sig.write_bytes(fake_signature(frames=15000, segments=334))
        self.env.update(FAKE_LONG_SIG=str(long_sig), FAKE_M7D_FIRST='900')
        ffmpeg = Path(self.ffmpeg)
        ffmpeg.write_text(ffmpeg.read_text().replace('name=""',
            'case "$*" in *mine.mp4*) FAKE_SIG="$FAKE_LONG_SIG";; esac\nname=""'))
        probe = Path(self.ffprobe)
        probe.write_text(probe.read_text().replace('case "$*" in\n',
            'case "$*" in *mine.mp4*) FAKE_DURATION=3000;; *) FAKE_DURATION=180;; esac\n'
            'case "$*" in\n'))

    def test_long_original_short_excerpt_and_legacy_mode_reuse_the_same_cache(self):
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        record = self.record()
        self.assertEqual(record['schema'], 'find_reuse/9')
        self.assertEqual(record['settings']['coverage_basis'], 'shorter')
        hit = record['matches'][0]
        self.assertEqual((hit['source_coverage_percent'], hit['candidate_coverage_percent'],
                          hit['shorter_coverage_percent'], hit['coverage_percent']),
                         (6, 100, 100, 100))
        self.assertEqual(hit['matched_seconds'], 180)
        self.assertIn('source 6.0%, candidate 100.0%', done.stdout)
        page = render_report.render(record, [])
        self.assertIn('40% of the shorter video', page)
        self.assertIn('Source coverage', page)
        self.assertIn('Candidate coverage', page)
        self.assertIn('6.0%', page)
        self.assertIn('100.0%', page)
        self.assertIn('03:00', page)
        count = self.log.read_text().count('signature=filename=')
        legacy = self.run_script('--min-source-coverage', '40', '--show-misses')
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertEqual(self.record()['matches'], [])
        self.assertEqual(self.record()['candidates'][0]['best_coverage_percent'], 6)
        self.assertIsNone(self.record()['settings']['min_coverage'])
        self.assertEqual(self.record()['settings']['min_source_coverage'], 40)
        self.assertIn('best 6%', legacy.stdout)
        self.assertIn('40% of the source', render_report.render(self.record(), []))
        self.assertEqual(self.log.read_text().count('signature=filename='), count)
        self.assertEqual(self.run_script('--min-source-coverage', '6').returncode, 0)
        self.assertEqual(self.record()['matches'][0]['coverage_percent'], 6)

    def test_short_source_long_candidate_uses_shorter_denominator_too(self):
        self.src, self.comp = self.comp / 'one.mp4', self.src / 'mine.mp4'
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        hit = self.record()['matches'][0]
        self.assertEqual((hit['coverage_percent'], hit['source_coverage_percent'],
                          hit['candidate_coverage_percent']), (100, 100, 6))

    def test_cli_conflict_stops_before_running_tools_and_points_to_readme(self):
        done = self.run_script('--min-coverage', '40', '--min-source-coverage', '0')
        self.assertEqual(done.returncode, 2)
        self.assertIn('Choose only one', done.stderr)
        self.assertIn('README.md', done.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.dir / 'sig').exists())

    def test_toml_conflict_is_rejected_even_with_cli_override(self):
        config = self.dir / 'conflict.toml'
        config.write_text('min_coverage = 40\nmin_source_coverage = 0\n')
        done = self.run_script('--config', str(config), '--min-coverage', '20')
        self.assertEqual(done.returncode, 2)
        self.assertIn('Choose only one', done.stderr)
        self.assertIn('README.md', done.stderr)
        self.assertFalse(self.log.exists())

    def test_cli_choice_overrides_the_other_toml_choice(self):
        config = self.dir / 'coverage.toml'
        config.write_text('min_source_coverage = 40\n')
        self.assertEqual(self.run_script('--config', str(config)).returncode, 0)
        self.assertEqual(self.record()['matches'], [])
        self.assertEqual(self.run_script('--config', str(config), '--min-coverage', '40').returncode, 0)
        self.assertEqual(len(self.record()['matches']), 1)
        config.write_text('min_coverage = 40\n')
        self.assertEqual(self.run_script('--config', str(config), '--min-source-coverage', '40').returncode, 0)
        self.assertEqual(self.record()['matches'], [])

    def test_new_threshold_rejects_invalid_values_but_allows_zero(self):
        for value in ('nan', 'inf', '-1', '101'):
            with self.subTest(value=value):
                done = self.run_script('--min-source-coverage', value)
                self.assertEqual(done.returncode, 2)
                self.assertFalse(self.log.exists())
        self.assertEqual(self.run_script('--min-source-coverage', '0').returncode, 0)
        self.assertEqual(self.record()['settings']['min_source_coverage'], 0)

    def test_invalid_toml_threshold_and_short_candidate_warning(self):
        config = self.dir / 'coverage.toml'
        for value in ('true', '"40"', 'nan', '101'):
            with self.subTest(value=value):
                config.write_text(f'min_source_coverage = {value}\n')
                done = self.run_script('--config', str(config))
                self.assertEqual(done.returncode, 2)
                self.assertFalse(self.log.exists())
        self.template.write_bytes(fake_signature(frames=60, segments=2))
        self.assertEqual(self.run_script(FAKE_M7D_FIRST='60').returncode, 0)
        self.assertIn('short_candidate', [w['code'] for w in self.record()['candidates'][0]['warnings']])
        self.assertEqual(self.run_script('--min-source-coverage', '40', FAKE_M7D_FIRST='60').returncode, 0)
        self.assertNotIn('short_candidate', [w['code'] for w in self.record()['candidates'][0]['warnings']])

    def test_fallback_retries_only_misses_under_the_selected_rule(self):
        fallback_fixture.Scan.comparator(self)
        comparator = Path(self.m7d)
        comparator.write_text(comparator.read_text().replace('60 if hit else 10', '900 if hit else 10'))
        done = self.run_script('--crop-fallback', ALL_HIT='1')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.record()['fallback']['pairs'], [])
        self.assertFalse(self.record()['matches'][0]['requires_review'])
        legacy = self.run_script('--crop-fallback', '--min-source-coverage', '40', ALL_HIT='1')
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertEqual(len(self.record()['fallback']['pairs']), 1)
        self.assertEqual(self.record()['matches'], [])

    def test_fallback_can_recover_a_short_candidate_under_the_new_rule(self):
        fallback_fixture.Scan.comparator(self)
        comparator = Path(self.m7d)
        comparator.write_text(comparator.read_text().replace('60 if hit else 10', '900 if hit else 10'))
        done = self.run_script('--crop-fallback')
        self.assertEqual(done.returncode, 0, done.stderr)
        hit = self.record()['matches'][0]
        self.assertTrue(hit['requires_review'])
        self.assertEqual(hit['coverage_percent'], 100)
        self.assertEqual(hit['source_coverage_percent'], 6)

    def test_legacy_records_keep_source_threshold_interpretation(self):
        self.assertEqual(self.run_script('--min-source-coverage', '6').returncode, 0)
        for version in range(3, 9):
            with self.subTest(version=version):
                record = copy.deepcopy(self.record())
                record['schema'] = f'find_reuse/{version}'
                record['settings']['min_coverage'] = 6
                record['settings'].pop('min_source_coverage')
                record['settings'].pop('coverage_basis')
                for hit in record['matches']:
                    for key in ('coverage_basis', 'source_coverage_percent',
                                'candidate_coverage_percent', 'shorter_coverage_percent'):
                        hit.pop(key)
                page = render_report.render(record, [])
                self.assertIn('6% of the source', page)
                self.assertNotIn('6% of the shorter video', page)
                self.assertIn("source's own frame count", html.unescape(page))

    def test_speed_and_overrun_are_not_disguised_by_clamping(self):
        done = self.run_script(FAKE_M7D_RATIO='0.8', FAKE_M7D_FIRST='1000')
        self.assertEqual(done.returncode, 0, done.stderr)
        hit = self.record()['matches'][0]
        self.assertTrue(hit['overrun'])
        self.assertGreater(hit['candidate_coverage_percent'], 100)
        page = render_report.render(self.record(), [])
        self.assertIn('Overrun', page)
        self.assertIn('slower clip', page)
        self.assertNotIn('data-start=', page)


if __name__ == '__main__':
    unittest.main(verbosity=2)
