"""Standing authority and admission for automatic same-harness handoffs.

The assisted engine remains the only owner of prepare/confirm/retire/launch/
accept and of its dispatch barrier. This module never manipulates its lock.
"""

import argparse
from contextlib import nullcontext
import datetime
import json
import os
from pathlib import Path
import re
import shlex
import signal
import sys
import subprocess
import time
import uuid

from . import common, config, pane_close

require = pane_close.require
DEFAULT_LIMITS = dict(window_seconds=1800, daily_cap=4, min_work_seconds=300,
                      safe_wait_seconds=600)
HARD_DAILY_CAP = 24
SUPPORTED_HARNESSES = {'codex': 'codex-cli 0.166.0', 'claude': '2.1.316 (Claude Code)'}
SUPPORTED_HERDR = dict(version='0.9.3', protocol=22)
SNAPSHOT_MAX_AGE_SECONDS = 60
AGENT_PROBE_SECONDS = 5
AGENT_PROBE_TOTAL_SECONDS = 10
HELP = """Automatic relay control. The initial mode remains warn.
authorize is the sole enable operation: durable operator words/reason, home,
authority identity, code/dependency hashes and bounded limits. It changes only
relay_mode, never thresholds, hooks or trust. Forged auto configuration grants no
authority. Changed code/dependencies, invalid config or revocation preserve warn.
off [--mode warn|assisted] revokes immediately, even with invalid JSON or a busy
config lock. It never reclaims locks, reverses retirement or interrupts processes.
Every automatic action rereads revocation; enabling again needs new authorization.
Defaults: --window-seconds 1800 (60..86400, one attempt per window), --daily-cap 4
(hard cap 24 per UTC day), --min-work-seconds 300 (>=1 plus explicit useful work),
--safe-wait-seconds 600 (1..3600). Failed reserved attempts count. A predecessor
without successor acceptance prevents a new automatic attempt.
Hooks request a safe point only. Save decisions and reconcile operations; live
workers must acknowledge the exact pause key. Pending operator calls, critical
operations, landing and incomplete snapshots block admission. No forced pause.
The same prepare/confirm/barrier/retire/startup/accept/finalizer protocol is used.
System confirmation binds the durable authorization to the package.
Full preflight and optional generic alert arming precede retirement. Failure before
retirement preserves the outgoing owner; failure afterward preserves package/outbox.
Recovery permits one NEW successor, never revival of the old SID. Live or ambiguous
sessions/locks block recovery; stale generations cannot launch late.
show --limit N (1..100, default 20) returns settings JSON. show --json includes
threshold provenance. status [--language en|es] [--json] is read-only. English is
the default summary language. Journal: data/context-relay/automatic-events.jsonl;
a journal entry does not prove external notification delivery.
Exit: 0 success/query, 1 preserved refusal, 2 usage.
Pure catalog data, docs, tests and examples are outside the executable binding.
The Firstmate HEAD and its existing primitive hashes remain bound unchanged.
Legacy authority is rejected, never migrated; each new authority needs actual words.
"""


def bounded_notice(paths, record, reason):
    key = common.session_key(record.get('harness'), record.get('session_id'))
    require(key is not None, 'invalid notice identity')
    target = Path(paths.state_dir)/'admission-notices'/(key+'.json')
    digest = common.sha256_bytes(common.dump_json([request_key(record), reason]))
    with common.FileLock(str(locations(paths)['lock'])):
        if (common.read_json(str(target)) or {}).get('key') == digest:
            return None
        write(target, dict(key=digest, reason=reason))
    return 'Automatic relay held: '+reason+'. Preserve the session.'


def code_fingerprint(paths):
    from . import safety_manifest
    return safety_manifest.fingerprint(paths)


def locations(paths):
    return dict(control=Path(paths.state_dir)/'automatic-authority.json',
                kill=Path(paths.state_dir)/'automatic-kill-switch.json',
                ledger=Path(paths.state_dir)/'automatic-ledger.json',
                journal=Path(paths.data_dir)/'automatic-events.jsonl',
                lock=Path(paths.state_dir)/'automatic.lock')


