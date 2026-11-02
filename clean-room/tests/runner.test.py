#!/usr/bin/env python3
"""Executable regressions; all fixtures, clones and evidence stay in --task-tmp."""
import argparse
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / 'bin/clean-room'


def git(*args):
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null')
    return subprocess.run(['git', *map(str, args)], env=env, check=True, capture_output=True, text=True).stdout


def write(root, name, content, executable=False):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    if executable:
        p.chmod(0o755)


def bare(root, name, files):
    repo = root / (name + '-seed')
    repo.mkdir()
    git('init', '-q', repo)
    for path, value in files.items():
        write(repo, path, value, path.startswith(('bin/', 'planning/bin/')))
    git('-C', repo, 'add', '.')
    git('-C', repo, '-c', 'user.name=cbriseno', '-c', 'user.email=4805447+cbriseno@users.noreply.github.com', 'commit', '-qm', 'Fixture')
    target = root / (name + '.git')
    git('clone', '--bare', '--depth', '1', repo.as_uri(), target)
    return target


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='self-test-', dir=TASK_TMP)
        cls.tmp = Path(cls.temp.name)
        cls.proof = []
        cls.app = bare(cls.tmp, 'app', {
            'README.md': 'Synthetic local core.\n',
            'day-attendant.py': '''import os
from pathlib import Path
p = Path(os.environ['XDG_STATE_HOME']) / 'day-attendant/profiles/default'
p.mkdir(parents=True)
(p/'profile.json').write_text('{"synthetic":true}')
''',
            'tests/test_core.py': '''import unittest
class Core(unittest.TestCase):
    def test_core(self):
        self.assertEqual(2 + 2, 4)
''',
            'scripts/test_system.py': "print('Synthetic system check passed')\n",
        })
        cls.extension = bare(cls.tmp, 'extension', {
            'README.md': 'Synthetic install and status.\n',
            'planning/bin/install-skills': '''#!/usr/bin/env python3
import os, sys
from pathlib import Path
if '--dry-run' not in sys.argv:
    for harness in ('.codex', '.claude'):
        for name in ('firstmate-planning', 'planning-firstmate-work', 'reviewing-firstmate-plan', 'executing-firstmate-unit'):
            p = Path(os.environ['HOME']) / harness / 'skills' / name
            p.mkdir(parents=True, exist_ok=True)
            (p/'SKILL.md').write_text('Synthetic fixture skill')
print('synthetic install check')
''',
            'bin/fm-extension': '''#!/usr/bin/env python3
print('{"mode":"native","synthetic":true}')
''',
        })

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
        (TASK_TMP / 'self-test-evidence.json').write_text(json.dumps({'scenarios': cls.proof, 'fixture_cleanup_complete': not cls.tmp.exists()}, indent=2) + '\n')

    def run_scenario(self, scenario, bindings=None, patterns=None, extra=None):
        case = Path(tempfile.mkdtemp(prefix='case-', dir=self.tmp))
        config = case / 'scenario.json'
        config.write_text(json.dumps(scenario))
        args = [str(CLI), 'run', str(config), '--task-tmp', str(case)]
        for name, path in (bindings or {'app': self.app}).items():
            args.extend(['--source', name + '=' + str(path)])
        if patterns:
            private = self.tmp / 'private-patterns'
            private.write_text('\n'.join(patterns))
            args.extend(['--patterns', str(private)])
        args.extend(extra or [])
        run = subprocess.run(args, capture_output=True, text=True, timeout=35)
        reports = list(case.glob('run-*.json'))
        self.assertEqual(len(reports), 1, run.stdout + run.stderr)
        report = json.loads(reports[0].read_text())
        self.assertTrue(report['cleanup_complete'], report)
        self.assertFalse(list(case.glob('sandbox-*')))
        self.assertTrue(reports[0].with_suffix('.txt').is_file())
        self.assertEqual(run.returncode, 0 if report['ok'] else 1, run.stderr)
        return report

    def scenario(self, command):
        return {'name': 'Synthetic regression', 'synthetic': True, 'level': 1, 'network': False,
                'tools': [{'name': n} for n in ('git', 'bash', 'sh', 'python3')],
                'repos': [{'id': 'app'}],
                'steps': [{'source': 'Fixture README / install', 'cwd': '/work/repos/app', 'command': command}]}

    def test_ready_scenarios_with_stand_ins(self):
        for name, binding in [('day-attendant', {'app': self.app}), ('extension', {'extension': self.extension})]:
            with self.subTest(name=name):
                scenario = json.loads((ROOT / 'clean-room/scenarios' / (name + '.json')).read_text())
                if name == 'extension':
                    # Runner transport regression; real distribution scenarios run separately.
                    scenario['steps'] = [
                        {'source': 'Synthetic fixture / install', 'cwd': '/work/repos/extension',
                         'command': 'planning/bin/install-skills install'},
                        {'source': 'Synthetic fixture / status', 'cwd': '/work/repos/extension',
                         'command': 'bin/fm-extension planning status'}]
                scenario['synthetic'] = True
                report = self.run_scenario(scenario, binding)
                self.assertTrue(report['ok'], report)
                self.assertTrue(report['synthetic'])
                self.assertEqual(len(report['clones']), 1)
                self.proof.append(report)
                print(name, 'allocated_bytes=', report['sandbox_allocated_bytes'],
                      'host_scratch_bytes=', report['host_scratch_bytes_at_exit'])

    def test_undeclared_tool_and_absolute_tool_unavailable(self):
        report = self.run_scenario(self.scenario('jq --version'))
        self.assertEqual(report['cause'], 'undeclared_dependency', report)
        self.assertEqual(report['step'], 2)
        self.assertIn('jq', report['commands'][-1]['stdout_stderr_tail'])
        report = self.run_scenario(self.scenario('/usr/bin/jq --version'))
        self.assertEqual(report['cause'], 'undeclared_dependency')
        report = self.run_scenario(self.scenario("python3 -c 'import subprocess; subprocess.run([\"uv\"])'"))
        self.assertEqual(report['cause'], 'undeclared_dependency', report)

    def test_readme_step_located(self):
        scenario = self.scenario('exit 19')
        scenario['steps'].append({'source': 'Fixture README / must not run', 'command': 'touch /work/should-not-exist'})
        report = self.run_scenario(scenario)
        self.assertEqual(report['step'], 2)
        self.assertEqual(report['source'], 'Fixture README / install')
        self.assertEqual(report['commands'][-1]['exit_code'], 19)
        self.assertEqual(report['commands'][-1]['command'], 'exit 19')

    def test_output_and_filesystem_privacy(self):
        for command in ("printf '%s' '/fixtures/private-marker/private'", "python3 -c 'from pathlib import Path; (Path.home()/\"leak\").write_text(\"/fixtures/private-marker/private\")'"):
            with self.subTest(command=command):
                report = self.run_scenario(self.scenario(command), patterns=['/fixtures/private-marker'])
                self.assertEqual(report['cause'], 'private_reference', report)
                self.assertNotIn('/fixtures/private-marker', json.dumps(report))

    def test_isolation_network_home_pid_readonly(self):
        probe = '''python3 - <<'CHECK'
import os, socket
from pathlib import Path
assert os.environ['HOME'] == '/sandbox/guest'
assert not Path('/sandbox/guest/.codex').exists()
assert not Path('/root').exists()
assert not Path('/run/user').exists()
assert not Path('/usr/bin/jq').exists()
assert Path('/proc/1/status').exists()
assert len(list(Path('/proc').glob('[0-9]*'))) < 10
try:
    Path('/usr/share/clean-room-write-probe').write_text('bad')
except OSError:
    pass
else:
    raise AssertionError('host system writable')
s = socket.socket()
s.settimeout(0.2)
assert s.connect_ex(('192.0.2.1', 443)) != 0
assert 'SECRET_SENTINEL' not in os.environ
CHECK'''
        previous = os.environ.get('SECRET_SENTINEL')
        os.environ['SECRET_SENTINEL'] = 'must not inherit'
        try:
            report = self.run_scenario(self.scenario(probe))
        finally:
            if previous is None:
                os.environ.pop('SECRET_SENTINEL')
            else:
                os.environ['SECRET_SENTINEL'] = previous
        self.assertTrue(report['ok'], report)

    def test_missing_declared_tool_and_file(self):
        scenario = self.scenario('true')
        scenario['tools'].append({'name': 'clean-room-missing-tool'})
        report = self.run_scenario(scenario)
        self.assertEqual(report['cause'], 'missing_declared_dependency')
        scenario = self.scenario(':')
        scenario['steps'][0]['files'] = ['/work/not-created']
        report = self.run_scenario(scenario)
        self.assertEqual(report['cause'], 'missing_file')

    def test_timeout_and_expected_nonzero(self):
        scenario = self.scenario("python3 -c 'import time; time.sleep(5)'")
        scenario['steps'][0]['timeout'] = 1
        report = self.run_scenario(scenario)
        self.assertEqual(report['cause'], 'timeout', report)
        scenario = self.scenario('exit 3')
        scenario['steps'][0]['expect_exit'] = 3
        self.assertTrue(self.run_scenario(scenario)['ok'])

    def test_checks_versions_and_presence_only_binaries(self):
        scenario = self.scenario(':')
        scenario['steps'][0]['check'] = 'exit 8'
        report = self.run_scenario(scenario)
        self.assertEqual(report['stage'], 'check')
        self.assertEqual(report['step'], 2)
        scenario = self.scenario(':')
        scenario['tools'][-1].update(version_args=['--version'], expect_version='impossible-version')
        self.assertEqual(self.run_scenario(scenario)['cause'], 'version_mismatch')
        fake = self.tmp / 'vendor-stand-in'
        fake.write_text('#!/tools/bash\nprintf BAD-VENDOR-EXECUTED\n')
        fake.chmod(0o755)
        scenario = self.scenario('codex --version')
        scenario['level'] = 2
        scenario['tools'].append({'name': 'codex', 'expect_version': 'fixture-1'})
        report = self.run_scenario(scenario, extra=['--tool', 'codex=' + str(fake)])
        self.assertFalse(report['ok'])
        self.assertNotIn('BAD-VENDOR-EXECUTED', json.dumps(report))
        self.assertEqual(report['binaries'][0]['mode'], 'read-only-presence')

    def test_termination_cleans_namespace_and_reports(self):
        case = Path(tempfile.mkdtemp(prefix='terminated-', dir=self.tmp))
        config = case / 'scenario.json'
        scenario = self.scenario("python3 -c 'from pathlib import Path; import time; Path(\"/work/started\").touch(); time.sleep(20)'")
        config.write_text(json.dumps(scenario))
        process = subprocess.Popen([str(CLI), 'run', str(config), '--task-tmp', str(case), '--source', 'app=' + str(self.app)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            until = time.monotonic() + 10
            while not list(case.glob('sandbox-*/work/started')) and process.poll() is None and time.monotonic() < until:
                time.sleep(0.05)
            self.assertTrue(list(case.glob('sandbox-*/work/started')))
            process.terminate()
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 1, stdout + stderr)
            report = json.loads(next(case.glob('run-*.json')).read_text())
            self.assertEqual(report['cause'], 'timeout_or_interrupted')
            self.assertTrue(report['cleanup_complete'])
            self.assertFalse(list(case.glob('sandbox-*')))
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_entrypoint_from_renamed_distribution(self):
        renamed = self.tmp / 'renamed-distribution'
        (renamed / 'bin').mkdir(parents=True)
        (renamed / 'clean-room').mkdir()
        shutil.copyfile(CLI, renamed / 'bin/clean-room')
        (renamed / 'bin/clean-room').chmod(0o755)
        shutil.copyfile(ROOT / 'clean-room/runner.py', renamed / 'clean-room/runner.py')
        run = subprocess.run([str(renamed / 'bin/clean-room'), 'run', '--help'], cwd=self.tmp,
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('--task-tmp', run.stdout)

    def test_busy_lock_and_level_three_refused(self):
        import fcntl
        case = Path(tempfile.mkdtemp(prefix='locked-', dir=self.tmp))
        config = case / 'scenario.json'
        config.write_text(json.dumps(self.scenario(':')))
        with (case / '.clean-room.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run = subprocess.run([str(CLI), 'run', str(config), '--task-tmp', str(case)], capture_output=True, text=True)
        report = json.loads(next(case.glob('run-*.json')).read_text())
        self.assertEqual(run.returncode, 1)
        self.assertEqual(report['cause'], 'busy')
        scenario = self.scenario(':')
        scenario['level'] = 3
        self.assertEqual(self.run_scenario(scenario)['cause'], 'configuration')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--task-tmp', required=True)
    args, remaining = parser.parse_known_args()
    TASK_TMP = Path(args.task_tmp).resolve(strict=True)
    unittest.main(argv=[sys.argv[0], *remaining])
