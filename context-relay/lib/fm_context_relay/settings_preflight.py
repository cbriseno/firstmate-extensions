"""Read-only activation eligibility for settings clients; never drives a backend."""
import json
import os
from pathlib import Path
import subprocess
import sys

from . import automatic, common, config, install, pane_close, diagnostics


def view(paths, harness, codex_home=None):
    reasons = []
    authorization = []

    def add(code, message):
        reasons.append(dict(code=code, message=message))

    if not pane_close.processes.safety_evidence_available():
        add(pane_close.processes.RELAY_UNAVAILABLE_CODE, pane_close.processes.RELAY_UNAVAILABLE_MESSAGE)
    if harness not in automatic.SUPPORTED_HARNESSES:
        add('unsupported_harness', 'Automatic relay supports claude and codex only')
    else:
        if harness == 'codex':
            directory = Path(codex_home or os.environ.get('CODEX_HOME') or Path.home()/'.codex')
            path = directory / 'hooks.json'
            events = install.CODEX_EVENTS
        else:
            path = Path(install._settings_path(paths.home))
            events = install.CLAUDE_EVENTS
        try:
            # This is deliberately a read, not installation or trust approval.
            data = path.read_bytes() if path.exists() else None
            obj = json.loads(data) if data is not None else {}
            hooks = obj.get('hooks', {})
            if not isinstance(hooks, dict):
                raise ValueError('hooks must be an object')
            missing = []
            for event, _ in events:
                groups = hooks.get(event, [])
                if not isinstance(groups, list):
                    raise ValueError('hook groups must be lists')
                expected = install.hook_command(paths.home, harness, event)
                if not any(isinstance(group, dict) and isinstance(group.get('hooks'), list)
                           and any(isinstance(h, dict) and h.get('type') == 'command'
                                   and h.get('command') == expected for h in group['hooks']) for group in groups):
                    missing.append(event)
            if missing:
                add('missing_hooks', 'Missing relay hooks: '+', '.join(missing))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            add('invalid_hooks', str(exc))
        if harness == 'codex':
            add('codex_trust_unverified', 'Prepared Codex hooks do not prove native /hooks trust and activation')
    loaded = config.load(paths)
    if loaded.errors:
        add('invalid_configuration', '; '.join(loaded.errors))
    if not automatic.locations(paths)['control'].exists():
        authorization.append(dict(code='missing_standing_authorization', message='No recorded standing authorization'))
    else:
        try:
            automatic.read_authority(paths)
        except (pane_close.Preserve, OSError, ValueError) as exc:
            authorization.append(dict(code='invalid_standing_authorization', message=str(exc)))
    config_ok = loaded.status != 'invalid' and not loaded.errors and not reasons
    checks = [dict(check='config', result='pass' if config_ok else 'fail',
                   reason='ok' if config_ok else 'config_invalid')]
    for item in reasons + authorization:
        checks.append(dict(check='authorization' if item in authorization else 'config',
                           result='fail', reason=item['code']))
    recent = []
    for record_path in Path(paths.sessions).glob('*.json'):
        record = common.read_json(str(record_path)) or {}
        if record.get('schema') == 'fm-context-relay-session.v1' and record.get('harness') == harness:
            recent.append(record)
    recent.sort(key=lambda item: item.get('updated_at') or '', reverse=True)
    record = recent[0] if recent else {}
    effective = loaded.settings.for_model(record.get('model') or '')
    model_ok = harness in automatic.SUPPORTED_HARNESSES and bool(record.get('model')) and effective.get('known') is True
    checks.append(dict(check='model', result='pass' if model_ok else 'fail', reason='ok' if model_ok else 'model_unknown'))
    observed = record.get('window_tokens_observed')
    window_ok = (type(observed) is int and observed > 0 and
                 all(not effective.get(field) or effective[field] < observed for field in ('request_tokens', 'stop_new_work_tokens')))
    checks.append(dict(check='window', result='pass' if window_ok else 'fail', reason='ok' if window_ok else 'window_unknown'))
    try:
        revision = automatic.code_fingerprint(paths)['manifest_hash'][:12]
        code_ok = True
    except (OSError, ValueError):
        revision, code_ok = 'unknown', False
    checks.append(dict(check='code', result='pass' if code_ok else 'fail', reason='ok' if code_ok else 'code_changed'))
    try:
        result = subprocess.run(['herdr', 'status', '--json'], cwd=paths.home, capture_output=True,
                                text=True, timeout=3)
        if result.returncode:
            raise ValueError('herdr status --json rejected')
        herdr = json.loads(result.stdout)
        server, client = herdr.get('server', {}), herdr.get('client', {})
        healthy = (server.get('running') is True and server.get('compatible') is True
                   and client.get('compatible') is True and server.get('session') == client.get('session'))
        checks.append(dict(check='herdr.session', result='pass' if healthy else 'fail', reason='ok' if healthy else 'session_null'))
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        healthy = False
        checks.append(dict(check='herdr.session', result='fail', reason='session_null'))
    process_ok = pane_close.processes.safety_evidence_available()
    try:
        from . import assisted
        assisted.peers(Path(paths.home), harness)
    except (pane_close.Preserve, OSError, ValueError):
        process_ok = False
    checks.append(dict(check='processes', result='pass' if process_ok else 'fail',
                       reason='ok' if process_ok else 'process_active'))
    tasks_ok = True
    for meta in (Path(paths.home)/'state').glob('*.meta'):
        status = Path(paths.home)/'state'/(meta.stem+'.status')
        lines = status.read_text(errors='replace').splitlines() if status.exists() else []
        if not lines or not lines[-1].startswith(('paused ', 'done ', 'failed ')):
            tasks_ok = False
    checks.append(dict(check='tasks.paused', result='pass' if tasks_ok else 'fail',
                       reason='ok' if tasks_ok else 'tasks_active'))
    logger = diagnostics.Logger('extensiones', '0.1.0', revision)
    for check in checks:
        reason = check['reason'] if check['reason'] in diagnostics.ENUMS['reason'] else 'error'
        logger.emit('relay.preflight', check=check['check'], result=check['result'], reason=reason,
                    version='0.1.0', revision=revision)
    logger.flush()
    if not healthy or not process_ok or not tasks_ok or not model_ok or not window_ok or not code_ok:
        reasons.extend(dict(code=item['check'], message=item['reason']) for item in checks
                       if item['result'] == 'fail' and item['check'] in
                       ('herdr.session', 'processes', 'tasks.paused', 'model', 'window', 'code'))
    return dict(schema='fm-extension-settings-preflight.v1', home=paths.home, harness=harness,
                eligible=not reasons and not authorization, can_authorize=not reasons,
                reasons=reasons+authorization, authorization_reasons=authorization, checks=checks)