def write(path, value):
    common.atomic_write_json(str(path), value, 0o600)
    fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def journal(paths, kind, **fields):
    path = locations(paths)['journal']
    common.ensure_dir(str(path.parent), 0o700)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        data = (json.dumps(dict(fields, recorded_at=common.iso(), kind=kind), ensure_ascii=False)+'\n').encode()
        require(os.write(fd, data) == len(data), 'journal incompleto')
        os.fsync(fd)
    finally:
        os.close(fd)


def limits(value):
    require(isinstance(value, dict) and not set(value)-set(DEFAULT_LIMITS), 'unknown automatic limits')
    out = dict(DEFAULT_LIMITS, **value)
    for key, number in out.items():
        require(type(number) is int and number >= 1, key+' must be a positive integer')
    require(out['daily_cap'] <= HARD_DAILY_CAP, 'daily_cap exceeds hard cap 24')
    require(60 <= out['window_seconds'] <= 86400, 'window_seconds outside 60..86400')
    require(out['safe_wait_seconds'] <= 3600, 'safe_wait_seconds exceeds one hour')
    return out


def read_authority(paths):
    """No config loader call here: the loader uses this to reject forged auto."""
    target = locations(paths)['control']
    require(target.resolve() == target, 'autoridad no sigue symlinks')
    value = common.read_json(str(target))
    require(isinstance(value, dict) and value.get('schema') == 'fm-auto-authority.v1'
            and value.get('home') == paths.home and value.get('enabled') is True
            and isinstance(value.get('captain_words'), str) and value['captain_words'].strip()
            and value.get('id'), 'current durable operator authorization missing')
    from . import safety_manifest
    try:
        current = code_fingerprint(paths)
    except (OSError, ValueError) as exc:
        raise pane_close.Preserve('safety_manifest_unavailable: '+str(exc)) from exc
    changes = safety_manifest.changed(value.get('code_fingerprint'), current)
    require(not changes, 'code/dependencies changed: '+', '.join(changes)+
            '; preserve warn and verify before authorizing again')
    require(value.get('kill_generation') == common.read_json(str(locations(paths)['kill'])),
            'kill switch newer than this authorization')
    value['limits'] = limits(value.get('limits', {}))
    return value


def authority(paths):
    """Read the actual file every time. Cached auto settings never authorize."""
    read_authority(paths)  # expose the precise invalidation before mode fallback
    loaded = config.load(paths)
    require(loaded.status == 'ok' and loaded.settings.value('relay_mode') == 'auto',
            'automatic mode disabled or configuration invalid')
    return read_authority(paths)  # do not reuse authority across a config/kill recheck


def _config_bytes(paths):
    data = common.read_bytes(paths.config)
    if data is None:
        return data, {}
    obj, error = config.parse_bytes(data)
    require(not error and isinstance(obj, dict), 'unreadable configuration: use auto off to revoke')
    return data, obj


