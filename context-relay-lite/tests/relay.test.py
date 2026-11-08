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
        assert result.returncode == 2 and 'haz el relevo' in result.stderr, result
        assert check(row, 300000).returncode == 0, 'below threshold'
        assert check(row, 0).returncode == 0, 'threshold 0 is off'
        assert check(row, 290000, stop_hook_active=True).returncode == 0, 'never block twice in a row'
        assert check(row, 290000, cwd='/').returncode == 0, 'only the primary in its home'

    log = home / 'herdr.log'
    fake = home / 'bin'
    fake.mkdir()
    (fake / 'herdr').write_text('#!/bin/sh\necho "$@" >> "$HERDR_LOG"\n'
                                'case "$2" in split) echo \'{"result":{"pane":{"pane_id":"new-pane"}}}\';; esac\n')
    (fake / 'herdr').chmod(0o755)
    env.update(PATH=f'{fake}:{env["PATH"]}', HERDR_PANE_ID='old-pane', HERDR_LOG=str(log))
    for harness, flag in (('claude', '--effort high'), ('codex', 'model_reasoning_effort=high')):
        log.write_text('')
        result = subprocess.run(['bash', str(here / 'relay'), str(home), harness, 'm1', 'high'],
                                env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        split, run, close = log.read_text().splitlines()
        assert split.startswith('pane split old-pane') and close == 'pane close old-pane', log.read_text()
        assert run.startswith('pane run new-pane') and harness in run and 'm1' in run and flag in run, run
print('PASS: threshold blocks the stop for Claude and Codex; relay opens sibling, launches same harness, closes old pane')
