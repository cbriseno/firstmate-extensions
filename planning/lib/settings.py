"""Versioned JSON settings facade over the planning policy owner."""
import base64
import argparse
import json
import os
from pathlib import Path
import tempfile
import uuid

from common import PlanningError, atomic, home_root, home_state, installation, locked, read_json, regular, sha
from config import home_config, load
from project_registry import identify, registry
from resolver import resolve
import task_preferences


class Rejected(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def add_parser(subs):
    parent = subs.add_parser('settings', help='versioned JSON settings contract')
    actions = parent.add_subparsers(dest='action', required=True)
    for action in ('status', 'set', 'undo'):
        cmd = actions.add_parser(action)
        cmd.add_argument('--home', default=os.environ.get('FM_HOME'), required=not bool(os.environ.get('FM_HOME')))
        cmd.add_argument('--project')
        cmd.add_argument('--task-id')
        if action in ('set', 'undo'):
            cmd.add_argument('--request-id', required=True)
            cmd.add_argument('--expected-revision', required=True)
        if action == 'set':
            cmd.add_argument('--target', choices=('planning',), required=True)
            cmd.add_argument('--scope', choices=('task', 'project', 'home', 'installation'))
            cmd.add_argument('--mode', choices=('inherit', 'native', 'speckit'))
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
    files = [installation()/'config.json', installation()/'registry.json',
             home_state(home)/'config.json', home/'config/planning-mode', task_preferences.path_for(home)]
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
    return dict(schema='fm-extension-settings.v1', ok=True, identity=ident,
                revision=revision(ident, value),
                planning=dict(scopes=scopes, resolution=resolve(home, str(project), obj, task_id=ident['task_id']) if project else None))


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
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = path.stat().st_mode & 0o7777 if path.exists() else 0o644
            fd, temporary = tempfile.mkstemp(prefix='.planning-', dir=path.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(base64.b64decode(content))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, mode)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)


def mutate(args, ident, value):
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
    # Shared planning lock protects both scoped preferences and this journal.
    with locked(installation()):
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
                applied, before = mutate(args, ident, value)
                value['generation'] += 1
                result = receipt(args, ident, revision(ident, value), 'applied')
                result['result'] = applied
                if before is not None:
                    token = str(uuid.uuid4())
                    result['undo_token'] = token
                    value['undos'][token] = dict(identity=ident, revision=result['revision'], files=before, used=False)
            except Rejected as exc:
                result = receipt(args, ident, revision(ident, value), exc.code, exc.message)
            except (PlanningError, ValueError) as exc:
                result = receipt(args, ident, revision(ident, value), 'invalid_configuration', str(exc))
            except OSError as exc:
                result = receipt(args, ident, revision(ident, value), 'write_failed', str(exc))
        value['requests'][args.request_id]['receipt'] = result
        atomic(state_path(), value)
        return result


def run(args):
    ident = None
    try:
        ident = identity(args)
        if args.action == 'status':
            result = status(ident, state())
        else:
            result = transaction(args, ident)
    except (PlanningError, OSError, ValueError) as exc:
        code = 'busy' if 'busy' in str(exc) else 'invalid_configuration'
        if args.action != 'status':
            result = receipt(args, ident, None, code, str(exc))
        else:
            result = dict(schema='fm-extension-settings-error.v1', ok=False, code=code, message=str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 4 if result.get('code') == 'stale_revision' else 1 if result.get('state') == 'rejected' or result.get('ok') is False else 0