def set_mode(paths, mode, words=None, reason=None, selected_limits=None, runtime=None, config_locked=False):
    """The explicit authorizer is the only operation that writes enabled=true.

    OFF is published first, even if the settings file cannot be repaired. An
    interrupted authorization or an invalid settings fallback always stays off.
    """
    require(mode in ('auto', 'assisted', 'warn'), 'unknown mode')
    require(Path(paths.home).resolve() == Path(paths.home), 'noncanonical home')
    loc = locations(paths)
    require(loc['control'].parent.resolve() == loc['control'].parent, 'state does not follow symlinks')
    if mode != 'auto':
        # Revoke independently of config.lock. Even an authorizer that was
        # already in flight cannot publish a grant for the old kill generation.
        write(loc['kill'], dict(id=str(uuid.uuid4()), mode=mode, at=common.iso()))
        previous = common.read_json(str(loc['control'])) or {}
        write(loc['control'], dict(schema='fm-auto-authority.v1', home=paths.home,
              enabled=False, mode=mode, revoked_id=previous.get('id'), at=common.iso()))
        journal(paths, 'kill-switch', mode=mode, revoked_id=previous.get('id'))
        try:
            with nullcontext() if config_locked else common.FileLock(paths.config_lock, timeout=0.2):
                _, obj = _config_bytes(paths)
                obj['relay_mode'] = mode
                common.atomic_write_json(paths.config, obj)
        except (pane_close.Preserve, common.LockTimeout):
            return dict(mode=mode, enabled=False, config_preserved=True)
        return dict(mode=mode, enabled=False)
    with nullcontext() if config_locked else common.FileLock(paths.config_lock, timeout=5):
        require(isinstance(words, str) and bool(words.strip()) and isinstance(reason, str)
                and bool(reason.strip()), 'explicit operator words and reason required')
        require(pane_close.processes.safety_evidence_available(), pane_close.processes.RELAY_UNAVAILABLE_MESSAGE)
        require(Path(paths.config).resolve() == Path(paths.config), 'config does not follow symlinks')
        _, obj = _config_bytes(paths)
        obj['relay_mode'] = 'auto'
        errors, _ = config.validate(obj)
        require(not errors, '; '.join(errors))
        value = dict(schema='fm-auto-authority.v1', home=paths.home, enabled=True,
                     id=str(uuid.uuid4()), at=common.iso(), captain_words=words,
                     reason=reason, scope='same-harness', limits=limits(selected_limits or {}),
                     code_fingerprint=code_fingerprint(paths),
                     kill_generation=common.read_json(str(loc['kill'])), runtime=runtime or {})
        journal(paths, 'authorization', **value)
        # Revoke a former record before replacing config. A crash in the middle
        # cannot use an old authorization with a newly enabled configuration.
        write(loc['control'], dict(schema='fm-auto-authority.v1', home=paths.home,
                                  enabled=False, at=common.iso()))
        common.atomic_write_json(paths.config, obj)
        write(loc['control'], value)
        return dict(mode='auto', enabled=True, authorization=value)


def request_key(record):
    relay = record.get('relay') or {}
    return common.sha256_bytes(common.dump_json([record.get('harness'), record.get('session_id'),
                                               relay.get('policy'), relay.get('pending_since')]))


def read_ledger(paths):
    path = locations(paths)['ledger']
    require(path.resolve() == path, 'ledger does not follow symlinks')
    if not path.exists():
        return dict(schema='fm-auto-ledger.v1', requests={}, attempts=[])
    value = common.read_json(str(path))
    require(isinstance(value, dict) and value.get('schema') == 'fm-auto-ledger.v1'
            and isinstance(value.get('requests'), dict) and isinstance(value.get('attempts'), list),
            'automatic ledger unreadable; do not reset limits')
    require(all(isinstance(a, dict) and a.get('id') and common.parse_iso(a.get('at')) is not None
                and isinstance(a.get('accepted'), bool) for a in value['attempts']), 'invalid attempt history')
    require(all(isinstance(r, dict) and r.get('authorization') and r.get('state')
                and common.parse_iso(r.get('deadline')) is not None for r in value['requests'].values()),
            'invalid automatic requests')
    return value


def admission_reason(history, now, selected):
    if history:
        previous = history[-1]
        if previous.get('accepted') is not True:
            return 'previous successor never accepted; do not loop'
        at = common.parse_iso(previous.get('at'))
        if at is None or (now-at).total_seconds() < selected['window_seconds']:
            return 'maximum one relay per window; wait'
    dates = [common.parse_iso(a.get('at')) for a in history]
    if any(d is None for d in dates):
        return 'invalid attempt history; do not reset limits'
    if sum(d.date() == now.date() for d in dates) >= selected['daily_cap']:
        return 'daily relay cap reached (UTC)'
    return None


