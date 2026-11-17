#!/usr/bin/env python3
"""relay-check threshold for Claude and Codex transcripts; relay pane steps with a fake herdr."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

here = Path(__file__).resolve().parents[1]
claude_row = {'type': 'assistant', 'message': {'usage': {
    'input_tokens': 10, 'cache_creation_input_tokens': 100, 'cache_read_input_tokens': 290000}}}
codex_row = {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
    'last_token_usage': {'input_tokens': 290110}}}}
with tempfile.TemporaryDirectory(prefix='relay-test-') as temporary:
    home = Path(temporary)
    (home / 'config').mkdir()
    env = {key: value for key, value in os.environ.items() if key != 'FM_TASK_ID'}

    def check(row, threshold, **extra):
        (home / 'config/relay-lite.json').write_text(json.dumps({'threshold_tokens': threshold}))
        transcript = home / 'session.jsonl'
        transcript.write_text('{"type":"user"}\n' + json.dumps(row) + '\n')
        payload = {'transcript_path': str(transcript), 'cwd': str(home), 'stop_hook_active': False, **extra}
        return subprocess.run([sys.executable, str(here / 'relay-check'), str(home)], input=json.dumps(payload),
                              env=env, capture_output=True, text=True, timeout=10)

    for row in (claude_row, codex_row):
        result = check(row, 290000)
        assert result.returncode == 2 and 'relay now' in result.stderr, result
        assert check(row, 300000).returncode == 0, 'below threshold'
        assert check(row, 0).returncode == 0, 'threshold 0 is off'
        assert check(row, 290000, stop_hook_active=True).returncode == 0, 'never block twice in a row'
        assert check(row, 290000, cwd='/').returncode == 0, 'only the primary in its home'

    # Exercise each independent axis through the hook, with no inferred window.
    transcript = home / 'session.jsonl'
    config = home / 'config/relay-lite.json'
    def matrix_check(role, row, settings, prefix=(), **extra):
        config.write_text(json.dumps({'thresholds': settings}))
        transcript.write_text(''.join(json.dumps(item) + '\n' for item in (*prefix, row)))
        payload = {'transcript_path': str(transcript), 'cwd': str(home), **extra}
        test_env = env.copy()
        if role == 'worker':
            test_env['FM_TASK_ID'] = 'example'
        return subprocess.run([sys.executable, str(here / 'relay-check'), str(home)],
                              input=json.dumps(payload), env=test_env, capture_output=True, text=True, timeout=10)

    rows = {'codex': {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
        'last_token_usage': {'input_tokens': 75}, 'model_context_window': 100}}},
        'claude': {'type': 'assistant', 'message': {'model': 'claude-test', 'usage': {'input_tokens': 75}}}}
    for role in ('primary', 'worker'):
        for harness in ('codex', 'claude'):
            for axis in ('tokens', 'percent'):
                settings = {r: {h: {'tokens': 0, 'percent': 0} for h in rows} for r in ('primary', 'worker')}
                settings[role][harness][axis] = 70
                result = matrix_check(role, rows[harness], settings)
                if harness == 'claude' and axis == 'percent':
                    assert result.returncode == 0, result.stderr  # no window in transcript
                    result = matrix_check(role, rows[harness], settings,
                                          context_window={'model': 'claude-test', 'tokens': 100})
                    assert matrix_check(role, rows[harness], settings,
                                        context_window={'model': 'other', 'tokens': 100}).returncode == 0
                assert result.returncode == 2, (role, harness, axis, result.stderr)
                other = 'worker' if role == 'primary' else 'primary'
                assert matrix_check(other, rows[harness], settings).returncode == 0
    settings = {'primary': {'codex': {'percent': 70, 'tokens': 0}}}
    # Native statusline evidence binds a measured window to this transcript/model.
    native = {'session_id': 'real-session', 'transcript_path': str(transcript),
              'model': {'id': 'claude-test'},
              'context_window': {'context_window_size': 100, 'used_percentage': 75}}
    limits = {'worker': {'claude': {'tokens': 0, 'percent': 0,
              'models': {'claude-test': {'tokens': 70}, 'different': {'tokens': 90}}}}}
    assert matrix_check('worker', rows['claude'], limits, statusline=native).returncode == 2
    limits['worker']['claude']['models']['claude-test'] = {'percent': 70}
    assert matrix_check('worker', rows['claude'], limits, statusline=native).returncode == 2
    for altered in ({'model': {'id': 'other'}}, {'transcript_path': '/other'}, {'session_id': None}):
        assert matrix_check('worker', rows['claude'], limits,
                            statusline={**native, **altered}).returncode == 0
    unknown = {'type': 'assistant', 'message': {'model': 'uncalibrated', 'usage': {'input_tokens': 75}}}
    assert matrix_check('worker', unknown, limits, statusline=native).returncode == 0
    assert matrix_check('primary', rows['claude'], limits, statusline=native).returncode == 0
    absent = {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
        'last_token_usage': {'input_tokens': 75}}}}
    assert matrix_check('primary', absent, settings).returncode == 0
    changed = {'type': 'turn_context', 'payload': {'model': 'new'}}
    # A model change after a measurement invalidates both the usage and window.
    transcript.write_text('\n'.join(map(json.dumps, [
        {'type': 'turn_context', 'payload': {'model': 'old'}}, rows['codex'], changed])) + '\n')
    config.write_text(json.dumps({'thresholds': settings}))
    result = subprocess.run([sys.executable, str(here / 'relay-check'), str(home)],
                            input=json.dumps({'transcript_path': str(transcript), 'cwd': str(home)}),
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    # A tail may omit the old model record; do not reuse that unbound window.
    transcript.write_text('\n'.join(map(json.dumps, [rows['codex'], changed])) + '\n')
    result = subprocess.run([sys.executable, str(here / 'relay-check'), str(home)],
                            input=json.dumps({'transcript_path': str(transcript), 'cwd': str(home)}),
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr

    # Migration preserves current activation, archives legacy data and is repeatable.
    legacy_json = {'relay_policy': {'claude': {'compactions': 2, 'min_context_percent': 80}}}
    legacy_text = 'codex: compactaciones:4\nclaude: umbral:400000\n'
    config.write_text(json.dumps({'threshold_tokens': 250000, 'enabled': True}))
    (home / 'config/context-relay.json').write_text(json.dumps(legacy_json))
    (home / 'config/umbrales-relevo').write_text(legacy_text)
    migrate = [sys.executable, str(here / 'relay-migrate'), str(home)]
    subprocess.run(migrate, check=True, capture_output=True, text=True, timeout=10)
    migrated = json.loads(config.read_text())
    assert 'tokens' not in migrated['thresholds']['primary']['codex']
    assert 'tokens' not in migrated['thresholds']['primary']['claude']
    assert migrated['threshold_tokens'] == 250000
    assert migrated['thresholds']['worker']['codex']['tokens'] == 0
    assert migrated['thresholds']['worker']['claude']['percent'] == 0
    assert migrated['legacy'] == {'context-relay.json': legacy_json, 'umbrales-relevo': legacy_text}
    assert json.loads((home / 'config/relay-lite.json.before-migration').read_text())['threshold_tokens'] == 250000
    subprocess.run(migrate, check=True, capture_output=True, text=True, timeout=10)
    assert json.loads(config.read_text()) == migrated
    assert (home / 'config/umbrales-relevo').read_text() == legacy_text
    # Existing scalar editor remains effective after migration until explicitly overridden.
    migrated['threshold_tokens'] = 70
    config.write_text(json.dumps(migrated))
    transcript.write_text(json.dumps(rows['codex']) + '\n')
    live = subprocess.run([sys.executable, str(here / 'relay-check'), str(home)],
                          input=json.dumps({'transcript_path': str(transcript), 'cwd': str(home)}),
                          env=env, capture_output=True, text=True, timeout=10)
    assert live.returncode == 2, live.stderr
    migrated['threshold_tokens'] = 250000
    config.write_text(json.dumps(migrated))
    result = subprocess.run([sys.executable, str(here / 'relay-check'), '--json', str(home)],
                            input=json.dumps({'transcript_path': str(transcript), 'cwd': str(home)}),
                            env=env, capture_output=True, text=True, timeout=10)
    diagnostic = json.loads(result.stdout)
    assert diagnostic['schema'] == 'relay-lite.check.v1' and diagnostic['state'] in ('below', 'unknown')

    # Rendered text captured from Claude 2.1.325 and Codex 0.169.0 on
    # 2026-11-14 in an isolated Herdr session; workspace paths normalized.
    log = home / 'herdr.log'
    fake = home / 'bin'
    fake.mkdir()
    # Model macOS: no flock and no readable Linux procfs; ps supplies lstart.
    (fake / 'flock').write_text('#!/bin/sh\nexit 127\n')
    (fake / 'flock').chmod(0o755)
    (fake / 'ps').write_text('#!/bin/sh\nprintf "%s\\n" "$FAKE_START"\n')
    (fake / 'ps').chmod(0o755)
    (fake / 'sitecustomize.py').write_text("""import pathlib
