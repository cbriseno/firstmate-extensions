#!/usr/bin/env python3
"""Dry-run safety and hook ownership/error reporting, without Herdr operations."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

controller = Path(__file__).resolve().parents[1] / 'relay.py'
started = time.monotonic()
spec = importlib.util.spec_from_file_location('relay', controller)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)
for tty in ('ttys001', 'pts/1'):  # macOS and Linux ps output
    row = f'  42 Wed Nov  4 10:03:41 2026 {tty} 123 S+ claude --model opus'
    with patch.object(relay.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, row, '')):
        info = relay.process_info(456)
        assert info == {'pid': 456, 'ppid': 42, 'start': 'Wed Nov 4 10:03:41 2026',
                        'tty': tty, 'session': '123', 'args': 'claude --model opus'}
        assert relay.process(456)['tty'] == tty
with patch.object(relay.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0,
        '42 Wed Nov 4 10:03:41 2026 ?? 123 Z <defunct>', '')):
    assert relay.process(456) is None
assert relay.process(os.getpid()) is not None and relay.ancestor(os.getppid())
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

# Exercise the hook boundary against Firstmate's published ownership API.
# Identity classification belongs to Firstmate; fixtures provide that API.
with tempfile.TemporaryDirectory(prefix='relay-lite-hook-') as temporary:
    home = Path(temporary)
    (home / 'config').mkdir()
    (home / 'state').mkdir()
    (home / 'bin').mkdir()
    (home / 'config/relay-lite.json').write_text(json.dumps({'enabled': True, 'threshold_tokens': 300000}))
    (home / 'state/.lock').write_text(str(os.getpid()))
    (home / 'state/.lock-session').write_text('fork-session')
    ownership = home / 'state/ownership'
    ownership.write_text('held')
    (home / 'bin/fm-primary-scope-lib.sh').write_text('fm_primary_scope_matches() { return 0; }\n')
    (home / 'bin/fm-session-lock-lib.sh').write_text('''
fm_session_lock_owned_by_self() { [ "$(cat "$1/ownership")" = owned ]; }
fm_session_lock_inspect() { FM_LOCK_INSPECT_STATE=$(cat "$1/ownership"); }
''')
    # Lock acquisition belongs to Firstmate, outside the short relay hook.
    acquire = home / 'bin/fm-lock.sh'
    acquire.write_text('#!/bin/sh\ntouch "$FM_HOME/state/unexpected-acquisition"\nexit 91\n')
    acquire.chmod(0o755)
    transcript = home / 'fork-session.jsonl'
    transcript.write_text(json.dumps({'type': 'assistant', 'message': {
        'id': 'call-1', 'model': 'fixture-model', 'usage': {
            'input_tokens': 2, 'cache_creation_input_tokens': 4358, 'cache_read_input_tokens': 591396}}}) + '\n')
    payload = json.dumps({'session_id': 'fork-session', 'transcript_path': str(transcript),
                          'hook_event_name': 'PostToolUse'})
    env = {key: value for key, value in os.environ.items()
           if key not in ('FM_TASK_ID', 'HERDR_ENV', 'HERDR_PANE_ID')}
    env['FM_HOME'] = str(home)
    hook = controller.with_name('relay-hook')
    def invoke():
        result = subprocess.run(['bash', str(hook), str(home)], input=payload, env=env,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()
    before = (home / 'state/.lock').read_bytes()
    alert = json.loads(invoke())
    assert 'another live primary' in alert['systemMessage'], alert
    assert '595756' in alert['systemMessage'] and '300000' in alert['systemMessage'], alert
    assert (home / 'state/.lock').read_bytes() == before
    assert not (home / 'state/unexpected-acquisition').exists()
    assert not (home / 'state/relay-lite/ticket.json').exists()
    assert invoke() == '', 'unchanged conflict must not repeat a warning on every tool'
    with transcript.open('a') as stream:
        stream.write(json.dumps({'type': 'assistant', 'message': {
            'id': 'call-2', 'model': 'fixture-model', 'usage': {'input_tokens': 600000}}}) + '\n')
    assert invoke() == '', 'a larger context is still the same ownership conflict'
    log = home / 'state/relay-lite/controller.log'
    assert 'another live primary' in log.read_text()
    # Below-threshold conflict needs no alarm, then a later crossing does.
    ownership.write_text('owned')
    (home / 'config/relay-lite.json').write_text(json.dumps({'enabled': True, 'threshold_tokens': 900000}))
    assert invoke() == ''
    ownership.write_text('held')
    assert invoke() == ''
    (home / 'config/relay-lite.json').write_text(json.dumps({'enabled': True, 'threshold_tokens': 300000}))
    assert 'another live primary' in json.loads(invoke())['systemMessage']
    # Successful ownership verification rearms even during the cooldown.
    ownership.write_text('owned')
    cooldown = home / 'state/relay-lite/last-relay'
    cooldown.write_text(str(time.time()))
    assert invoke() == ''
    ownership.write_text('held')
    assert 'another live primary' in json.loads(invoke())['systemMessage']
    cooldown.unlink()
    # A dead owner remains Firstmate's acquisition responsibility.
    ownership.write_text('stale')
    assert 'ownership is stale' in json.loads(invoke())['systemMessage']
    assert not (home / 'state/unexpected-acquisition').exists()
    # After normal acquisition restores ownership, the next hook retries;
    # low threshold exposes a second pre-launch failure, without calling Herdr.
    ownership.write_text('owned')
    alert = json.loads(invoke())
    assert 'local Herdr required' in alert['systemMessage'], alert
    assert ownership.read_text().strip() == 'owned'
    assert invoke() == '', 'unchanged pre-launch error must not repeat'
    assert not (home / 'state/relay-lite/ticket.json').exists()
    assert 'local Herdr required' in log.read_text()
    # Disabled and below-threshold hooks cannot change lock ownership.
    ownership.write_text('stale')
    for config in ({'enabled': False}, {'enabled': True, 'threshold_tokens': 900000}):
        (home / 'config/relay-lite.json').write_text(json.dumps(config))
        assert invoke() == ''
        assert ownership.read_text() == 'stale'
        assert not (home / 'state/unexpected-acquisition').exists()
    # An optimistic shell verdict cannot grant authority with a foreign SID.
    ownership.write_text('owned')
    (home / 'config/relay-lite.json').write_text(json.dumps({'enabled': True, 'threshold_tokens': 300000}))
    (home / 'state/.lock-session').write_text('foreign-session')
    assert 'hook is not primary session' in json.loads(invoke())['systemMessage']
    assert not (home / 'state/relay-lite/ticket.json').exists()
print('PASS: ownership conflicts visible once; threshold crossing alerts; restored ownership retries; pre-launch errors visible once')

# A pane client can parent a daemon/model with a new hook SID while the lock
# still records the client and its pre-fork SID. No real Herdr is invoked.
with tempfile.TemporaryDirectory(prefix='relay-lite-client-') as temporary:
    home = Path(temporary)
    for name in ('bin', 'config', 'state', 'state/relay-lite'):
        (home / name).mkdir(exist_ok=True)
    (home / 'config/relay-lite.json').write_text('{"enabled":true,"threshold_tokens":300000}')
    (home / 'state/.lock').write_text(str(os.getpid()))
    (home / 'state/.lock-session').write_text('client-session')
    (home / 'state/relay-lite/last-relay').write_text(str(time.time()))  # No controller launch.
    (home / 'bin/fm-primary-scope-lib.sh').write_text('fm_primary_scope_matches() { return 0; }\n')
    (home / 'bin/fm-session-lock-lib.sh').write_text('fm_session_lock_owned_by_self() { return 0; }\n')
    (home / 'state/pane-owner').write_text(str(os.getpid()))
    herdr = home / 'bin/herdr'
    herdr.write_text('''#!/usr/bin/env python3
import json,pathlib,sys
home=pathlib.Path(__file__).resolve().parents[1]
args=sys.argv[1:]
if args[2:]==['pane','get','fixture-pane']:
 value={'pane':{'pane_id':'fixture-pane','agent':'claude','foreground_cwd':str(home)}}
elif args[2:]==['pane','process-info','--pane','fixture-pane']:
 pid=int((home/'state/pane-owner').read_text())
 value={'process_info':{'foreground_process_group_id':pid,'foreground_processes':[{'pid':pid}]}}
else:
 sys.exit('unexpected Herdr operation')
print(json.dumps({'result':value}))
''')
    herdr.chmod(0o755)
    transcript = home / 'fork-session.jsonl'
    transcript.write_text(json.dumps({'type': 'assistant', 'message': {
        'id': 'call', 'model': 'fixture', 'usage': {'input_tokens': 595756}}}) + '\n')
    payload = json.dumps({'session_id': 'fork-session', 'transcript_path': str(transcript),
                          'hook_event_name': 'PostToolUse'})
    runner = home / 'model.py'
    runner.write_text('''import os,subprocess,sys
if sys.argv[1]=='daemon':
 result=subprocess.run([sys.executable,__file__,'model',*sys.argv[2:]],capture_output=True,text=True,timeout=15)
else:
 env={**os.environ,'CLAUDE_PID':str(os.getpid()),'CLAUDE_CODE_SESSION_ID':'fork-session'}
 result=subprocess.run(['bash',*sys.argv[2:4]],input=sys.argv[4],env=env,capture_output=True,text=True,timeout=10)
sys.stdout.write(result.stdout);sys.stderr.write(result.stderr);sys.exit(result.returncode)
''')
    env = {key: value for key, value in os.environ.items() if key != 'FM_TASK_ID'}
    env.update(FM_HOME=str(home), HERDR_ENV='1', HERDR_PANE_ID='fixture-pane',
               PATH=str(home / 'bin') + ':' + os.environ['PATH'])
    def topology_hook():
        result = subprocess.run([sys.executable, str(runner), 'daemon', str(controller.with_name('relay-hook')),
                                 str(home), payload], env=env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()
    assert topology_hook() == '', 'same foreground client must accept the fork SID without changing its lock'
    assert (home / 'state/.lock-session').read_text() == 'client-session'
    assert not (home / 'state/relay-lite/ticket.json').exists()
    # Neither a different pane client nor an optimistic shell ownership verdict
    # can make another primary the lock holder.
    (home / 'state/pane-owner').write_text(str(os.getppid()))
    assert 'hook is not primary session' in json.loads(topology_hook())['systemMessage']
    subject = relay.Relay(home)
    subject.ticket = dict(old_pid=os.getpid(), old_process=relay.process(os.getpid()),
                          sid='fork-session', lock_sid='client-session')
    assert subject.owner(), 'controller must retain the captured client/SID binding'
    (home / 'state/.lock-session').write_text('fork-session')
    assert subject.owner(), 'normal Firstmate SID refresh must remain valid'
    (home / 'state/.lock-session').write_text('another-primary')
    assert not subject.owner()
print('PASS: client/daemon/model fork SID accepted only for exact foreground client; lock unchanged; other primary refused')
with tempfile.TemporaryDirectory(prefix='relay-lite-tail-') as temporary:
    transcript = Path(temporary) / 'tail-session.jsonl'
    usage = lambda n: json.dumps({'type': 'assistant', 'message': {'id': f'call-{n}', 'model': 'm', 'usage': {
        'input_tokens': n, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0}}}) + '\n'
    filler = json.dumps({'type': 'user', 'message': {'content': 'x' * 1000}}) + '\n'
    transcript.write_text(usage(1) + filler * (relay.TAIL_BYTES // len(filler) + 50) + usage(7))
    tail = relay.measure_tail(transcript)
    assert relay.measure.resident(tail)['tokens'] == 7 and tail['line'] < 5000, tail['line']
subject = relay.Relay(Path(tempfile.gettempdir()))
subject.ticket = dict(claude='claude', sid='98694db5-full-session')
listed = json.dumps([{'id': '98694db5', 'sessionId': '98694db5-full-session', 'kind': 'background'},
                     {'sessionId': 'other', 'kind': 'interactive'}])
with patch.object(relay.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, listed, '')):
    assert subject.background_id() == '98694db5'
    subject.ticket['sid'] = 'other'
    assert subject.background_id() is None, 'interactive sessions keep the /exit path'
print('PASS: tail-only measurement; background sessions detected for claude stop')
import contextlib, io
with tempfile.TemporaryDirectory(prefix='relay-lite-now-') as temporary:
    home = Path(temporary)
    for d in ('config', 'state/relay-lite'):
        (home / d).mkdir(parents=True)
    (home / 'config/relay-lite.json').write_text(json.dumps({'enabled': True, 'threshold_tokens': 400000}))
    (home / 'state/.lock').write_text(str(os.getpid()))
    (home / 'state/.lock-session').write_text('now-session')
    transcript = home / 'now-session.jsonl'
    transcript.write_text(json.dumps({'type': 'assistant', 'message': {'id': 'c', 'model': 'm', 'usage': {
        'input_tokens': 10, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0}}}) + '\n')
    payload = {'session_id': 'now-session', 'transcript_path': str(transcript), 'hook_event_name': 'Stop'}
    def measured():
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            relay.Relay(home).admit(payload, True)
        return json.loads(out.getvalue())['eligible']
    assert not measured(), 'small context stays below threshold'
    (home / 'state/relay-lite/request').write_text('')
    assert measured(), 'a captain request is eligible regardless of threshold'
with patch.object(relay.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'disabled')):
    assert subject.background_id() is None, 'disabled agent view keeps the /exit path'
print('PASS: relay-now request bypasses threshold; disabled agent view tolerated')