def notice(paths, record, now=None):
    """One bounded safe-point request. No Stop continuation, daemon or launch."""
    now = now or common.utcnow()
    try:
        auth = authority(paths)
    except pane_close.Preserve as exc:
        saved = common.read_json(str(locations(paths)['control'])) or {}
        if saved.get('enabled') is True and saved.get('home') == paths.home:
            return bounded_notice(paths, record, str(exc))
        return None
    if not (record.get('relay') or {}).get('pending'):
        return None
    from . import admission, session
    proof = admission.evidence(record, session.relay_policy(config.load(paths).settings, record.get('harness')))
    if not proof['admitted']:
        return bounded_notice(paths, record, admission.describe(proof))
    with common.FileLock(str(locations(paths)['lock'])):
        ledger = read_ledger(paths)
        key = request_key(record)
        reason = admission_reason(ledger['attempts'], now, auth['limits'])
        previous = ledger['requests'].get(key)
        if previous and previous['authorization'] == auth['id']:
            if previous['state'] == 'waiting-safe-point' and now > common.parse_iso(previous['deadline']):
                previous.update(state='safe-point-timeout', reason='safe-point deadline expired')
                journal(paths, 'safe-point-timeout', **previous)
                write(locations(paths)['ledger'], ledger)
                record_failure(paths, key, previous['reason'], 'Retain control; inspect pending operations and use assisted or warn.', previous['trigger'])
                return 'Safe-point deadline expired: automatic relay stopped. Retain control and pending work; review the durable alert.'
            if previous['state'] != 'guarded' or reason is not None:
                return None
        deadline = now + datetime.timedelta(seconds=auth['limits']['safe_wait_seconds'])
        item = dict(key=key, session_id=record['session_id'], harness=record['harness'],
                    authorization=auth['id'], state='waiting-safe-point', at=common.iso(now),
                    deadline=common.iso(deadline), trigger=record['relay'])
        if reason:
            item.update(state='guarded', reason=reason)
        ledger['requests'][key] = item
        journal(paths, 'safe-point-request', **item)
        write(locations(paths)['ledger'], ledger)
        if reason:
            return 'Automatic relay held: '+reason+'. Preserve the session and continue current work.'
        cli = str(Path(__file__).resolve().parents[2]/'bin/fm-context-relay')
        notes_path = str(Path(paths.state_dir)/'safe-point.json')
        argv = [cli, '--home', paths.home, 'relay', 'start', '--automatic', '--harness', record['harness'],
                '--notes', notes_path]
        runtime = auth.get('runtime') or {}
        selected_session = runtime.get('herdr_session') or os.environ.get('HERDR_SESSION')
        if selected_session:
            argv += ['--herdr-session', selected_session]
        if runtime.get('herdr_lab_helper'):
            argv += ['--herdr-lab-helper', runtime['herdr_lab_helper']]
        return ('Automatic relay authorized: '+admission.describe(proof)+'. Trigger reached. Save decisions and pending operations, '
                'finish or durably pause critical worker operations, operator calls and landings. '
                'Worker pauses must use the key auto-relay-'+key[:16]+'. '
                'Do not interrupt operations. The safe-point deadline is '+common.iso(deadline)+
                '; if unmet, retain control and record the blocker. '
                'Prepare '+notes_path+' with relay prepare lists, safe_point, owner_sid='+record['session_id']+
                ', owner_pid='+str(record.get('owner_pid'))+', useful_work (trabajo realmente completado) y automatic_safe_point: '
                '{decisions_saved:true,own_operations_reconciled:true,captain_call_in_progress:false,'
                'landing_in_progress:false,critical_operation_in_progress:false}. '
                'Preserve concrete pending work, decisions and uncertainties. Do not invent facts: wait if any requirement is unmet. '
                'At the safe point run ONCE: '+shlex.join(argv)+'. '
                'The system confirms under durable authority; do not ask the operator to confirm again. '
                'After retirement end the turn without more tools so the driver can observe Stop.')


