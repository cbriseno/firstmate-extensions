"""Public CLI regressions in disposable homes; no native sessions or installation."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT/'bin/fm-extension'


class SettingsContract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='extension-settings-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base/'home'
        self.project = self.base/'project'
        (self.home/'config').mkdir(parents=True)
        self.project.mkdir()
        self.env = dict(os.environ, HOME=str(self.base), XDG_CONFIG_HOME=str(self.base/'xdg'),
                        XDG_STATE_HOME=str(self.base/'state'), CODEX_HOME=str(self.base/'codex'),
                        FM_HOME=str(self.home), FM_LIVE='0', PYTHONDONTWRITEBYTECODE='1')
        self.serial = 0

    def invoke(self, *args, code=0, scoped=True):
        command = [str(CLI), *map(str, args), '--home', str(self.home)]
        if scoped:
            command += ['--project', str(self.project), '--task-id', 'job']
        p = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, code, p.stdout+p.stderr)
        return json.loads(p.stdout)

    def status(self, **kwargs):
        return self.invoke('settings', 'status', **kwargs)

    def change(self, *args, revision=None, request=None, code=0, scoped=True):
        self.serial += 1
        return self.invoke('settings', *args, '--request-id', request or f'owner-{self.serial}',
                           '--expected-revision', revision or self.status(scoped=scoped)['revision'],
                           code=code, scoped=scoped)

    def tree(self):
        return {str(p.relative_to(self.base)): p.read_bytes() for p in self.base.rglob('*') if p.is_file()}

    def test_reads_are_side_effect_free(self):
        before = self.tree()
        a, b = self.status(), self.status()
        self.assertEqual(a, b)
        self.assertEqual(before, self.tree())
        self.assertEqual(set(a['planning']['scopes']), {'installation', 'home', 'project', 'task'})
        for row in a['planning']['scopes'].values():
            self.assertIsNotNone(row['identity'])
            self.assertEqual(row['mode'], 'inherit')
        self.assertIsNone(self.status(scoped=False)['planning']['scopes']['task']['mode'])

    def test_all_scopes_read_write_clear_and_resolve(self):
        for scope in ('installation', 'home', 'project', 'task'):
            result = self.change('set', '--target', 'planning', '--scope', scope, '--mode', 'speckit')
            self.assertEqual(result['state'], 'applied')
            shown = self.status()
            self.assertEqual(shown['revision'], result['revision'])
            self.assertEqual(shown['planning']['scopes'][scope]['mode'], 'speckit')
        resolved = self.invoke('planning', 'resolve')
        self.assertEqual((resolved['mode'], resolved['origin']), ('speckit', 'task'))
        for scope in ('task', 'project', 'home', 'installation'):
            self.change('set', '--target', 'planning', '--scope', scope, '--mode', 'inherit')
        self.assertEqual(self.invoke('planning', 'resolve')['mode'], 'native')

    def test_legacy_task_surface_and_frozen_brief(self):
        self.invoke('planning', 'set', '--scope', 'task', '--mode', 'speckit')
        result = self.invoke('planning', 'status')
        self.assertEqual(result['task_preference']['mode'], 'speckit')
        brief = self.base/'brief'
        brief.write_text('planning_mode: native\n')
        self.assertEqual(self.invoke('planning', 'resolve', '--brief', brief)['mode'], 'native')
        self.invoke('planning', 'clear', '--scope', 'task')
        self.assertEqual(self.invoke('planning', 'resolve')['mode'], 'native')

    def test_task_identity_cannot_cross_project_or_home(self):
        self.change('set', '--target', 'planning', '--scope', 'task', '--mode', 'speckit')
        other = self.base/'other'
        other.mkdir()
        p = subprocess.run([str(CLI), 'settings', 'status', '--home', str(self.home),
                            '--project', str(other), '--task-id', 'job'], env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn('different or replaced project', p.stdout)
        (other/'config').mkdir()
        p = subprocess.run([str(CLI), 'settings', 'status', '--home', str(other),
                            '--project', str(self.project), '--task-id', 'job'], env=self.env, capture_output=True, text=True)
        self.assertEqual(json.loads(p.stdout)['planning']['scopes']['task']['mode'], 'inherit')

    def test_stale_revision_and_idempotent_applied_and_rejected_receipts(self):
        old = self.status()['revision']
        args = ('set', '--target', 'planning', '--scope', 'task', '--mode', 'speckit')
        first = self.change(*args, request='first', revision=old)
        before = self.tree()
        self.assertEqual(first, self.change(*args, request='first', revision=old))
        self.assertEqual(before, self.tree())
        stale = self.change(*args, request='stale', revision=old, code=4)
        self.assertEqual(stale['code'], 'stale_revision')
        self.assertEqual(stale['revision'], first['revision'])
        self.assertEqual(stale, self.change(*args, request='stale', revision=old, code=4))
        conflict = self.change(*args, request='first', revision=first['revision'], code=1)
        self.assertEqual(conflict['code'], 'request_id_conflict')

    def test_concurrent_writers_only_apply_one_revision(self):
        old = self.status()['revision']
        def call(n):
            return subprocess.run([str(CLI), 'settings', 'set', '--home', str(self.home),
                '--target', 'planning', '--scope', 'home', '--mode', 'native', '--request-id', str(n),
                '--expected-revision', old], env=self.env, capture_output=True, text=True, timeout=15)
        # This selector omits project/task, so obtain its matching revision.
        old = self.status(scoped=False)['revision']
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(call, (1, 2)))
        self.assertEqual(sum(p.returncode == 0 for p in results), 1)
        rejected = next(p for p in results if p.returncode != 0)
        self.assertIn(json.loads(rejected.stdout)['code'], ('busy', 'stale_revision'))
        if rejected.returncode == 1:
            # The planning lock is fail-fast; retrying the busy writer then
            # observes the now-stale revision without applying a second write.
            result = call(1 if results[0].returncode else 2)
            self.assertEqual(result.returncode, 4, result.stdout+result.stderr)

    def test_legacy_change_invalidates_revision(self):
        old = self.status()['revision']
        self.invoke('planning', 'set', '--scope', 'task', '--mode', 'speckit')
        rejected = self.change('set', '--target', 'planning', '--scope', 'task', '--mode', 'native', revision=old, code=4)
        self.assertEqual(rejected['code'], 'stale_revision')


    def test_planning_undo_and_intervening_write(self):
        first = self.change('set', '--target', 'planning', '--scope', 'project', '--mode', 'speckit')
        self.change('undo', '--undo-token', first['undo_token'])
        self.assertEqual(self.status()['planning']['scopes']['project']['mode'], 'inherit')
        first = self.change('set', '--target', 'planning', '--scope', 'home', '--mode', 'speckit')
        self.change('set', '--target', 'planning', '--scope', 'installation', '--mode', 'native')
        self.assertEqual(self.change('undo', '--undo-token', first['undo_token'], code=1)['code'], 'undo_unavailable')


    def test_interrupted_receipt_is_never_reexecuted(self):
        revision = self.status()['revision']
        args = ('set', '--target', 'planning', '--scope', 'task', '--mode', 'speckit')
        applied = self.change(*args, revision=revision, request='interrupted')
        path = self.base/'xdg/fm-planning/settings-contract.json'
        stored = json.loads(path.read_text())
        row = stored['requests']['interrupted']['receipt']
        row.update(state='rejected', code='operation_incomplete', undo_token=None)
        path.write_text(json.dumps(stored))
        before = self.tree()
        result = self.change(*args, revision=revision, request='interrupted', code=1)
        self.assertEqual(result['code'], 'operation_incomplete')
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.status()['revision'], applied['revision'])


if __name__ == '__main__':
    unittest.main()
