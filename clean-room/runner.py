"""Host orchestration and private namespace worker; Python standard library only."""
import argparse
import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import uuid

MIN_FREE = 3 * 1024**3
RESERVED = {'codex', 'claude', 'herdr'}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def pairs(values):
    result = {}
    for value in values:
        key, sep, path = value.partition('=')
        if not sep or not re.fullmatch(r'[a-zA-Z0-9_-]+', key):
            raise ValueError('bindings require NAME=PATH')
        result[key] = Path(path).resolve(strict=True)
    return result


def redact(text, patterns):
    for pattern in patterns:
        text = re.sub(pattern['regex'], '<private:' + pattern['label'] + '>', text)
    return text


def sanitize(value, patterns):
    if isinstance(value, str):
        return redact(value, patterns)
    if isinstance(value, list):
        return [sanitize(item, patterns) for item in value]
    if isinstance(value, dict):
        return {key: sanitize(item, patterns) for key, item in value.items()}
    return value


def hits(data, patterns):
    text = data.decode('utf-8', errors='replace') if isinstance(data, bytes) else data
    return sorted({p['label'] for p in patterns if re.search(p['regex'], text)})


def scan(roots, patterns):
    """Bound memory, follow no links, include names and link targets."""
    found = []
    allocated = logical = 0
    for root in roots:
        for folder, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                p = Path(folder) / name
                info = p.lstat()
                allocated += info.st_blocks * 512
                logical += info.st_size
                labels = hits(str(p), patterns)
                if p.is_symlink():
                    labels += hits(os.readlink(p), patterns)
                elif stat.S_ISREG(info.st_mode):
                    with p.open('rb') as stream:
                        overlap = b''
                        while chunk := stream.read(65536):
                            labels += hits(overlap + chunk, patterns)
                            overlap = chunk[-8192:]
                if labels and len(found) < 50:
                    found.append({'path': redact(str(p), patterns), 'patterns': sorted(set(labels))})
    return found[:50], allocated, logical


def classify(text, declared):
    missing = re.search(r'(?:^|\n).*?([\w.+-]+): (?:command )?not found', text)
    if not missing:
        missing = re.search(r"No such file or directory: ['\"]([\w.+-]+)['\"]", text)
    absolute = re.search(r'(/(?:usr/bin|bin|tools)/[\w.+-]+): No such file or directory', text)
    if absolute:
        return 'missing_declared_dependency' if Path(absolute[1]).name in declared else 'undeclared_dependency'
    if missing:
        return ('missing_declared_dependency' if missing[1] in declared else 'undeclared_dependency')
    if 'No such file or directory' in text or "can't open file" in text:
        return 'wrong_path' if re.search(r'/(?:home|work|usr|opt)/', text) else 'missing_file'
    if 'Network is unreachable' in text or 'Could not resolve' in text:
        return 'network_denied'
    return 'test_failure'


