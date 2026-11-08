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

    log = home / 'herdr.log'
    fake = home / 'bin'
    fake.mkdir()
    (fake / 'herdr').write_text('#!/bin/sh\necho "$@" >> "$HERDR_LOG"\ncase "$2" in\n'
                                'split) echo \'{"result":{"pane":{"pane_id":"new-pane"}}}\';;\n'
                                'process-info) echo \'{"result":{"process_info":{"foreground_processes":[{"pid":4242}]}}}\';;\n'
                                'get) echo "{\\"result\\":{\\"pane\\":{\\"agent\\":\\"$FAKE_AGENT\\",\\"agent_status\\":\\"$FAKE_STATUS\\"}}}";;\n'
                                'esac\n')
    (fake / 'herdr').chmod(0o755)
    env.update(PATH=f'{fake}:{env["PATH"]}', HERDR_PANE_ID='old-pane', HERDR_LOG=str(log), FAKE_STATUS='working', RELAY_WAIT_SECONDS='3')
    for harness, flag in (('claude', '--effort high'), ('codex', '--no-daemon -m m1 -c model_reasoning_effort=high')):
        log.write_text('')
        env['FAKE_AGENT'] = harness
        result = subprocess.run(['bash', str(here / 'relay'), str(home), harness, 'm1', 'high'],
                                env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        info, split, run, get, close = log.read_text().splitlines()
        assert split.startswith('pane split old-pane') and close == 'pane close old-pane', log.read_text()
        assert run.startswith('pane run new-pane') and harness in run and 'm1' in run and flag in run and '4242' in run and 'fm-session-start.sh' in run, run
    log.write_text('')
    result = subprocess.run(['bash', str(here / 'relay'), str(home), 'codex', '-', '-'], env=env, capture_output=True, text=True, timeout=10)
    run = log.read_text().splitlines()[2]
    assert result.returncode == 0 and ' -m ' not in run and 'model_reasoning_effort' not in run and 'codex' in run, run
    log.write_text('')
    env.update(FAKE_STATUS='idle', RELAY_WAIT_SECONDS='600')  # stuck on a startup menu: must exit after ~20 s, not 600
    result = subprocess.run(['bash', str(here / 'relay'), str(home), 'codex', 'm1', 'low'], env=env, capture_output=True, text=True, timeout=40)
    assert result.returncode == 1 and 'this primary stays' in result.stderr, result
    assert 'pane close old-pane' not in log.read_text(), 'old pane must stay when the new one is not working'
print('PASS: threshold blocks the stop for Claude and Codex; relay closes the old pane only once the new harness is working')