def parked_without_agent(paths, row, operational, deadline):
    """Read the core classifier, never infer death from a failed presence read."""
    task = row['id']
    relative = 'state/'+task+'.meta'
    captured = (operational.get('files') or {}).get(relative) or {}
    text = captured.get('text')
    meta = Path(paths.home)/relative
    if not isinstance(text, str) or meta.is_symlink():
        return None
    data = text.encode('utf-8')
    if captured.get('sha256') != common.sha256_bytes(data):
        return None
    # A remote route must never be interpreted against this home's backend.
    if any(line.startswith('remote_host=') and line[12:] for line in text.splitlines()):
        return None
    remaining = min(AGENT_PROBE_SECONDS, deadline-time.monotonic())
    if remaining <= 0:
        return None
    try:
        if meta.read_bytes() != data:
            return None
        env = dict(os.environ, FM_HOME=paths.home, FM_ROOT_OVERRIDE=paths.home,
                   FM_STATE_OVERRIDE=str(Path(paths.home)/'state'),
                   FM_CONFIG_OVERRIDE=str(Path(paths.home)/'config'))
        process = subprocess.Popen(['bash', '-c',
            '. "$1/bin/fm-backend.sh" || exit 1; '
            'fm_backend_validate_task_endpoint "$2" "$3" >/dev/null 2>&1 || exit 1; '
            'printf "%s\\n" "$FM_BACKEND_VALIDATED_BACKEND" "$FM_BACKEND_VALIDATED_TARGET"; '
            'fm_backend_agent_state "$FM_BACKEND_VALIDATED_BACKEND" "$FM_BACKEND_VALIDATED_TARGET"',
            'fm-context-relay-probe', paths.home, str(meta), task],
            cwd=paths.home, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True)
        try:
            stdout, _ = process.communicate(timeout=remaining)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            return None
        if meta.is_symlink() or meta.read_bytes() != data or process.returncode != 0:
            return None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    fields = stdout.splitlines()
    if len(fields) != 3 or not all(fields) or fields[2] not in ('missing', 'dead'):
        return None
    if fields[0] != row.get('backend') or fields[1] != (row.get('endpoint') or {}).get('target'):
        return None
    status = (operational.get('files') or {}).get('state/'+task+'.status') or {}
    lines = (status.get('text') or '').splitlines()
    return dict(id=task, last_status_line=lines[-1] if lines else None,
                reason='parked_without_agent', unresolved=True,
                worker_liveness=dict(source='backend.agent_state', verdict=fields[2],
                    observed_at=common.iso(), backend=fields[0], target=fields[1],
                    metadata_sha256=captured['sha256'],
                    spawn_gen=next((line[10:] for line in text.splitlines() if line.startswith('spawn_gen=')), None)))


