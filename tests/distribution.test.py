"""Installation portability and ownership protection, using temporary private homes."""
import json
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

    def test_active_relay_preserves_installed_files(self):
        self.run_cli('install')
        firstmate = self.root / 'firstmate'
        active = firstmate / 'state/context-relay/active.json'
        active.parent.mkdir(parents=True)
        active.write_text('{}')
        receipt = self.installed / 'installation.json'
        record = json.loads(receipt.read_text())
        record['home'] = str(firstmate)
        receipt.write_text(json.dumps(record))
        result = self.run_cli('uninstall', code=2)
        self.assertIn('active', result.stderr)
        self.assertTrue(self.installed.exists())
        self.assertTrue((self.home / '.codex/skills/firstmate-planning/SKILL.md').exists())

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
        result = self.run_cli('relay', 'status', code=2)
        self.assertIn('Firstmate home unavailable', result.stderr)


if __name__ == '__main__':
    unittest.main()
