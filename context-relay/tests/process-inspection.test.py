"""Process inspection is conservative when POSIX ps lacks identity evidence."""
import os
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from fm_context_relay import process_inspection as process


class InspectionTest(unittest.TestCase):
    def test_linux_proc(self):
        if sys.platform != 'linux':
            self.skipTest('Linux proc fixture')
        row = process.process(os.getpid())
        self.assertEqual(row['pid'], os.getpid())
        self.assertTrue(row['start'])
        self.assertIn(os.getpid(), process.pids())
        self.assertTrue(process.argv_bytes(os.getpid()))

    @patch.object(process.sys, 'platform', 'darwin')
    @patch.object(process.subprocess, 'run')
    def test_bsd_ps_and_unknown(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, '  42  1 /a path/My App /a path/My App --arg with spaces\n  73 42 python3 python3 -B x.py\n', '')
        rows = process.ps()
        self.assertEqual([(r['pid'], r['ppid']) for r in rows], [(42, 1), (73, 42)])
        self.assertEqual(rows[0]['comm'], 'unknown')
        self.assertEqual(rows[0]['command'], 'unknown')
        self.assertIn('-o', run.call_args.args[0])
        self.assertIsNone(process.process(99))
        with self.assertRaises(process.Unknown):
            process.process(42)
        with self.assertRaises(process.Unknown):
            process.argv_bytes(42)

    @patch.object(process.subprocess, 'run')
    def test_ps_failure_is_unknown(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, '', 'error')
        with self.assertRaises(process.Unknown):
            process.ps(42)
        self.assertEqual(run.call_args.args[0][:3], ['ps', '-p', '42'])


class RelayPlatformTest(unittest.TestCase):
    def test_unverified_platform_refuses_lifecycle_before_home_or_backend(self):
        from fm_context_relay import cli
        cases = [
            ['relay', 'start'],
            ['relay', 'prepare', '--harness', 'codex', '--notes', 'n', '--launch', 'l', '--new-pane', 'p', '--authority', 'x', '--herdr-session', 's'],
            ['relay', '_drive', '--handoff', 'h', '--herdr-session', 's'],
            ['relay', 'exec-launch', '--handoff', 'h', '--generation', '1', '--herdr-session', 's'],
            ['pane-close', 'inspect', '--handoff', 'h', '--herdr-session', 's'],
            ['auto', 'authorize', '--captain-words', 'x', '--reason', 'y'],
            ['set', 'relay_mode', 'assisted'],
        ]
        with patch.object(process.sys, 'platform', 'darwin'), patch.object(cli.os, 'execve') as execute:
            for args in cases:
                with self.subTest(args=args), contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(cli.main(args), 1)
                    diagnostic = json.loads(output.getvalue())
                    self.assertEqual(diagnostic['code'], process.RELAY_UNAVAILABLE_CODE)
                    self.assertEqual(diagnostic['reason'], process.RELAY_UNAVAILABLE_MESSAGE)
            execute.assert_not_called()

    def test_non_lifecycle_status_and_kill_switch_not_platform_blocked(self):
        from fm_context_relay import cli
        with patch.object(process.sys, 'platform', 'darwin'), \
             patch.object(cli, 'resolve_home', return_value=None), \
             contextlib.redirect_stderr(io.StringIO()):
            for args in (['auto', 'status'], ['auto', 'off'], ['relay', 'status', '--handoff', 'h']):
                self.assertEqual(cli.main(args), 2)  # Missing home, not platform refusal.


if __name__ == '__main__':
    unittest.main()
