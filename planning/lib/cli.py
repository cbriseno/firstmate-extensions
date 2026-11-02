"""Portable extension command surface using public scoped skills and private state."""
import argparse
import json
import os
from pathlib import Path
import sys
from common import PlanningError, atomic, home_root, home_state, installation, locked, read_json
from config import home_config, load, save
from materialize import prepare, preview, rollback, verify
from project_registry import identify, registry, relocate, write
from preparation_records import directory
from resolver import resolve
from policy import enforce, inspect


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest='module', required=True)
    planning = subs.add_parser('planning')
    actions = planning.add_subparsers(dest='action', required=True)
    for action in ('status', 'set', 'clear', 'resolve', 'relocate'):
        cmd = actions.add_parser(action)
        cmd.add_argument('--home', default=os.environ.get('FM_HOME'))
        cmd.add_argument('--project')
        cmd.add_argument('--language', choices=('en', 'es'), default=os.environ.get('FM_PLANNING_LANGUAGE'))
        if action in ('set', 'clear'):
            cmd.add_argument('--scope', choices=('installation', 'home', 'project'), required=True)
            cmd.add_argument('--clear-project-overrides', action='store_true')
            cmd.add_argument('--dry-run', action='store_true')
        if action == 'set':
            cmd.add_argument('--mode', choices=('native', 'speckit', 'inherit'), required=True)
        if action == 'resolve':
            cmd.add_argument('--task-mode', choices=('native', 'speckit'))
            cmd.add_argument('--brief', type=Path)
            cmd.add_argument('--require-core', action='store_true')
        if action == 'relocate':
            cmd.add_argument('--id', required=True)
    speckit = subs.add_parser('speckit')
    actions = speckit.add_subparsers(dest='action', required=True)
    for action in ('prepare', 'verify', 'rollback'):
        cmd = actions.add_parser(action)
        cmd.add_argument('--home', default=os.environ.get('FM_HOME'))
        cmd.add_argument('--project', required=True)
        cmd.add_argument('--language', choices=('en', 'es'), default=os.environ.get('FM_PLANNING_LANGUAGE'))
        if action == 'prepare':
            cmd.add_argument('--dry-run', action='store_true')
            cmd.add_argument('--preview-digest')
            cmd.add_argument('--policy-review')
            cmd.add_argument('--delivery-mode', choices=('local-only', 'direct-PR', 'no-mistakes'))
    return p


def status(args, obj):
    inst = load(installation())
    home = home_root(args.home) if args.home else None
    selected = home_config(home) if home else None
    homes = list(dict.fromkeys(obj['homes'] + ([str(home)] if home else [])))
    exceptions = []
    errors = []
    home_defaults = []
    for scope, location, config in [('installation', None, inst)] + [
            ('home', value, None) for value in homes]:
        try:
            if config is None:
                config = home_config(home_root(location))
            if scope == 'home' and config['default'] != 'inherit':
                home_defaults.append({'home': location, 'mode': config['default'], 'origin': 'home'})
            for key, mode in config['projects'].items():
                if mode == 'inherit':
                    continue
                entry = obj['projects'].get(key)
                result = {'project_id': key, 'project': entry['root'] if entry else None,
                          'home': location, 'mode': mode, 'origin': f'project:{scope}'}
                try:
                    if not entry:
                        raise PlanningError('Override refers to an unknown private project ID; repair the registry')
                    local_home = home_root(location) if location else home
                    if not local_home:
                        result['state'] = 'pending'
                        result['cause'] = 'Pass --home to resolve the dependent home policy'
                    else:
                        resolution = resolve(local_home, entry['root'], obj)
                        result['state'] = resolution['availability']
                        result['effective_origin'] = resolution['origin']
                        result['cause'] = resolution['cause']
                except (OSError, PlanningError) as exc:
                    result.update(state='failed', cause=str(exc))
                    errors.append(str(exc))
                exceptions.append(result)
        except (OSError, PlanningError) as exc:
            errors.append(str(exc))
            home_defaults.append({'home': location, 'state': 'failed', 'cause': str(exc)})
    projects = []
    for key, entry in obj['projects'].items():
        row = {'project_id': key, 'project': entry['root']}
        try:
            identify(entry['root'], obj)
            old = read_json(directory(key) / 'receipt.json')
            failure = read_json(directory(key) / 'failure.json')
            journal = read_json(directory(key) / 'journal.json')
            row.update(state='failed' if failure else 'pending' if journal or not old else old['state'],
                       cause=failure.get('cause') if failure else 'Unfinished journal; rollback before retrying' if journal else None)
            if old:
                row['core'] = verify(Path(entry['root']), key, probe=False)
            if home:
                row['resolution'] = resolve(home, entry['root'], obj)
        except (OSError, PlanningError) as exc:
            row.update(state='failed', cause=str(exc))
            errors.append(str(exc))
        projects.append(row)
    result = {'ok': not errors, 'installation_default': inst['default'],
              'home_default': selected['default'] if selected else None,
              'home_exceptions': home_defaults, 'project_exceptions': exceptions,
              'projects': projects, 'errors': errors, 'network_required': False,
              'retained_on_disable': 'Core files, specs, plans, decisions, instructions and ongoing tasks',
              'applies_to': 'new tasks only', 'language': args.language or (selected or inst)['language']}
    if args.project:
        if not home:
            raise PlanningError('Project status requires --home or FM_HOME')
        result['resolution'] = resolve(home, args.project, obj)
    return result