def execute(command, cwd, expected, timeout, report, patterns, declared):
    log = Path('/out/step-output')
    timed_out = False
    with log.open('wb') as output:
        process = subprocess.Popen(['/tools/bash', '--noprofile', '--norc', '-c', command],
                                   cwd=cwd, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            code = process.wait()
    labels = set()
    with log.open('rb') as output:
        overlap = b''
        while chunk := output.read(65536):
            labels.update(hits(overlap + chunk, patterns))
            overlap = chunk[-8192:]
        output.seek(max(0, output.tell() - 8192))
        tail = output.read().decode('utf-8', errors='replace')
    log.unlink()
    result = {'command': redact(command, patterns), 'exit_code': code, 'expected_exit': expected,
              'stdout_stderr_tail': redact(tail, patterns)}
    if labels:
        result.update(cause='private_reference', patterns=sorted(labels))
    elif timed_out:
        result['cause'] = 'timeout'
    elif code != expected:
        result['cause'] = classify(tail, declared)
    report['commands'].append(result)
    return result


def worker():
    control = json.loads(Path('/control/input.json').read_text())
    scenario, patterns = control['scenario'], control['patterns']
    report = {'commands': [], 'ok': False, 'level': scenario.get('level', 1),
              'network': scenario.get('network', False), 'synthetic': scenario.get('synthetic', False)}
    declared = {t['name'] for t in scenario['tools']}
    try:
        for path in ('.config', '.cache', '.local/state', '.local/share', '.run'):
            (Path('/sandbox/guest') / path).mkdir(parents=True, exist_ok=True)
        Path('/work/repos').mkdir()
        for tool in scenario['tools']:
            if tool['name'] in RESERVED:
                # Presence only: vendor processes, including --version, stay outside this tool.
                report.setdefault('binaries', []).append({'name': tool['name'],
                    'expected_version': tool['expect_version'], 'verified': False, 'mode': 'read-only-presence'})
                continue
            if 'version_args' in tool:
                command = shlex.join([tool['name'], *tool['version_args']])
                result = execute(command, '/work', 0, 10, report, patterns, declared)
                if 'cause' in result:
                    report.update(cause=result['cause'], stage='version')
                    return
                if not re.search(tool.get('expect_version', '.*'), result['stdout_stderr_tail']):
                    report.update(cause='version_mismatch', stage='version')
                    return
        steps = []
        for repo in scenario['repos']:
            source = 'file:///sources/' + repo['id'] if repo['id'] in control['sources'] else repo['url']
            cmd = shlex.join(['git', 'clone', '--depth', '1', '--no-local', '--', source, '/work/repos/' + repo['id']])
            steps.append({'command': cmd, 'cwd': '/work', 'source': 'clone', 'expect_exit': 0})
        steps.extend(scenario['steps'])
        for number, step in enumerate(steps, 1):
            report['step'] = number
            report['source'] = step.get('source', '')
            cwd = step.get('cwd', '/work')
            result = execute(step['command'], cwd, step.get('expect_exit', 0), step.get('timeout', 60), report, patterns, declared)
            if 'cause' in result:
                report['cause'] = result['cause']
                return
            if step.get('source') == 'clone':
                repo_path = '/work/repos/' + scenario['repos'][number - 1]['id']
                revision = subprocess.run(['/tools/git', '-C', repo_path, 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
                report.setdefault('clones', []).append({'id': scenario['repos'][number - 1]['id'], 'revision': revision, 'depth': 1})
            for expected_file in step.get('files', []):
                p = Path(os.path.expandvars(expected_file))
                if not p.is_absolute():
                    p = Path(cwd) / p
                if not p.exists():
                    report.update(cause='missing_file', missing_file=redact(str(p), patterns))
                    return
            if step.get('check'):
                check = execute(step['check'], cwd, 0, step.get('timeout', 60), report, patterns, declared)
                if 'cause' in check:
                    report.update(cause=check['cause'], stage='check')
                    return
            leaks, allocated, logical = scan(['/sandbox/guest', '/work', '/tmp'], patterns)
            report['sandbox_allocated_bytes'] = max(report.get('sandbox_allocated_bytes', 0), allocated)
            report['sandbox_logical_bytes'] = max(report.get('sandbox_logical_bytes', 0), logical)
            if leaks:
                report.update(cause='private_reference', leaks=leaks)
                return
        report['ok'] = True
    except Exception as exc:
        report.update(cause='runner_error', error=redact(str(exc), patterns))
    finally:
        # Scan also after a failed command, including the tmpfs HOME before it disappears.
        leaks, allocated, logical = scan(['/sandbox/guest', '/work', '/tmp'], patterns)
        report['sandbox_allocated_bytes'] = max(report.get('sandbox_allocated_bytes', 0), allocated)
        report['sandbox_logical_bytes'] = max(report.get('sandbox_logical_bytes', 0), logical)
        if leaks:
            report.update(ok=False, cause='private_reference', leaks=leaks)
        dump(Path('/out/result.json'), report)


def validate(scenario):
    if scenario.get('level', 1) not in (1, 2):
        raise ValueError('only levels 1 and 2 are supported; live model flows use separate authorized labs')
    if not isinstance(scenario.get('network', False), bool):
        raise ValueError('network must be boolean')
    for group in ('tools', 'repos', 'steps'):
        if not isinstance(scenario.get(group), list):
            raise ValueError(group + ' must be a list')
    names = []
    for tool in scenario['tools']:
        name = tool['name']
        if not re.fullmatch(r'[a-zA-Z0-9_.+-]+', name):
            raise ValueError('invalid tool name')
        if name in RESERVED and (scenario.get('level', 1) != 2 or not tool.get('expect_version')):
            raise ValueError('harness presence requires level 2 and an expected version')
        names.append(name)
    if not {'bash', 'sh', 'git'}.issubset(names) or len(names) != len(set(names)):
        raise ValueError('declare bash, sh and git exactly once; duplicate tools are forbidden')
    repo_ids = [repo['id'] for repo in scenario['repos']]
    if len(repo_ids) != len(set(repo_ids)):
        raise ValueError('duplicate repo ids are forbidden')
    for repo in scenario['repos']:
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', repo['id']):
            raise ValueError('invalid repo id')
        url = repo.get('url', '')
        if url and (not scenario.get('network') or not url.startswith('https://') or '@' in url):
            raise ValueError('public sources require network=true and credential-free https URLs')
    for step in scenario['steps']:
        if not isinstance(step['command'], str) or not step.get('source'):
            raise ValueError('each step requires command and documentation source')
        if not Path(step.get('cwd', '/work')).is_absolute():
            raise ValueError('cwd must be an absolute sandbox path')
        if not 0 < step.get('timeout', 60) <= 900:
            raise ValueError('step timeout must be 1..900 seconds')


def run(args):
    root = Path(args.task_tmp).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('task tmp must be an existing directory')
    ident = 'run-' + uuid.uuid4().hex[:12]
    report_path = root / (ident + '.json')
    report = {'ok': False, 'cleanup_complete': True, 'network': False, 'commands': []}
    patterns = [{'label': 'real-home', 'regex': re.escape(str(Path.home()))},
                {'label': 'real-user', 'regex': r'\b' + re.escape(getpass.getuser()) + r'\b'}]
    sandbox = None
    lock = None
    try:
        scenario_path = Path(args.scenario).resolve(strict=True)
        scenario = json.loads(scenario_path.read_text())
        report.update(network=scenario.get('network', False), scenario=scenario.get('name', scenario_path.stem))
        validate(scenario)
        if args.patterns:
            private_file = Path(args.patterns).resolve(strict=True)
            # A private patterns file must not be inside this distribution or a supplied source.
            forbidden = [Path(__file__).resolve().parents[1], *pairs(args.source).values()]
            if any(private_file.is_relative_to(p) for p in forbidden):
                raise ValueError('patterns file must live outside repositories')
            for number, literal in enumerate(private_file.read_text().splitlines(), 1):
                if len(literal.encode()) > 4096:
                    raise ValueError('private patterns must be at most 4096 bytes each')
                if literal.strip():
                    patterns.append({'label': 'private-' + str(number), 'regex': re.escape(literal)})
        report['free_bytes_before'] = shutil.disk_usage(root).free
        if report['free_bytes_before'] < MIN_FREE:
            report['cause'] = 'disk_floor'
            raise ValueError('at least 3 GiB free is required')
        lock = (root / '.clean-room.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            report['cause'] = 'busy'
            raise ValueError('one sandbox at a time per task tmp')
        sandbox = Path(tempfile.mkdtemp(prefix='sandbox-', dir=root))
        for name in ('work', 'out', 'control', 'tools'):
            (sandbox / name).mkdir()
        sources = pairs(args.source)
        binaries = pairs(args.tool)
        runtimes = pairs(args.runtime)
        mounts = []
        for repo in scenario['repos']:
            name = repo['id']
            if name in sources:
                bare = subprocess.run(['/usr/bin/git', '--git-dir', str(sources[name]), 'rev-parse', '--is-bare-repository'],
                                      env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
                                      capture_output=True, text=True)
                if bare.returncode or bare.stdout.strip() != 'true':
                    raise ValueError('source ' + name + ' must be a local bare repository')
                mounts += ['--ro-bind', str(sources[name]), '/sources/' + name]
            elif not repo.get('url'):
                raise ValueError('missing local source binding: ' + name)
        for tool in scenario['tools']:
            name = tool['name']
            path = binaries.get(name) or shutil.which(name)
            if not path:
                report.update(cause='missing_declared_dependency', dependency=name)
                raise ValueError('declared dependency unavailable: ' + name)
            path = Path(path).resolve(strict=True)
            destination = '/opt/binaries/' + name
            mounts += ['--ro-bind', str(path), destination]
            if name in RESERVED:
                # A presence marker never executes the vendor; no accidental inference/lifecycle calls.
                marker = sandbox / 'control' / name
                marker.write_text('#!/tools/bash\nprintf "%s\\n" "clean-room: presence only; use an authorized live lab" >&2\nexit 126\n')
                marker.chmod(0o755)
                mounts += ['--ro-bind', str(marker), '/opt/guards/' + name]
                destination = '/opt/guards/' + name
            (sandbox / 'tools' / name).symlink_to(destination)
        for name, path in runtimes.items():
            mounts += ['--ro-bind', str(path), '/opt/runtime/' + name]
        dump(sandbox / 'control/input.json', {'scenario': scenario, 'patterns': patterns, 'sources': list(sources)})
        cmd = [shutil.which('bwrap') or '/usr/bin/bwrap', '--die-with-parent', '--new-session', '--unshare-all']
        if scenario.get('network'):
            cmd += ['--share-net']
        for directory in ('/usr/lib', '/usr/share'):
            cmd += ['--ro-bind', directory, directory]
        for directory in ('/lib', '/lib64'):
            if Path(directory).exists():
                cmd += ['--ro-bind', directory, directory]
        cmd += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/sandbox', '--dir', '/sandbox/guest',
                '--tmpfs', '/tmp', '--tmpfs', '/run', '--dir', '/etc',
                '--ro-bind', str(sandbox / 'tools'), '/tools', '--ro-bind', str(sandbox / 'tools'), '/usr/bin',
                '--symlink', 'usr/bin', '/bin', '--ro-bind', str(sandbox / 'control'), '/control',
                '--bind', str(sandbox / 'work'), '/work', '--bind', str(sandbox / 'out'), '/out',
                '--ro-bind', '/usr/bin/python3', '/bootstrap/python3',
                '--ro-bind', str(Path(__file__).resolve()), '/bootstrap/runner.py']
        if Path('/etc/ld.so.cache').exists():
            cmd += ['--ro-bind', '/etc/ld.so.cache', '/etc/ld.so.cache']
        if scenario.get('network'):
            for name in ('resolv.conf', 'hosts', 'nsswitch.conf', 'ssl/certs'):
                if Path('/etc', name).exists():
                    cmd += ['--ro-bind', '/etc/' + name, '/etc/' + name]
        cmd += mounts + ['--chdir', '/work', '--clearenv']
        env = {'HOME': '/sandbox/guest', 'USER': 'guest', 'LOGNAME': 'guest', 'PATH': '/tools',
               'XDG_CONFIG_HOME': '/sandbox/guest/.config', 'XDG_CACHE_HOME': '/sandbox/guest/.cache',
               'XDG_STATE_HOME': '/sandbox/guest/.local/state', 'XDG_DATA_HOME': '/sandbox/guest/.local/share',
               'XDG_RUNTIME_DIR': '/sandbox/guest/.run', 'TMPDIR': '/tmp', 'LANG': 'C.UTF-8',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0',
               'GIT_EXEC_PATH': '/usr/lib/git-core',
               'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'FM_LIVE': '0'}
        for key, value in env.items():
            cmd += ['--setenv', key, value]
        cmd += ['/bootstrap/python3', '-B', '/bootstrap/runner.py', '--worker']
        with (sandbox / 'outer.log').open('wb') as output:
            proc = subprocess.Popen(cmd, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                rc = proc.wait(timeout=args.timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt, InterruptedError):
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                report['cause'] = 'timeout_or_interrupted'
                rc = -1
        result = sandbox / 'out/result.json'
        if result.exists():
            report.update(json.loads(result.read_text()))
        else:
            report.setdefault('cause', 'sandbox_unavailable')
            report['stdout_stderr_tail'] = redact((sandbox / 'outer.log').read_text(errors='replace')[-8192:], patterns)
        report['sandbox_exit'] = rc
        report['platform_limit'] = 'host Linux kernel and Debian userland; no cross-platform certification'
        report['herdr_lifecycle'] = 'not exercised; requires the separately authorized isolated lab helper'
        report['ok'] = report['ok'] and rc == 0
    except (Exception, KeyboardInterrupt) as exc:
        report.setdefault('cause', 'configuration')
        report['error'] = redact(str(exc), patterns)
    finally:
        if sandbox:
            report['host_scratch_bytes_at_exit'] = sum(p.lstat().st_blocks * 512 for p in sandbox.rglob('*'))
            try:
                shutil.rmtree(sandbox)
                report['cleanup_complete'] = not sandbox.exists()
            except OSError as exc:
                report.update(cleanup_complete=False, cleanup_error=redact(str(exc), patterns))
        report['ok'] = report['ok'] and report['cleanup_complete']
        report = sanitize(report, patterns)
        dump(report_path, report)
        summary = ('OK' if report['ok'] else 'FAIL') + ': ' + str(report.get('scenario', 'configuration'))
        summary += '; step ' + str(report.get('step', '-')) + '; cause ' + report.get('cause', 'none')
        summary += '; sandbox ' + str(report.get('sandbox_allocated_bytes', 0)) + ' bytes'
        summary += '; cleanup ' + ('complete' if report['cleanup_complete'] else 'FAILED') + '\n'
        report_path.with_suffix('.txt').write_text(summary)
        if lock:
            lock.close()
        print(summary, end='')
        print('Report: ' + str(report_path))
    return 0 if report['ok'] else 1


def interrupted(signum, frame):
    raise InterruptedError('received signal ' + str(signum))


def main():
    if sys.argv[1:] == ['--worker']:
        worker()
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    command = subs.add_parser('run', help='run one scenario; reports remain under task tmp')
    command.add_argument('scenario')
    command.add_argument('--task-tmp', required=True, help='existing private task scratch directory')
    command.add_argument('--source', action='append', default=[], metavar='ID=BARE_REPO')
    command.add_argument('--tool', action='append', default=[], metavar='NAME=EXECUTABLE')
    command.add_argument('--runtime', action='append', default=[], metavar='NAME=DIRECTORY', help='read-only runtime tree at /opt/runtime/NAME')
    command.add_argument('--patterns', help='private literal patterns, one per line; outside repositories')
    command.add_argument('--timeout', type=int, default=300, help='entire namespace deadline in seconds')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    return run(args)


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    sys.exit(main())