def safe_point(paths, record, notes, operational, now=None):
    """Fail closed on unsaved decisions, unknown workers, calls and landings.

    Primary attestations supplement the deterministic snapshot and control-lock
    checks; they cannot override them. Live children must acknowledge this exact
    safe-point key through their normal durable paused status protocol.
    """
    now = now or common.utcnow()
    from . import session
    loaded = config.load(paths)
    from . import admission
    proof = admission.evidence(record, session.relay_policy(loaded.settings, record.get('harness')))
    require(proof['admitted'], admission.describe(proof))
    require((record.get('scan') or {}).get('caught_up') is True,
            'incomplete measurement; warning only')
    require((record.get('relay') or {}).get('policy') == session.relay_policy(loaded.settings, record.get('harness')),
            'policy changed; reevaluation required')
    proof = notes.get('automatic_safe_point')
    require(isinstance(proof, dict), 'explicit safe-point checkpoint missing')
    for key in ('decisions_saved', 'own_operations_reconciled'):
        require(proof.get(key) is True, 'punto inseguro: '+key)
    for key in ('captain_call_in_progress', 'landing_in_progress', 'critical_operation_in_progress'):
        require(proof.get(key) is False, 'punto inseguro o unknown: '+key)
    require(isinstance(notes.get('useful_work'), list) and bool(notes['useful_work'])
            and all(isinstance(w, str) and w.strip() for w in notes['useful_work']),
            'no completed useful work; do not rotate on bootstrap')
    snapshot = operational.get('snapshot') or {}
    require(snapshot.get('schema') == 'fm-fleet-snapshot.v1' and snapshot.get('fm_home') == paths.home,
            'snapshot ajeno/unknown')
    generated = common.parse_iso(snapshot.get('generated'))
    require(generated is not None and 0 <= (now-generated).total_seconds() <= SNAPSHOT_MAX_AGE_SECONDS,
            'snapshot missing/stale: safe point unverifiable')
    require((snapshot.get('main_inventory') or {}).get('valid') is True,
            'inventario incompleto: punto seguro no verificable')
    require(isinstance(snapshot.get('tasks'), list) and isinstance(snapshot.get('backlog'), dict),
            'snapshot missing tasks/backlog')
    expected_key = 'auto-relay-'+request_key(record)[:16]
    probe_deadline = time.monotonic()+AGENT_PROBE_TOTAL_SECONDS
    parked = []
    for row in snapshot['tasks']:
        state = (row.get('current_state') or {}).get('state')
        if state in ('done', 'failed'):
            continue
        task = row.get('id')
        require(isinstance(task, str) and common.SESSION_ID_RE.fullmatch(task), 'invalid worker ID')
        raw = common.read_bytes(str(Path(paths.home)/'state'/(task+'.status')))
        lines = (raw or b'').decode('utf-8', 'replace').splitlines()
        match = re.match(r'^paused \[key='+re.escape(expected_key)+r'\] \[at=(\d+)\]:', lines[-1]) if lines else None
        since = common.parse_iso((record.get('relay') or {}).get('pending_since'))
        acknowledged = match and since is not None and int(match.group(1)) >= int(since.timestamp())
        if state == 'unknown' and not acknowledged:
            current = row.get('current_state') or {}
            item = parked_without_agent(paths, row, operational, probe_deadline) if (
                current.get('freshness') == 'fresh' and current.get('observed_at') == snapshot['generated']) else None
            if item:
                parked.append(item)
                continue
        require(state == 'paused', 'worker activo/unknown: esperar pausa segura')
        require(acknowledged,
                'worker has not acknowledged this safe point: '+task)
    # Pending captain calls travel unresolved in the package. An answer in
    # progress is blocked by the explicit primary proof and the control claim
    # below, rather than requiring the captain to settle every pending call.
    home = Path(paths.home)
    require(not list((home/'state').glob('.control-*.lock')), 'control/landing operation in progress')
    started = common.parse_iso(record.get('session_started_at')) or common.parse_iso(record.get('first_seen'))
    auth = authority(paths)
    require(started is not None and (now-started).total_seconds() >= auth['limits']['min_work_seconds'],
            'minimum useful work/age unmet')
    notes['parked_without_agent'] = parked
    return auth


def reserve(paths, record, notes, operational, now=None):
    """Claim once before any prepare. All attempts, including failures, count."""
    now = now or common.utcnow()
    with common.FileLock(str(locations(paths)['lock'])):
        auth = safe_point(paths, record, notes, operational, now)
        ledger = read_ledger(paths)
        key = request_key(record)
        require(key in ledger['requests'], 'handover notice not yet recorded')
        request = ledger['requests'][key]
        require(request['authorization'] == auth['id'], 'authority changed; reevaluation required')
        require(request['state'] == 'waiting-safe-point', request.get('reason') or 'trigger already consumed or stopped')
        require(now <= common.parse_iso(request['deadline']), 'safe-point deadline expired; preserve outgoing owner')
        history = ledger['attempts']
        reason = admission_reason(history, now, auth['limits'])
        require(reason is None, reason)
        attempt = dict(id=str(uuid.uuid4()), key=key, at=common.iso(now),
                       authorization=auth['id'], session_id=record['session_id'],
                       trigger=request['trigger'], state='reserved', accepted=False, recoveries=0)
        attempt['parked_without_agent'] = notes['parked_without_agent']
        request['state'] = 'reserved'
        history.append(attempt)
        journal(paths, 'reserved', **attempt)
        write(locations(paths)['ledger'], ledger)
        return attempt


