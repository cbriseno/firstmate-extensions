"""Versioned JSON settings facade over the planning and relay policy owners."""
import base64
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

from common import PlanningError, ROOT, atomic, home_root, home_state, installation, locked, read_json, regular, sha
from config import home_config, load
from project_registry import identify, registry
from resolver import resolve
import task_preferences

def relay_modules():
    # Installed planning-only skills retain their independent runtime.
    global automatic, relay_common, relay_config, pane_close, thresholds, preflight
    sys.path.insert(0, str(ROOT / 'context-relay/lib'))
    from fm_context_relay import automatic, common as relay_common, config as relay_config, pane_close, thresholds
    from fm_context_relay.settings_preflight import view as preflight


class Rejected(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def add_parser(subs):
    parent = subs.add_parser('settings', help='versioned JSON settings contract')
    actions = parent.add_subparsers(dest='action', required=True)
    for action in ('status', 'preflight', 'set', 'authorize', 'off', 'undo'):
        cmd = actions.add_parser(action)
        cmd.add_argument('--home', default=os.environ.get('FM_HOME'), required=not bool(os.environ.get('FM_HOME')))
        cmd.add_argument('--project')
        cmd.add_argument('--task-id')
        if action == 'preflight':
            cmd.add_argument('--harness', required=True)
            cmd.add_argument('--codex-home')
        if action in ('set', 'authorize', 'off', 'undo'):
            cmd.add_argument('--request-id', required=True)
            cmd.add_argument('--expected-revision', required=True)
        if action in ('set', 'authorize', 'off'):
            cmd.add_argument('--captain-words')
            cmd.add_argument('--reason')
        if action == 'set':
            cmd.add_argument('--target', choices=('planning', 'relay-mode', 'threshold'), required=True)
            cmd.add_argument('--scope', choices=('task', 'project', 'home', 'installation'))
            cmd.add_argument('--mode', choices=('inherit', 'native', 'speckit', 'warn', 'assisted'))
            cmd.add_argument('--harness', choices=('claude', 'codex'))
            cmd.add_argument('--spec')
        if action == 'authorize':
            for key in ('window_seconds', 'daily_cap', 'min_work_seconds', 'safe_wait_seconds'):
                cmd.add_argument('--'+key.replace('_', '-'), type=int)
        if action == 'off':
            cmd.add_argument('--mode', choices=('warn', 'assisted'), default='warn')
        if action == 'undo':
            cmd.add_argument('--undo-token', required=True)


def state_path():
    return installation() / 'settings-contract.json'


def state():
    value = read_json(state_path(), dict(schema=1, generation=0, requests={}, undos={}))
    if (not isinstance(value, dict) or value.get('schema') != 1
            or type(value.get('generation')) is not int or value['generation'] < 0
            or not isinstance(value.get('requests'), dict) or not isinstance(value.get('undos'), dict)):
        raise PlanningError('Invalid settings receipts; restore private settings-contract.json')
    return value


def identity(args):
    home = home_root(args.home)
    project = identify(args.project, registry())[1] if args.project else None
    if args.task_id:
        task_preferences.task_id(args.task_id)
        if project is None:
            raise PlanningError('--task-id requires --project')
    return dict(installation=str(installation()), home=str(home),
                project=str(project) if project else None, task_id=args.task_id)


def revision(ident, value):
    home = Path(ident['home'])
    paths = relay_common.Paths(str(home))
    files = [installation()/'config.json', installation()/'registry.json',
             home_state(home)/'config.json', home/'config/planning-mode', task_preferences.path_for(home),
             Path(paths.config), Path(thresholds.path_for(paths)),
             automatic.locations(paths)['control'], automatic.locations(paths)['kill']]
    contents = {}
    for path in files:
        regular(path)
        contents[str(path)] = sha(path.read_bytes()) if path.exists() else None
    return sha(json.dumps(dict(identity=ident, files=contents, generation=value['generation']), sort_keys=True).encode())


def status(ident, value):
    home = Path(ident['home'])
    obj = registry()
    key, project = identify(ident['project'], obj) if ident['project'] else (None, None)
    inst, local = load(installation()), home_config(home)
    selected = task_preferences.mode_for(home, project, ident['task_id']) if ident['task_id'] else None
    scopes = {
        'installation': dict(identity=ident['installation'], mode=inst['default']),
        'home': dict(identity=ident['home'], mode=local['default']),
        'project': dict(identity=ident['project'], project_id=key,
                        mode=local['projects'].get(key, 'inherit') if project else None,
                        installation_mode=inst['projects'].get(key, 'inherit') if project else None),
        'task': dict(identity=dict(home=ident['home'], project=ident['project'], task_id=ident['task_id'])
                     if ident['task_id'] else None, mode=selected)}
    paths = relay_common.Paths(str(home))
    loaded = relay_config.load(paths)
    auto = automatic.view(paths)
    policies, sources = thresholds.resolve(loaded.thresholds['entries'], loaded.settings.value('relay_policy'))
    relay = dict(mode=auto['mode'], automatic_enabled=auto['auto_enabled'],
                 errors=loaded.errors, thresholds={h: dict(text=display_spec(policies[h]) if h in policies else None,
                 parsed=policies.get(h), source=sources[h]) for h in ('claude', 'codex')})
    return dict(schema='fm-extension-settings.v1', ok=not loaded.errors, identity=ident,
                revision=revision(ident, value), relay=relay,
                planning=dict(scopes=scopes, resolution=resolve(home, str(project), obj, task_id=ident['task_id']) if project else None))


def display_spec(policy):
    text = thresholds.format_spec(policy)
    tokens = policy.get('min_context_tokens')
    if tokens:
        unit, divisor = ('M', 1000000) if tokens % 1000000 == 0 else ('K', 1000) if tokens % 1000 == 0 else ('', 1)
        text = text.replace('umbral:%d' % tokens, 'umbral:%d%s' % (tokens // divisor, unit))
    return text


def snapshot(files):
    out = {}
    for path in files:
        regular(path)
        out[str(path)] = base64.b64encode(path.read_bytes()).decode() if path.exists() else None
    return out


def restore(files):
    for name, content in files.items():
        path = Path(name)
        regular(path)
        if content is None:
            path.unlink(missing_ok=True)
        else:
            relay_common.atomic_write_bytes(str(path), base64.b64decode(content))


def require_words(args):
    if not args.captain_words or not args.captain_words.strip() or not args.reason or not args.reason.strip():
        raise Rejected('authorization_required', 'Explicit captain words and reason are required')


def mutate(args, ident, value, paths):
    home = Path(ident['home'])
    if args.action == 'undo':
        undo = value['undos'].get(args.undo_token)
        if not undo or undo['identity'] != ident or undo['revision'] != args.expected_revision or undo.get('used'):
            raise Rejected('undo_unavailable', 'Undo requires the same identity and immediately resulting revision')
        before = snapshot([Path(name) for name in undo['files']])
        try:
            restore(undo['files'])
        except OSError:
            restore(before)
            raise
        undo['used'] = True
        return dict(restored=True), None
    if args.action == 'authorize':
        require_words(args)
        selected = {k: getattr(args, k) for k in automatic.DEFAULT_LIMITS if getattr(args, k) is not None}
        return automatic.set_mode(paths, 'auto', args.captain_words, args.reason, selected, config_locked=True), None
    if args.action == 'off' or args.target == 'relay-mode':
        require_words(args)
        if args.mode not in ('warn', 'assisted'):
            raise Rejected('invalid_request', 'Relay mode must be warn or assisted; use authorize for auto')
        # Audit both directions with the captain's exact words, including OFF.
        automatic.journal(paths, 'settings-mode-request', captain_words=args.captain_words,
                          reason=args.reason, request_id=args.request_id, mode=args.mode)
        return automatic.set_mode(paths, args.mode, config_locked=True), None
    if args.target == 'threshold':
        if not args.harness or not args.spec:
            raise Rejected('invalid_request', 'Threshold requires --harness and --spec')
        path = Path(thresholds.path_for(paths))
        before = snapshot([path])
        thresholds.change(paths, 'set', args.spec, args.harness, config_locked=True, request_id=args.request_id)
        return dict(harness=args.harness, parsed=relay_config.load(paths).settings.value('relay_policy')[args.harness]), before
    if args.target == 'planning':
        if args.scope is None or args.mode not in ('native', 'speckit', 'inherit'):
            raise Rejected('invalid_request', 'Planning requires a scope and native, speckit or inherit mode')
        from cli import mutate as planning_mutate
        legacy = argparse.Namespace(action='clear' if args.mode == 'inherit' else 'set', mode=args.mode,
            scope=args.scope, home=ident['home'], project=ident['project'] if args.scope in ('project', 'task') else None,
            task_id=ident['task_id'] if args.scope == 'task' else None, language=None,
            clear_project_overrides=False, dry_run=False)
        if args.scope == 'task':
            if not ident['task_id']:
                raise Rejected('invalid_request', 'Task scope requires project and task ID')
            before = snapshot([task_preferences.path_for(home)])
        else:
            dest = installation() if args.scope == 'installation' else home_state(home)
            before = snapshot([dest/'config.json', installation()/'registry.json'])
        return planning_mutate(legacy, registry(), include_status=False), before
    raise Rejected('invalid_request', 'Unknown settings operation')


def receipt(args, ident, current, code, message=None):
    return dict(schema='fm-extension-settings-receipt.v1', request_id=args.request_id,
                state='applied' if code == 'applied' else 'rejected', code=code,
                revision=current, previous_revision=args.expected_revision, identity=ident,
                undo_token=None, **({'message': message} if message else {}))


def transaction(args, ident):
    paths = relay_common.Paths(ident['home'])
    regular(Path(paths.config_lock))
    # Shared planning lock protects both scoped preferences and this journal.
    with locked(installation()), relay_common.FileLock(paths.config_lock, timeout=5):
        value = state()
        current = revision(ident, value)
        payload = vars(args)
        old = value['requests'].get(args.request_id)
        if old:
            if old['payload'] != payload:
                return receipt(args, ident, current, 'request_id_conflict', 'Request ID already belongs to another operation')
            return old['receipt']
        if not args.request_id.strip() or len(args.request_id) > 256:
            return receipt(args, ident, current, 'invalid_request', 'Request ID must have 1..256 nonblank characters')
        result = receipt(args, ident, current, 'operation_incomplete', 'Interrupted operation; inspect status before submitting another request')
        value['requests'][args.request_id] = dict(payload=payload, receipt=result)
        atomic(state_path(), value)
        if current != args.expected_revision:
            result = receipt(args, ident, current, 'stale_revision', 'Settings changed; read status and submit a new request ID')
        else:
            try:
                applied, before = mutate(args, ident, value, paths)
                value['generation'] += 1
                result = receipt(args, ident, revision(ident, value), 'applied')
                result['result'] = applied
                if before is not None:
                    token = str(uuid.uuid4())
                    result['undo_token'] = token
                    value['undos'][token] = dict(identity=ident, revision=result['revision'], files=before, used=False)
            except Rejected as exc:
                result = receipt(args, ident, revision(ident, value), exc.code, exc.message)
            except (PlanningError, pane_close.Preserve, ValueError) as exc:
                result = receipt(args, ident, revision(ident, value), 'invalid_configuration', str(exc))
            except OSError as exc:
                result = receipt(args, ident, revision(ident, value), 'write_failed', str(exc))
        value['requests'][args.request_id]['receipt'] = result
        atomic(state_path(), value)
        return result


def run(args):
    relay_modules()
    ident = None
    try:
        ident = identity(args)
        if args.action == 'status':
            result = status(ident, state())
        elif args.action == 'preflight':
            result = preflight(relay_common.Paths(ident['home']), args.harness, args.codex_home)
        else:
            result = transaction(args, ident)
    except (PlanningError, pane_close.Preserve, OSError, ValueError, relay_common.LockTimeout) as exc:
        code = 'busy' if isinstance(exc, relay_common.LockTimeout) or 'busy' in str(exc) else 'invalid_configuration'
        if args.action not in ('status', 'preflight'):
            result = receipt(args, ident, None, code, str(exc))
        else:
            result = dict(schema='fm-extension-settings-error.v1', ok=False, code=code, message=str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 4 if result.get('code') == 'stale_revision' else 1 if result.get('state') == 'rejected' or result.get('ok') is False else 0