original = pathlib.Path.read_text
def read_text(path, *args, **kwargs):
    if str(path).startswith('/proc/'):
        raise OSError('procfs unavailable')
    return original(path, *args, **kwargs)
pathlib.Path.read_text = read_text
""")
    (fake / 'herdr').write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with open(os.environ['HERDR_LOG'], 'a') as stream:
    stream.write(' '.join(sys.argv[1:]) + "\\n")
command = sys.argv[2]
if command == 'split':
    print(json.dumps({'result': {'pane': {'pane_id': 'new-pane'}}}))
elif command == 'process-info':
    print(json.dumps({'result': {'process_info': {'foreground_processes': [{'pid': int(os.environ.get('FAKE_PID', '4242'))}]}}}))
elif command == 'get':
    print(json.dumps({'result': {'pane': {'agent': os.environ['FAKE_AGENT'], 'agent_status': os.environ['FAKE_STATUS']}}}))
elif command == 'read':
    print(Path(os.environ['FAKE_SCREEN']).read_text())
elif command == 'run' and os.environ['FAKE_STARTED'] == 'yes':
    next(Path(os.environ['RELAY_HOME']).glob('state/relay-start.*')).write_text('started')
''')
    (fake / 'herdr').chmod(0o755)
    env.update(PATH=f'{fake}:{env["PATH"]}', HERDR_PANE_ID='old-pane', HERDR_LOG=str(log),
               FAKE_STATUS='working', RELAY_WAIT_SECONDS='1', RELAY_HOME=str(home), FAKE_STARTED='yes',
               FAKE_START='Mon Nov 16 11:00:00 2026', PYTHONPATH=str(fake))
    command = ['bash', str(here / 'relay'), str(home)]
    for index, (harness, flag) in enumerate((('claude', '--effort high'), ('codex', '--no-daemon -m m1 -c model_reasoning_effort=high'))):
        env['FAKE_PID'] = str(4242 + index)
        log.write_text('')
        env.update(FAKE_AGENT=harness, FAKE_SCREEN=str(here / 'tests/startup-dialogs' / (harness + '.txt')))
        result = subprocess.run(command + [harness, 'm1', 'high'], env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        info, split, run, close = log.read_text().splitlines()
        assert split.startswith('pane split old-pane') and close == 'pane close old-pane', log.read_text()
        assert run.startswith('pane run new-pane') and harness in run and flag in run, run
        assert run.index('printf') < run.index('kill') < run.index('fm-session-start.sh'), run
        assert env['FAKE_PID'] in run and 'relay-start.' in run, run
    assert not list((home / 'state').glob('relay-launch.*')), 'completed launches clean their records'
    # An orphaned or live lock has a finite wait and prints the manual escape.
    lock = home / 'state/relay-launch.lock'
    lock.mkdir()
    (lock / 'owner').write_text('999999999\nMon Nov 16 10:00:00 2026\n')
    log.write_text('')
    result = subprocess.run(command + ['codex', '-', '-'], env={**env, 'RELAY_LOCK_WAIT_SECONDS': '0'},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and 'launch lock busy after 0s' in result.stderr
    assert '999999999' in result.stderr and 'rm -rf --' in result.stderr
    assert 'pane split' not in log.read_text()
    (lock / 'owner').unlink()
    lock.rmdir()
    env['FAKE_PID'] = '4244'
    log.write_text('')
    result = subprocess.run(command + ['codex', '-', '-'], env=env, capture_output=True, text=True, timeout=10)
    run = log.read_text().splitlines()[2]
    assert result.returncode == 0 and ' -m ' not in run and 'model_reasoning_effort' not in run, run

    env['FAKE_PID'] = '4245'
    (home / 'config/primary-effort').write_text('low\n')
    log.write_text('')
    result = subprocess.run(command + ['claude', 'm1', 'configured'], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and '--effort low' in log.read_text(), result.stderr
    (home / 'config/primary-effort').write_text('invalid\n')
    log.write_text('')
    result = subprocess.run(command + ['claude', 'm1', 'configured'], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and not log.read_text(), result.stderr

    # Repeated and concurrent invocations belong to the same outgoing process.
    env.update(FAKE_STARTED='no', FAKE_PID='5000', FAKE_SCREEN=str(here / 'tests/startup-dialogs/codex.txt'))
    log.write_text('')
    args = command + ['codex', '-', '-']
    with subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as first, \
         subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as second:
        _, err1 = first.communicate(timeout=10)
        _, err2 = second.communicate(timeout=10)
        assert first.returncode == second.returncode == 1
    again = subprocess.run(args, env=env, capture_output=True, text=True, timeout=10)
    assert again.returncode == 1
    watch1 = next(line for line in err1.splitlines() if ' --watch ' in line)
    assert watch1 in err2 and watch1 in again.stderr, (err1, err2, again.stderr)
    assert sum(line.startswith('pane split ') for line in log.read_text().splitlines()) == 1, log.read_text()
    assert sum(line.startswith('pane run ') for line in log.read_text().splitlines()) == 1, log.read_text()
    # PID reuse with a different process start is a later relay.
    env['FAKE_START'] = 'Mon Nov 16 12:00:00 2026'
    later = subprocess.run(args, env=env, capture_output=True, text=True, timeout=10)
    assert later.returncode == 1 and watch1 not in later.stderr
    assert sum(line.startswith('pane split ') for line in log.read_text().splitlines()) == 2
    for mark in (home / 'state').glob('relay-start.*'): mark.unlink()
    env['FAKE_PID'] = '6000'
    env['FAKE_START'] = 'Mon Nov 16 11:00:00 2026'

    env['FAKE_STARTED'] = 'no'
    for harness in ('claude', 'codex'):
        env.update(FAKE_AGENT=harness, FAKE_SCREEN=str(here / 'tests/startup-dialogs' / (harness + '.txt')))
        for status in ('working', 'done', 'idle', 'blocked'):
            env['FAKE_PID'] = str(int(env['FAKE_PID']) + 1)
            log.write_text('')
            env['FAKE_STATUS'] = status  # label alone must NEVER close the primary
            result = subprocess.run(command + [harness, '-', '-'], env=env, capture_output=True, text=True, timeout=10)
            assert result.returncode == 1 and 'this primary stays' in result.stderr, result
            assert 'pane close old-pane' not in log.read_text(), (harness, status)
            assert Path(env['FAKE_SCREEN']).read_text().strip() in result.stderr, result.stderr
            mark = next((home / 'state').glob('relay-start.*'))
            mark.write_text('started')  # successor executes its first command AFTER approval
            log.write_text('')
            result = subprocess.run(['bash', str(here / 'relay'), '--watch', str(home), 'new-pane', str(mark)],
                                    env=env, capture_output=True, text=True, timeout=10)
            assert result.returncode == 0 and log.read_text() == 'pane close old-pane\n', result
            assert not mark.exists(), 'successful watch cleans the mark'
    # A real startup denial left the old supervisor alive (live-4.log).
    # Pin that same retention path, including a dead or reused vigilance PID.
    (home / 'config/relay-lite.json').write_text(json.dumps({
        'threshold_tokens': 1, 'thresholds': {'supervisor': {'codex': {'tokens': 10}}}}))
    transcript = home / 'supervisor.jsonl'
    transcript.write_text(json.dumps(codex_row) + '\n')
    payload = json.dumps({'cwd': str(home), 'transcript_path': str(transcript)})
    probe = [sys.executable, str(here / 'relay-check'), '--json', '--supervisor', str(home)]
    result = subprocess.run(probe, input=payload, text=True, capture_output=True, env=env)
    assert json.loads(result.stdout)['role'] == 'supervisor' and json.loads(result.stdout)['state'] == 'reached'
    payload_outside = json.dumps({'cwd': str(home / 'elsewhere'), 'transcript_path': str(transcript)})
    result = subprocess.run(probe, input=payload_outside, text=True, capture_output=True, env=env)
    assert json.loads(result.stdout)['state'] == 'skipped'
    (home / 'config/relay-lite.json').write_text('{"threshold_tokens": 1}')
    result = subprocess.run(probe, input=payload, text=True, capture_output=True, env=env)
    assert json.loads(result.stdout)['state'] == 'below', 'primary scalar must not activate supervisor'
    (fake / 'ps').unlink()  # Supervisor retirement uses real process identities and real signals.
    handoff = home / 'handoff.md'
    handoff.write_text('Pending supervision item\n')
    mark = home / 'state/relay-start.supervisor'
    with subprocess.Popen(['sleep', '60']) as old_vigilance, subprocess.Popen(['sleep', '60']) as new_vigilance:
        old_start = subprocess.check_output(['ps', '-o', 'lstart=', '-p', str(old_vigilance.pid)],
                                            env={**env, 'LC_ALL': 'C', 'TZ': 'UTC'}, text=True).rstrip('\n')
        watch = ['bash', str(here / 'relay'), '--watch-supervisor', str(home), 'new-pane',
                 str(mark), str(handoff), str(old_vigilance.pid), old_start]
        try:
            for content in ('', 'started', 'watching 999999999', f'watching {old_vigilance.pid}'):
                mark.write_text(content)
                log.write_text('')
                result = subprocess.run(watch, capture_output=True, text=True, env=env, timeout=10)
                assert result.returncode != 0 and 'pane close' not in log.read_text()
                assert old_vigilance.poll() is None, 'old vigilance must remain until successor is watching'
            result = subprocess.run(['bash', str(here / 'relay'), '--ready', str(home), str(mark),
                                     str(new_vigilance.pid)], capture_output=True, text=True, env=env)
            assert result.returncode == 0, result.stderr
            # A reused outgoing PID must never be signaled, even with a ready successor.
            log.write_text('')
            result = subprocess.run(watch[:-1] + ['different process start'],
                                    capture_output=True, text=True, env=env, timeout=10)
            assert result.returncode == 2 and 'identity changed' in result.stderr
            assert old_vigilance.poll() is None and 'pane close' not in log.read_text()
            # A reused successor PID also cannot authorize retirement.
            ready_content = mark.read_text()
            mark.write_text(f'watching {new_vigilance.pid}\ndifferent process start\n')
            result = subprocess.run(watch, capture_output=True, text=True, env=env, timeout=10)
            assert result.returncode == 1 and old_vigilance.poll() is None
            mark.write_text(ready_content)
            log.write_text('')
            result = subprocess.run(watch, capture_output=True, text=True, env=env, timeout=10)
            assert result.returncode == 0 and log.read_text() == 'pane close old-pane\n', result.stderr
            old_vigilance.wait(timeout=5)
            assert new_vigilance.poll() is None and not mark.exists()
            assert 'Pending supervision item' in handoff.read_text() and 'successor vigilance PID' in handoff.read_text()
        finally:
            if old_vigilance.poll() is None: old_vigilance.terminate()
            new_vigilance.terminate()
print('PASS: supervisor role, disabled defaults, startup denial retains old vigilance, confirmed successor closes old pane')
print('PASS: threshold for both harnesses; startup mark before lock wait; dialogs retain primary; watch resumes the same pane')
