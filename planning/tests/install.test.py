#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess
import shutil
import json
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Installation(unittest.TestCase):
    def test_round_trip_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = dict(os.environ, HOME=tmp, FM_HOME=tmp, XDG_CONFIG_HOME=str(home / 'xdg'))
            run = lambda *args: subprocess.run(args, env=env, capture_output=True, text=True)
            install = str(ROOT / 'bin/install-skills')
            mode = str(ROOT / 'bin/planning-mode')
            existing = home / '.codex/skills/firstmate-planning'
            existing.mkdir(parents=True)
            (existing / 'old').write_text('original')
            self.assertEqual(run(install, 'install', '--dry-run').returncode, 0)
            self.assertTrue((existing / 'old').exists())
            self.assertEqual(run(install, 'install').returncode, 0)
            self.assertEqual(run(install, 'install').returncode, 0)
            skill = existing / 'SKILL.md'
            self.assertTrue(skill.exists())
            skill.write_text('modified')
            self.assertNotEqual(run(install, 'uninstall').returncode, 0)
            self.assertEqual(skill.read_text(), 'modified')
            skill.write_text((ROOT / 'skills/firstmate-planning/SKILL.md').read_text())
            self.assertEqual(run(install, 'uninstall').returncode, 0)
            self.assertEqual((existing / 'old').read_text(), 'original')
            self.assertEqual(run(install, 'uninstall').returncode, 0)
            (home / 'config').mkdir()
            self.assertEqual(run(mode, 'show').stdout, 'native\n')
            self.assertEqual(run(mode, 'speckit').returncode, 0)
            self.assertEqual(run(mode, 'show').stdout, 'speckit\n')
            (home / 'config/planning/config.json').write_text('{invalid')
            self.assertNotEqual(run(mode, 'show').returncode, 0)
            # Invalid config fails rather than getting silently repaired by a toggle.
            (home / 'config/planning/config.json').unlink()
            self.assertEqual(run(mode, 'native').returncode, 0)

    def test_installed_runtime_survives_source_move(self):
        with tempfile.TemporaryDirectory(prefix='planning-installed-') as tmp:
            base = Path(tmp)
            package = base / 'source with arbitrary name'
            shutil.copytree(ROOT.parent / 'bin', package / 'bin')
            shutil.copytree(ROOT, package / 'planning')
            shutil.copytree(ROOT.parent / 'vendor', package / 'vendor')
            shutil.copytree(ROOT.parent / 'clean-room', package / 'clean-room')
            for part in ('LICENSE', 'NOTICE', 'compatibility.json'):
                shutil.copyfile(ROOT.parent / part, package / part)
            user = base / 'user'
            fm = base / 'fm'
            (fm / 'config').mkdir(parents=True)
            project = base / 'project'
            project.mkdir()
            env = dict(os.environ, HOME=str(user), FM_HOME=str(fm), XDG_CONFIG_HOME=str(base / 'xdg'),
                       PYTHONDONTWRITEBYTECODE='1')
            def run(*args):
                return subprocess.run(args, env=env, text=True, capture_output=True, timeout=20)
            install = package / 'planning/bin/install-skills'
            p = run(str(install), 'install')
            self.assertEqual(p.returncode, 0, p.stderr)
            package.rename(base / 'moved source')
            for harness in ('.codex', '.claude'):
                cli = user / harness / 'skills/firstmate-planning/scripts/fm-extension'
                p = run(str(cli), 'planning', 'resolve', '--project', str(project))
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(json.loads(p.stdout)['mode'], 'native')
                p = run(str(cli), 'speckit', 'prepare', '--project', str(project), '--dry-run')
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertFalse(json.loads(p.stdout)['network_required'])


if __name__ == '__main__':
    unittest.main()