def update(paths, reservation, state, **fields):
    with common.FileLock(str(locations(paths)['lock'])):
        ledger = read_ledger(paths)
        matches = [a for a in ledger['attempts'] if a['id'] == reservation]
        require(len(matches) == 1, 'reserva ausente/duplicada')
        item = matches[0]
        item.update(state=state, **fields)
        journal(paths, 'attempt-state', **item)
        write(locations(paths)['ledger'], ledger)
        return item


def verify_reservation(paths, reservation, record, notes=None):
    auth = authority(paths)
    ledger = read_ledger(paths)
    matches = [a for a in ledger['attempts'] if a['id'] == reservation]
    require(len(matches) == 1 and matches[0]['key'] == request_key(record)
            and matches[0]['authorization'] == auth['id']
            and matches[0]['session_id'] == record['session_id'], 'automatic reservation belongs elsewhere or expired')
    request = ledger['requests'][matches[0]['key']]
    require(common.utcnow() <= common.parse_iso(request['deadline']), 'safe-point deadline expired')
    if notes is not None:
        notes['parked_without_agent'] = matches[0].get('parked_without_agent', [])
    return auth


def record_failure(paths, identity, reason, recovery, trigger=None):
    require(common.SESSION_ID_RE.fullmatch(identity), 'invalid alert ID')
    target = Path(paths.data_dir)/'automatic-outbox'/(identity+'.json')
    value = dict(schema='fm-auto-blocked.v1', at=common.iso(), home=paths.home,
                 identity=identity, reason=reason, recovery=recovery, trigger=trigger,
                 state='pending', delivery_confirmed=False)
    if not target.exists():
        write(target, value)
    return target


def view(paths, limit=20):
    require(type(limit) is int and 1 <= limit <= 100, 'limit outside 1..100')
    try:
        ledger = read_ledger(paths)
    except (pane_close.Preserve, OSError, ValueError) as exc:
        return dict(schema='fm-auto-settings.v1', mode=config.load(paths).settings.value('relay_mode'),
                    auto_enabled=False, status='blocked', reason=str(exc), ledger=None)
    ledger['attempts'] = ledger['attempts'][-limit:]
    ledger['requests'] = dict(list(ledger['requests'].items())[-limit:])
    try:
        value = authority(paths)
        effective = 'auto'
    except pane_close.Preserve as exc:
        value = common.read_json(str(locations(paths)['control']))
        effective = config.load(paths).settings.value('relay_mode')
        if effective == 'auto':
            effective = 'warn'
        return dict(schema='fm-auto-settings.v1', mode=effective, auto_enabled=False, authority=value, reason=str(exc),
                    ledger=ledger)
    return dict(schema='fm-auto-settings.v1', mode=effective, auto_enabled=True, authority=value, ledger=ledger)


def status_summary(result, language):
    from . import thresholds
    spanish = language == 'es'
    enabled = result['auto_enabled']
    lines = [('Relevo automático: %s. Modo: %s.' if spanish else 'Automatic relay: %s. Mode: %s.') % (
        ('autorizado' if enabled else 'no autorizado') if spanish else ('authorized' if enabled else 'not authorized'),
        result['mode'])]
    policies = result['thresholds']['policies']
    lines.append(('Umbrales: ' if spanish else 'Thresholds: ') + '; '.join(
        '%s: %s' % (h, thresholds.format_spec(p) if p else ('no policy' if spanish else 'no policy'))
        for h, p in policies.items()))
    selected = result['limits']
    lines.append(('Límites: un intento cada %s minutos; %s por día UTC; trabajo mínimo %ss; espera segura %ss.' if spanish
                  else 'Limits: one attempt per %s minutes; %s per UTC day; minimum work %ss; safe wait %ss.') % (
        selected['window_seconds']/60, selected['daily_cap'], selected['min_work_seconds'], selected['safe_wait_seconds']))
    attempts = (result.get('ledger') or {}).get('attempts', [])
    lines.append(('Últimos intentos: ' if spanish else 'Latest attempts: ') + (
        '; '.join('%s: %s' % (a.get('at'), a.get('state', 'unknown')) for a in attempts)
        if attempts else ('ninguno registrado' if spanish else 'none recorded')))
    if result.get('reason'):
        lines.append(('Motivo: ' if spanish else 'Reason: ') + result['reason'])
    from . import admission
    for item in result.get('admissions', []):
        lines.append('%s: %s; known_model=%s' % (item['harness'], admission.describe(item), item['known_model']))
    for error in result['thresholds']['errors']:
        lines.append(('Error de configuración: ' if spanish else 'Configuration error: ') + error)
    return '\n'.join(lines)


