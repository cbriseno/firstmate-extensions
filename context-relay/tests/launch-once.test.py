"""Public CLI execution-fence negatives without Herdr or a model."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'lib'))
from fm_context_relay import common


class LaunchFence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='relay-fence-')
        self.home = Path(self.temp.name)/'home'
        self.directory = self.home/'data/context-relay/attempts/test'
        self.directory.mkdir(parents=True)
        self.put('attempt.json', dict(id='test', home=str(self.home), directory=str(self.directory)))

    def tearDown(self):
        self.temp.cleanup()

    def put(self, name, value):
        (self.directory/name).write_text(json.dumps(value))

    def cli(self, generation=1, directory=None):
        return subprocess.run([str(ROOT/'bin/fm-context-relay'), '--home', str(self.home),
                               'relay', 'exec-launch', '--handoff', str(directory or self.directory),
                               '--generation', str(generation), '--herdr-session', 'fm-lab-test'],
                              capture_output=True, text=True, timeout=5)

    def test_delayed_original_command_refuses_after_recovery_generation(self):
        self.put('launch-generation.json', dict(generation=2, attempt='test'))
        p = self.cli(1)
        self.assertEqual(p.returncode, 1)
        self.assertIn('superseded', p.stdout)
        self.assertFalse((self.directory/'launch-execution-1.json').exists())

    def test_replayed_generation_cannot_run_startup_again(self):
        self.put('launch-generation.json', dict(generation=1, attempt='test'))
        self.put('launch-execution-1.json', dict(state='exited', rc=1))
        p = self.cli(1)
        self.assertEqual(p.returncode, 1)
        self.assertIn('already executed', p.stdout)

    def test_running_launcher_fence_prevents_second_execution(self):
        self.put('launch-generation.json', dict(generation=1, attempt='test'))
        with common.FileLock(str(self.directory/'.launch-execution.lock')):
            p = self.cli(1)
        self.assertEqual(p.returncode, 1)
        self.assertFalse((self.directory/'launch-execution-1.json').exists())

    def test_other_home_and_wrong_attempt_are_rejected(self):
        p = self.cli(directory=self.home/'different')
        self.assertEqual(p.returncode, 1)
        self.put('launch-generation.json', dict(generation=1, attempt='other'))
        p = self.cli(1)
        self.assertEqual(p.returncode, 1)
        self.assertIn('superseded', p.stdout)


if __name__ == '__main__':
    unittest.main()
