#!/usr/bin/env python3
"""One quick executable smoke: measurement dry-run cannot write or call Herdr."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

controller = Path(__file__).resolve().parents[1] / 'relay.py'
started = time.monotonic()
compile(controller.read_text(), str(controller), 'exec')
with tempfile.TemporaryDirectory(prefix='relay-lite-smoke-') as temporary:
    home = Path(temporary)
    (home / 'config').mkdir()
    (home / 'state').mkdir()
    (home / 'bin').mkdir()
    (home / 'config/relay-lite.json').write_text(json.dumps({'enabled': True, 'threshold_tokens': 400000}))
    (home / 'state/.lock').write_text(str(os.getpid()))
    (home / 'state/.lock-session').write_text('smoke-session')
    transcript = home / 'smoke-session.jsonl'
    transcript.write_text(json.dumps({'type': 'assistant', 'message': {
        'id': 'call-1', 'model': 'unlisted-smoke-model', 'usage': {
            'input_tokens': 20, 'cache_creation_input_tokens': 400000, 'cache_read_input_tokens': 5}}}) + '\n')
    forbidden = home / 'bin/herdr'
    forbidden.write_text('#!/bin/sh\necho unexpected-herdr-call >&2\nexit 91\n')
    forbidden.chmod(0o755)
    def snapshot():
        return {str(p.relative_to(home)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in home.rglob('*') if p.is_file()}
    before = snapshot()
    result = subprocess.run([sys.executable, '-B', str(controller), '--home', str(home), '--dry-run'],
                            input=json.dumps({'session_id': 'smoke-session', 'transcript_path': str(transcript),
                                              'hook_event_name': 'Stop'}),
                            env={**os.environ, 'PATH': str(home / 'bin') + ':' + os.environ['PATH']},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['phase'] == 'measurement' and value['dry_run'] and value['eligible'], value
    assert value['resident']['tokens'] == 400025, value
    assert not result.stderr, result.stderr
    assert snapshot() == before, 'dry-run changed files'
    assert not (home / 'state/relay-lite').exists(), 'dry-run created relay state'
print(f'PASS: parses and measures 400025 resident tokens; no writes or Herdr calls ({time.monotonic()-started:.3f}s)')
