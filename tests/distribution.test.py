"""Installation portability and ownership protection, using temporary private homes."""
import json
import contextlib
import importlib.util
import io
from types import SimpleNamespace
from unittest import mock
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DistributionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='extension-distribution-')
        self.root = Path(self.tmp.name)
        self.clone = self.root / 'arbitrary clone with spaces'
        shutil.copytree(ROOT, self.clone, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        self.home = self.root / 'user'
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('FM_', 'CODEX_', 'CLAUDE_', 'XDG_'))}
        self.env.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / '.config'),
                        XDG_DATA_HOME=str(self.home / '.local/share'), PYTHONDONTWRITEBYTECODE='1')
        self.installed = self.home / '.local/share/firstmate-extensions/runtime'

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, source=None, code=0):
        result = subprocess.run([sys.executable, str((source or self.clone) / 'bin/fm-extension'), *args],
                                env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def test_renamed_source_installed_entry_and_restore(self):
        original = self.home / '.codex/skills/firstmate-planning/SKILL.md'
        original.parent.mkdir(parents=True)
        original.write_text('Original user skill\n')
        self.run_cli('install', '--language', 'es')
        self.run_cli('install', '--language', 'es')
        renamed = self.root / 'another checkout name'
        self.clone.rename(renamed)
        self.clone = renamed
        self.assertTrue(original.is_file())
        result = self.run_cli('planning', 'status', source=self.installed)
        self.assertEqual(json.loads(result.stdout)['language'], 'es')
        skill_cli = original.parent / 'scripts/fm-extension'
        result = subprocess.run([sys.executable, str(skill_cli), 'planning', 'status'], env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.run_cli('doctor', source=self.installed)
        self.run_cli('uninstall', source=self.installed)
        self.assertEqual(original.read_text(), 'Original user skill\n')
        self.assertFalse(self.installed.exists())
        self.run_cli('install')
        self.run_cli('uninstall')

    def test_modified_runtime_refuses_removal(self):
        self.run_cli('install')
        (self.installed / 'NOTICE').write_text('Modified by user\n')
        result = self.run_cli('uninstall', code=2)
        self.assertIn('changed', result.stderr)
        self.assertTrue(self.installed.exists())

    def test_missing_firstmate_refuses_before_writes(self):
        invalid = self.root / 'missing home'
        result = self.run_cli('install', '--home', str(invalid), code=2)
        self.assertIn('Firstmate', result.stderr)
        self.assertFalse(self.installed.exists())


class DoctorPlatformTest(unittest.TestCase):
    def test_platform_diagnostics_and_explicit_relay_requirement(self):
        spec = importlib.util.spec_from_file_location('distribution_test_subject', ROOT / 'bin/distribution.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        args = SimpleNamespace(home=None, require_relay=False, language='en')
        for platform, expected, warning in [('linux', 'accepted baseline', False),
                                             ('darwin', 'supported by design; macOS acceptance unverified', True),
                                             ('freebsd', 'unverified', True)]:
            with self.subTest(platform=platform), mock.patch.object(module.sys, 'platform', platform), \
                 mock.patch.object(module, 'home_problems', return_value=[]), \
                 mock.patch.object(module.shutil, 'which', return_value='/available'), \
                 mock.patch.object(module, 'runtime', return_value=Path('/nonexistent-runtime')), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(module.doctor(args), 0)
                result = json.loads(output.getvalue())
                self.assertEqual(result['platform'], expected)
                self.assertEqual(bool(result['warnings']), warning)
                self.assertEqual(result['relay'], 'unavailable' if platform == 'freebsd' else 'prerequisites present')
                if platform == 'freebsd':
                    self.assertEqual(result['relay_diagnostic']['code'], 'unsupported_platform')
                    args.require_relay = True
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(module.doctor(args), 2)
                    args.require_relay = False
                else:
                    self.assertIsNone(result['relay_diagnostic'])
                    args.require_relay = True
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(module.doctor(args), 0)
                    args.require_relay = False
                for name in ('python', 'bash', 'git', 'ps', 'firstmate', 'herdr', 'codex', 'claude'):
                    self.assertIn('version', result['tools'][name])
                    self.assertIn('category', result['tools'][name])


if __name__ == '__main__':
    unittest.main()
