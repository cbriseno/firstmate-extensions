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

    # Rendered text captured from Claude 2.1.325 and Codex 0.169.0 on
    # 2026-11-14 in an isolated Herdr session; workspace paths normalized.
    log = home / 'herdr.log'
    fake = home / 'bin'
    fake.mkdir()
    (fake / 'herdr').write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with open(os.environ['HERDR_LOG'], 'a') as stream:
    stream.write(' '.join(sys.argv[1:]) + "\\n")
command = sys.argv[2]
if command == 'split':
    print(json.dumps({'result': {'pane': {'pane_id': 'new-pane'}}}))
elif command == 'process-info':
    print(json.dumps({'result': {'process_info': {'foreground_processes': [{'pid': 4242}]}}}))
elif command == 'get':
    print(json.dumps({'result': {'pane': {'agent': os.environ['FAKE_AGENT'], 'agent_status': os.environ['FAKE_STATUS']}}}))
elif command == 'read':
    print(Path(os.environ['FAKE_SCREEN']).read_text())
elif command == 'run' and os.environ['FAKE_STARTED'] == 'yes':
    next(Path(os.environ['RELAY_HOME']).glob('state/relay-start.*')).write_text('started')
''')
    (fake / 'herdr').chmod(0o755)
    env.update(PATH=f'{fake}:{env["PATH"]}', HERDR_PANE_ID='old-pane', HERDR_LOG=str(log),
               FAKE_STATUS='working', RELAY_WAIT_SECONDS='1', RELAY_HOME=str(home), FAKE_STARTED='yes')
    command = ['bash', str(here / 'relay'), str(home)]
    for harness, flag in (('claude', '--effort high'), ('codex', '--no-daemon -m m1 -c model_reasoning_effort=high')):
        log.write_text('')
        env.update(FAKE_AGENT=harness, FAKE_SCREEN=str(here / 'tests/startup-dialogs' / (harness + '.txt')))
        result = subprocess.run(command + [harness, 'm1', 'high'], env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        info, split, run, close = log.read_text().splitlines()
        assert split.startswith('pane split old-pane') and close == 'pane close old-pane', log.read_text()
        assert run.startswith('pane run new-pane') and harness in run and flag in run, run
        assert run.index('printf') < run.index('kill') < run.index('fm-session-start.sh'), run
        assert '4242' in run and 'relay-start.' in run, run
    log.write_text('')
    result = subprocess.run(command + ['codex', '-', '-'], env=env, capture_output=True, text=True, timeout=10)
    run = log.read_text().splitlines()[2]
    assert result.returncode == 0 and ' -m ' not in run and 'model_reasoning_effort' not in run, run

    env['FAKE_STARTED'] = 'no'
    for harness in ('claude', 'codex'):
        env.update(FAKE_AGENT=harness, FAKE_SCREEN=str(here / 'tests/startup-dialogs' / (harness + '.txt')))
        for status in ('working', 'done', 'idle', 'blocked'):
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
print('PASS: threshold for both harnesses; startup mark before lock wait; dialogs retain primary; watch resumes the same pane')