def mutate(args, obj):
    mode = args.mode if args.action == 'set' else 'inherit'
    if mode == 'inherit' and args.action == 'set' and args.scope != 'project':
        raise PlanningError('Use planning clear to inherit a home or installation default')
    if args.clear_project_overrides and args.scope == 'project':
        raise PlanningError('--clear-project-overrides requires installation or home scope')
    if args.scope != 'project' and args.project:
        raise PlanningError('--project conflicts with a default scope; select --scope project')
    changes = []
    home = home_root(args.home) if args.scope in ('home', 'project') else None
    dest = installation() if not home else home_state(home)
    config = load(dest) if not home else home_config(home)
    if args.scope == 'project':
        key, root = identify(args.project, obj, create=True)
        if mode != 'inherit':
            enforce(inspect(root), mode)
        config['projects'][key] = mode
    else:
        config['default'] = mode
    if args.language:
        config['language'] = args.language
    if home and str(home) not in obj['homes']:
        obj['homes'].append(str(home))
    changes.append((dest, config))
    cleared = []
    if args.clear_project_overrides:
        targets = [(dest, config)]
        if args.scope == 'installation':
            targets += [(home_state(home_root(h)), home_config(home_root(h))) for h in obj['homes']]
        for target, value in targets:
            cleared += [{'scope': str(target), 'id': key, 'mode': val} for key, val in value['projects'].items() if val != 'inherit']
            value['projects'] = {}
            if target != dest:
                changes.append((target, value))
    # Validate all affected files before committing, and roll back preferences on I/O failure.
    previous = [(d, (d / 'config.json').read_bytes() if (d / 'config.json').exists() else None) for d, c in changes]
    result = {'scope': args.scope, 'mode': mode, 'cleared_project_overrides': cleared,
              'dry_run': args.dry_run, 'applies_to': 'new tasks only',
              'retained': 'Authored files, prepared core, ongoing tasks and their pinned briefs',
              'language': args.language or config['language']}
    if args.dry_run:
        result['inventory'] = status(argparse.Namespace(home=args.home, project=None, language=args.language), registry())
        return result
    try:
        for d, value in changes:
            save(d, value)
        write(obj)
    except OSError:
        for d, data in previous:
            if data is None:
                (d / 'config.json').unlink(missing_ok=True)
            else:
                (d / 'config.json').write_bytes(data)
        raise
    result['status'] = status(argparse.Namespace(home=args.home, project=None, language=args.language), obj)
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = parser()
    flags = [a.split('=')[0] for a in argv if a.startswith('--')]
    if len(flags) != len(set(flags)):
        p.error('Conflicting repeated options; pass each scope/mode/location once')
    args = p.parse_args(argv)
    obj = registry()
    if args.module == 'planning':
        if args.action == 'status':
            result = status(args, obj)
        elif args.action == 'resolve':
            result = resolve(home_root(args.home), args.project, obj, args.task_mode, args.brief, args.require_core)
        else:
            with locked(installation()):
                obj = registry()
                if args.action == 'relocate':
                    relocate(obj, args.id, args.project)
                    write(obj)
                    result = {'state': 'relocated', 'project_id': args.id}
                else:
                    result = mutate(args, obj)
    else:
        home = home_root(args.home)
        key, root = identify(args.project, obj)
        # Validate policy configuration before touching dependent project files.
        resolution = resolve(home, str(root), obj) if args.action != 'rollback' else None
        if args.action == 'prepare':
            if args.dry_run:
                result, _ = preview(root, key)
                result['resolution'] = resolution
            else:
                # Registration is private; a failed preparation remains visibly pending/failed.
                with locked(installation()):
                    obj = registry()
                    key, root = identify(args.project, obj, create=True)
                    if str(home) not in obj['homes']:
                        obj['homes'].append(str(home))
                    write(obj)
                try:
                    result = prepare(root, key, args.preview_digest, args.delivery_mode, args.policy_review)
                except (PlanningError, OSError) as exc:
                    atomic(directory(key) / 'failure.json', {'state': 'failed', 'cause': str(exc)})
                    raise
        elif args.action == 'verify':
            with locked(directory(key)) if key else locked(installation()):
                result = verify(root, key)
        else:
            result = rollback(root, key)
    language = getattr(args, 'language', None) or result.get('language', 'en')
    result['message'] = 'Operación de planeación completada' if language == 'es' else 'Planning operation completed'
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get('ok', True) else 1


def entry():
    try:
        return main()
    except (OSError, ValueError, KeyError, TypeError, PlanningError) as exc:
        print(json.dumps({'ok': False, 'state': 'failed', 'error': str(exc),
                          'repair': 'Repair the reported configuration or conflict, then retry this dependent cycle; no native fallback'}), file=sys.stderr)
        return 1