def run(args, paths):
    try:
        if args.auto_action == 'preflight':
            from . import settings_preflight
            result = settings_preflight.view(paths, args.harness, args.codex_home)
        elif args.auto_action in ('show', 'status'):
            result = view(paths, args.limit)
            if args.auto_action == 'status':
                from . import thresholds
                result['thresholds'] = thresholds.machine_view(paths)
                from . import admission, session
                loaded = config.load(paths)
                records = sorted(Path(paths.sessions).glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
                result['admissions'] = []
                for path in records[:args.limit]:
                    record = common.read_json(str(path)) or {}
                    proof = admission.evidence(record, session.relay_policy(loaded.settings, record.get('harness')))
                    result['admissions'].append(dict(proof, harness=record.get('harness'),
                        known_model=loaded.settings.for_model(record.get('model'))['known']))
                auth = result.get('authority') or {}
                result['limits'] = auth.get('limits', DEFAULT_LIMITS) if result['auto_enabled'] else DEFAULT_LIMITS
                result['summary'] = status_summary(result, args.language)
                if not args.json:
                    print(result['summary'])
                    return 0
        elif args.auto_action == 'off':
            result = set_mode(paths, args.mode)
        elif args.auto_action == 'authorize':
            selected = {k: getattr(args, k) for k in DEFAULT_LIMITS if getattr(args, k) is not None}
            runtime = {k: getattr(args, k) for k in ('herdr_session', 'herdr_lab_helper') if getattr(args, k)}
            result = set_mode(paths, 'auto', args.captain_words, args.reason, selected, runtime)
        else:
            raise pane_close.Preserve('unknown automatic action')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (pane_close.Preserve, OSError, ValueError, common.LockTimeout) as exc:
        print(json.dumps(dict(status='preserved', reason=str(exc)), ensure_ascii=False))
        return 1


def add_parser(sub):
    p = sub.add_parser('auto', help='durable authority, immediate kill switch and settings status', description=HELP,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = p.add_subparsers(dest='auto_action', required=True)
    p = actions.add_parser('preflight', help='read-only machine activation eligibility')
    p.add_argument('--harness', required=True)
    p.add_argument('--codex-home')
    p.set_defaults(func=run)
    p = actions.add_parser('show')
    p.add_argument('--limit', type=int, default=20)
    p.set_defaults(func=run)
    p = actions.add_parser('status', help='read-only plain summary of authority, mode, thresholds and attempts')
    p.add_argument('--limit', type=int, default=3)
    p.add_argument('--language', choices=('en', 'es'), default='en')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=run)
    p = actions.add_parser('off', help='immediate revocation; never retire or reclaim locks')
    p.add_argument('--mode', choices=('assisted', 'warn'), default='warn')
    p.set_defaults(func=run)
    p = actions.add_parser('authorize', help='explicit enable with actual operator words')
    p.add_argument('--captain-words', required=True)
    p.add_argument('--reason', required=True)
    p.add_argument('--herdr-session')
    p.add_argument('--herdr-lab-helper')
    for key in DEFAULT_LIMITS:
        p.add_argument('--'+key.replace('_', '-'), type=int)
    p.set_defaults(func=run)
