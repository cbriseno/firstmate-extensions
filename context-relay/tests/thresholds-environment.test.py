"""Exercise test-core discovery through its executable, without a real core."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TEST = Path(__file__).with_name('thresholds.test.py')
PARSER_CASE = 'ThresholdTests.test_parser_amounts_percent_and_order'


class CoreEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='relay-core-environment-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.core = self.root/'configured core with spaces'
        (self.core/'bin').mkdir(parents=True)
        for name in ('fm-primary-scope-lib.sh', 'fm-session-lock-lib.sh', 'fm-cursor-lib.sh', 'fm-lock.sh'):
            (self.core/'bin'/name).write_text('# Synthetic dependency for parser-only fixtures.\n')
        self.missing = self.root/'missing core'
        self.env = dict(os.environ, FM_LIVE='0')
        for name in ('FIRSTMATE_ROOT', 'FM_HOME'):
            self.env.pop(name, None)

    def launch(self, variables, case=PARSER_CASE):
        return subprocess.run([sys.executable, '-B', str(TEST), case], env=dict(self.env, **variables),
                              capture_output=True, text=True, timeout=30)

    def test_no_configuration_names_the_two_supported_variables(self):
        result = self.launch({}, '--help')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Set FIRSTMATE_ROOT or FM_HOME', result.stderr)

    def test_fm_home_fallback_runs_the_public_parser_fixture(self):
        result = self.launch({'FM_HOME': str(self.core)})
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('Ran 1 test', result.stderr)
        self.assertIn('OK', result.stderr)

    def test_explicit_firstmate_root_has_priority(self):
        result = self.launch({'FIRSTMATE_ROOT': str(self.core), 'FM_HOME': str(self.missing)})
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_invalid_explicit_root_does_not_silently_use_fm_home(self):
        result = self.launch({'FIRSTMATE_ROOT': str(self.missing), 'FM_HOME': str(self.core)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.missing), result.stderr)
        self.assertIn('Incomplete Firstmate core', result.stderr)

    def test_invalid_fm_home_reports_the_configured_location(self):
        result = self.launch({'FM_HOME': str(self.missing)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.missing), result.stderr)
        self.assertIn('fm-primary-scope-lib.sh', result.stderr)


if __name__ == '__main__':
    unittest.main()
