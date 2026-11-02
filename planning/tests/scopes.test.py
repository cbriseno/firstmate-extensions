#!/usr/bin/env python3
"""Offline executable contract fixtures. No model, credentials or live home access."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / 'bin/fm-extension'


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode & 0o777)
            for p in root.rglob('*') if p.is_file()}


class Scopes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='planning-fixture-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / 'home one'
        (self.home / 'config').mkdir(parents=True)
        self.other_home = self.base / 'home two'
        (self.other_home / 'config').mkdir(parents=True)
        self.project = self.base / 'project with spaces'
        self.project.mkdir()
        self.env = dict(os.environ, HOME=str(self.base / 'user'), FM_HOME=str(self.home),
                        XDG_CONFIG_HOME=str(self.base / 'xdg'), PYTHONDONTWRITEBYTECODE='1')
        self.cli = CLI

    def run_cli(self, *args, ok=True):
        p = subprocess.run([str(self.cli), *map(str, args)], env=self.env, capture_output=True, text=True, timeout=20)
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            return json.loads(p.stdout)
        self.assertNotEqual(p.returncode, 0, p.stdout)
        return p.stderr + p.stdout

    def resolve(self, *args, **kw):
        return self.run_cli('planning', 'resolve', '--project', self.project, *args, **kw)

    def set_mode(self, scope, mode, *args):
        return self.run_cli('planning', 'set', '--scope', scope, '--mode', mode, *args)

    def preview(self, **kw):
        return self.run_cli('speckit', 'prepare', '--project', self.project, '--dry-run', **kw)

    def prepare(self, preview=None, *args, **kw):
        p = preview or self.preview()
        return self.run_cli('speckit', 'prepare', '--project', self.project,
                            '--preview-digest', p['preview_digest'], '--delivery-mode', 'local-only', *args, **kw)

    def test_precedence_clear_and_freeze(self):
        self.assertEqual(self.resolve()['origin'], 'built-in')
        self.set_mode('installation', 'speckit')
        self.assertEqual(self.resolve()['origin'], 'installation')
        self.assertIsNone(self.resolve()['effective'])
        self.set_mode('home', 'native')
        self.assertEqual(self.resolve()['mode'], 'native')
        self.set_mode('project', 'speckit', '--project', self.project)
        frozen = self.resolve()
        self.assertEqual(frozen['origin'], 'project:home')
        brief = self.base / 'brief'
        brief.write_text(frozen['brief_lines'] + '\n')
        self.assertEqual(self.resolve('--task-mode', 'native')['origin'], 'task')
        self.run_cli('planning', 'clear', '--scope', 'project', '--project', self.project)
        self.assertEqual(self.resolve()['mode'], 'native')
        self.assertEqual(self.resolve('--brief', brief)['mode'], 'speckit')
        self.assertIn('conflicts', self.resolve('--brief', brief, '--task-mode', 'native', ok=False))
        self.run_cli('planning', 'clear', '--scope', 'home')
        self.assertEqual(self.resolve()['mode'], 'speckit')
        self.run_cli('planning', 'clear', '--scope', 'installation')
        self.assertEqual(self.resolve()['mode'], 'native')

    def test_conflicting_options_and_missing_project(self):
        self.run_cli('planning', 'set', '--scope', 'home', '--scope', 'installation', '--mode', 'native', ok=False)
        self.run_cli('planning', 'set', '--scope', 'home', '--mode', 'speckit', '--project', self.project, ok=False)
        self.run_cli('planning', 'set', '--scope', 'project', '--mode', 'speckit', ok=False)
        self.run_cli('planning', 'resolve', '--project', self.base / 'absent', ok=False)
        self.run_cli('planning', 'resolve', ok=False)

    def test_invalid_config_never_downgrades(self):
        self.set_mode('home', 'speckit')
        path = self.home / 'config/planning/config.json'
        path.write_text('{broken')
        self.assertIn('repair', self.resolve('--task-mode', 'native', ok=False))
        self.assertIn('repair', self.run_cli('planning', 'status', ok=False))
        path.write_text(json.dumps({'schema': 1, 'default': 'wrong'}))
        self.assertIn('Invalid planning config', self.resolve(ok=False))

    def test_legacy_read_and_migration(self):
        old = self.home / 'config/planning-mode'
        old.write_text('speckit\n')
        self.assertEqual(self.resolve()['origin'], 'home:legacy')
        self.run_cli('planning', 'clear', '--scope', 'home')
        self.assertEqual(self.resolve()['mode'], 'native')
        self.assertEqual(old.read_text(), 'speckit\n')

    def test_status_all_exceptions_and_scope_clear(self):
        self.set_mode('project', 'speckit', '--project', self.project)
        self.set_mode('project', 'native', '--project', self.project, '--home', self.other_home)
        status = self.run_cli('planning', 'status')
        self.assertEqual(len(status['project_exceptions']), 2)
        self.assertEqual({x['origin'] for x in status['project_exceptions']}, {'project:home'})
        self.assertIn('pending', {x['state'] for x in status['project_exceptions']})
        before = snapshot(self.base / 'xdg')
        planned = self.set_mode('installation', 'native', '--clear-project-overrides', '--dry-run')
        self.assertEqual(len(planned['cleared_project_overrides']), 2)
        self.assertEqual(before, snapshot(self.base / 'xdg'))
        result = self.set_mode('installation', 'native', '--clear-project-overrides')
        self.assertEqual(result['status']['project_exceptions'], [])

    def test_private_stable_ids_and_relocation(self):
        self.set_mode('project', 'speckit', '--project', self.project)
        key = self.resolve()['project_id']
        clone = self.base / 'clone' / self.project.name
        clone.mkdir(parents=True)
        self.assertIsNone(self.run_cli('planning', 'resolve', '--project', clone)['project_id'])
        moved = self.base / 'renamed'
        self.project.rename(moved)
        self.run_cli('planning', 'status', ok=False)
        self.run_cli('planning', 'relocate', '--id', key, '--project', moved)
        self.project = moved
        self.assertEqual(self.resolve()['project_id'], key)
        self.assertEqual(self.resolve()['mode'], 'speckit')
        self.run_cli('planning', 'relocate', '--id', key, '--project', clone, ok=False)

    def test_dry_run_is_read_only_and_bound(self):
        before = snapshot(self.base)
        p = self.preview()
        self.assertFalse(p['network_required'])
        self.assertEqual(before, snapshot(self.base))
        self.assertIn('Preview changed', self.run_cli('speckit', 'prepare', '--project', self.project,
                      '--preview-digest', 'stale', '--delivery-mode', 'local-only', ok=False))
        self.assertEqual(snapshot(self.project), {})
        self.assertIn('delivery-mode', self.run_cli('speckit', 'prepare', '--project', self.project,
                      '--preview-digest', self.preview()['preview_digest'], ok=False))

    def test_prepare_verify_idempotency_disable_rollback(self):
        self.set_mode('project', 'speckit', '--project', self.project)
        self.assertIn('pending', self.resolve('--require-core', ok=False))
        self.assertEqual(self.prepare()['state'], 'verified')
        before = snapshot(self.project)
        self.assertEqual(self.prepare()['state'], 'verified')
        self.assertEqual(before, snapshot(self.project))
        self.assertEqual(self.resolve('--require-core')['effective'], 'speckit')
        verify = self.run_cli('speckit', 'verify', '--project', self.project)
        self.assertEqual(verify['native_discovery'], 'unverified')
        authored = self.project / 'specs/user/spec.md'
        authored.parent.mkdir(parents=True)
        authored.write_text('authored')
        self.set_mode('project', 'native', '--project', self.project)
        self.assertEqual(self.resolve()['mode'], 'native')
        self.assertTrue((self.project / '.specify/core/LICENSE').is_file())
        self.run_cli('speckit', 'rollback', '--project', self.project)
        self.assertEqual(snapshot(self.project), {'specs/user/spec.md': (b'authored', 0o644)})

    def test_rollback_preserves_modified_managed_file(self):
        self.prepare()
        path = self.project / '.specify/templates/spec-template.md'
        path.write_text('user changed template')
        self.run_cli('speckit', 'verify', '--project', self.project, ok=False)
        self.run_cli('speckit', 'rollback', '--project', self.project, ok=False)
        self.assertEqual(path.read_text(), 'user changed template')

    def test_unknown_core_and_symlinks_fail(self):
        path = self.project / '.specify/templates/spec-template.md'
        path.parent.mkdir(parents=True)
        path.write_text('unmanaged')
        self.assertIn('Unmanaged', self.preview(ok=False))
        path.unlink()
        target = self.base / 'elsewhere'
        target.write_text('private')
        path.symlink_to(target)
        self.assertIn('Symlink', self.preview(ok=False))
        self.assertEqual(target.read_text(), 'private')

    def test_project_policy_is_a_restriction(self):
        agents = self.project / 'AGENTS.md'
        self.set_mode('project', 'speckit', '--project', self.project)
        agents.write_text('planning_policy: native\n')
        self.assertIn('Project policy requires native', self.resolve(ok=False))
        self.assertIn('Project policy requires native', self.preview(ok=False))
        agents.write_text('Never use Spec Kit in this repository.\n')
        self.preview(ok=False)
        agents.write_text('Use local delivery. Never publish.\n')
        preview = self.preview()
        self.assertIn('policy review required', self.prepare(preview, ok=False))
        preview = self.preview()
        self.prepare(preview, '--policy-review', preview['policy']['digest'])
        self.assertEqual(agents.read_text(), 'Use local delivery. Never publish.\n')

    def test_changed_policy_invalidates_preview(self):
        p = self.preview()
        (self.project / 'AGENTS.md').write_text('Review all project writes.\n')
        self.assertIn('Preview changed', self.prepare(p, ok=False))

    def test_changed_vendor_fails_offline(self):
        self.copy_distribution()
        p = self.cli.parent.parent / 'vendor/spec-kit/1.2.4/templates/spec-template.md'
        p.write_text('corrupted')
        self.assertIn('digest mismatch', self.preview(ok=False))
        self.assertEqual(snapshot(self.project), {})

    def copy_distribution(self):
        dest = self.base / 'arbitrary renamed distribution'
        for part in ('bin', 'planning/lib', 'planning/core', 'vendor'):
            shutil.copytree(ROOT / part, dest / part)
        shutil.copyfile(ROOT / 'planning/pin.json', dest / 'planning/pin.json')
        self.cli = dest / 'bin/fm-extension'

    def test_failed_probe_restores_exact_project(self):
        self.copy_distribution()
        root = self.cli.parent.parent
        helper = root / 'planning/core/core.py'
        helper.write_text('raise SystemExit(7)\n')
        pin_path = root / 'planning/pin.json'
        pin = json.loads(pin_path.read_text())
        sha = lambda b: hashlib.sha256(b).hexdigest()
        pin['helpers']['core.py'] = sha(helper.read_bytes())
        pin.pop('digest')
        pin['digest'] = sha(json.dumps(pin, sort_keys=True).encode())
        pin_path.write_text(json.dumps(pin))
        (self.project / 'owned.txt').write_text('keep')
        before = snapshot(self.project)
        self.set_mode('project', 'speckit', '--project', self.project)
        self.assertIn('probe failed', self.prepare(ok=False))
        self.assertEqual(snapshot(self.project), before)
        status = self.run_cli('planning', 'status')
        self.assertEqual(status['project_exceptions'][0]['state'], 'failed')

    def test_project_lock_rejects_concurrent_preparation(self):
        self.set_mode('project', 'speckit', '--project', self.project)
        key = self.resolve()['project_id']
        p = self.preview()
        state = self.base / 'xdg/fm-planning/projects' / key
        state.mkdir(parents=True)
        with (state / 'state.lock').open('w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            self.assertIn('busy', self.prepare(p, ok=False))
        self.assertEqual(snapshot(self.project), {})

    def test_language_and_no_network_environment(self):
        self.set_mode('home', 'native', '--language', 'es')
        self.assertEqual(self.resolve()['language'], 'es')
        self.assertIn('completada', self.run_cli('planning', 'status')['message'])
        self.env.update(HTTPS_PROXY='http://proxy.invalid', HTTP_PROXY='http://proxy.invalid')
        self.prepare()

    def test_two_project_roots_and_homes_are_independent(self):
        other = self.base / 'worktree'
        other.mkdir()
        self.set_mode('project', 'speckit', '--project', self.project)
        self.set_mode('project', 'native', '--project', other)
        self.assertNotEqual(self.resolve()['project_id'], self.run_cli('planning', 'resolve', '--project', other)['project_id'])
        self.assertEqual(self.run_cli('planning', 'resolve', '--project', self.project, '--home', self.other_home)['mode'], 'native')
        self.prepare()
        self.assertEqual(snapshot(other), {})

    def test_interrupted_journal_rollback(self):
        self.prepare()
        key = self.resolve()['project_id']
        state = self.base / 'xdg/fm-planning/projects' / key
        shutil.copyfile(state / 'backup.json', state / 'journal.json')
        self.assertIn('journal', self.run_cli('speckit', 'verify', '--project', self.project, ok=False))
        self.run_cli('speckit', 'rollback', '--project', self.project)
        self.assertEqual(snapshot(self.project), {})

    def test_invalid_journal_does_not_remove_owned_files(self):
        self.prepare()
        key = self.resolve()['project_id']
        state = self.base / 'xdg/fm-planning/projects' / key
        data = json.loads((state / 'backup.json').read_text())
        data['created_dirs'].append('../outside')
        (state / 'backup.json').write_text(json.dumps(data))
        before = snapshot(self.project)
        self.assertIn('Invalid rollback', self.run_cli('speckit', 'rollback', '--project', self.project, ok=False))
        self.assertEqual(snapshot(self.project), before)

    def test_frozen_provenance_language_and_bad_duplicate(self):
        self.set_mode('home', 'speckit', '--language', 'es')
        initial = self.resolve()
        brief = self.base / 'durable brief'
        brief.write_text(initial['brief_lines'] + '\n')
        self.set_mode('home', 'native', '--language', 'en')
        resumed = self.resolve('--brief', brief)
        self.assertEqual(resumed['origin'], initial['origin'])
        self.assertEqual(resumed['language'], 'es')
        self.assertEqual(resumed['core'], initial['core'])
        brief.write_text('planning_mode: native\nplanning_mode: invalid extra words\n')
        self.assertIn('exactly one', self.resolve('--brief', brief, ok=False))

    def test_preparation_never_changes_git_or_project_instructions(self):
        git = self.project / '.git'
        git.mkdir()
        head = git / 'HEAD'
        head.write_text('ref: refs/heads/fixture\n')
        agents = self.project / 'AGENTS.md'
        agents.write_text('Keep current Git state.\n')
        p = self.preview()
        self.prepare(p, '--policy-review', p['policy']['digest'])
        self.assertEqual(head.read_text(), 'ref: refs/heads/fixture\n')
        self.assertEqual(agents.read_text(), 'Keep current Git state.\n')
        self.assertFalse((self.project / '.specify/feature.json').exists())

    def test_feature_helper_rejects_escape_and_missing_selection(self):
        self.prepare()
        helper = self.project / '.specify/scripts/python/core.py'
        for selection, persist in ((str(self.base / 'outside-feature'), '1'), ('specs/example', '0')):
            env = dict(self.env, SPECIFY_FEATURE_DIRECTORY=selection, SPECIFY_FEATURE_NO_PERSIST=persist)
            p = subprocess.run(['python3', str(helper), 'specify', '--json'], env=env,
                               text=True, capture_output=True, timeout=10)
            self.assertNotEqual(p.returncode, 0)
        self.assertFalse((self.base / 'outside-feature').exists())
        self.assertFalse((self.project / 'specs/example').exists())


if __name__ == '__main__':
    unittest.main()
