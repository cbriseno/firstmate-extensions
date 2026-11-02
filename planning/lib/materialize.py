"""Guarded offline projection with preview binding, write-ahead journal and rollback."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from common import PlanningError, atomic, locked, read_json, regular, sha
from core import bundle
import policy as project_policy
from preparation_records import directory, receipt
from project_registry import fingerprint


def safe(root, name):
    rel = Path(name)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.parts[0] not in ('.specify', '.agents', '.claude'):
        raise PlanningError('Unsafe managed path in receipt; restore private state before continuing')
    path = root / rel
    regular(path)
    return path


def inspect_tree(root):
    for name in ('.specify', '.agents/skills', '.claude/skills'):
        path = root / name
        for parent in (path, *path.parents):
            if parent == root.parent:
                break
            if parent.is_symlink():
                raise PlanningError(f'Symlink in integration tree: {parent}; review it before preparing')
        if path.exists():
            for item in path.rglob('*'):
                if item.is_symlink():
                    raise PlanningError(f'Symlink in integration tree: {item}; review it before preparing')


def preview(root, key):
    pin, assets = bundle()
    inspect_tree(root)
    pol = project_policy.inspect(root)
    project_policy.enforce(pol, 'speckit')
    old = receipt(key)
    managed = set(assets)
    # Existing authored constitution is retained; unknown integration machinery is a conflict.
    integration = root / '.specify'
    if integration.exists():
        for item in integration.rglob('*'):
            if item.is_file() and not str(item.relative_to(root)).startswith('.specify/memory/') and str(item.relative_to(root)) not in managed:
                raise PlanningError(f'Unknown existing integration file: {item.relative_to(root)}; preserve and reconcile it before preparing')
    if key and read_json(directory(key) / 'journal.json'):
        raise PlanningError('Unfinished preparation journal; run speckit rollback for this project, then retry preview')
    if old and old['digest'] != pin['digest']:
        raise PlanningError('Project uses a different core pin; retain its environment and perform a reviewed three-way upgrade')
    changes = []
    for name, (data, mode) in assets.items():
        path = safe(root, name)
        record = old['files'].get(name) if old else None
        current = sha(path.read_bytes()) if path.exists() else None
        if path.exists() and (not record or current != record['sha256'] or (path.stat().st_mode & 0o777) != record['mode']):
            raise PlanningError(f'Unmanaged or modified core file: {name}; preserve it and reconcile the conflict before preparation')
        if record and current is None:
            raise PlanningError(f'Managed core file missing: {name}; restore it or use explicit rollback before preparing')
        changes.append({'path': name, 'action': 'keep' if path.exists() else 'create',
                        'before': current, 'after': sha(data), 'mode': mode})
    if shutil.disk_usage(root).free < sum(len(data) for data, mode in assets.values()) * 4 + 1024 * 1024:
        raise PlanningError('Insufficient free space for staging and backup; free space and retry')
    if not os.access(root, os.W_OK):
        raise PlanningError('Project is not writable; use dry-run only until project modification is authorized')
    result = {'schema': 1, 'project': str(root), 'project_id': key, 'project_identity': fingerprint(root), 'core': pin,
              'network_required': False, 'policy': pol, 'changes': changes,
              'harnesses': ['codex', 'claude'], 'trust': 'not-granted',
              'delivery_required': True, 'retained': ['authored specs, plans, decisions, constitution and instructions']}
    result['preview_digest'] = sha(json.dumps(result, sort_keys=True).encode())
    return result, assets


def smoke(root):
    """Exercise all helpers on a disposable project, never on authored artifacts."""
    Path.home().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.core-probe-', dir=Path.home()) as tmp:
        probe = Path(tmp)
        for name in ('.specify/scripts', '.specify/templates'):
            shutil.copytree(root / name, probe / name)
        env = {**os.environ, 'SPECIFY_FEATURE_DIRECTORY': str(probe / 'specs/probe'),
               'SPECIFY_FEATURE_NO_PERSIST': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
        for stage in ('specify', 'clarify', 'plan', 'tasks', 'analyze', 'prerequisites'):
            proc = subprocess.run([sys.executable, str(probe / '.specify/scripts/python/core.py'), stage, '--json'],
                                  env=env, capture_output=True, text=True, timeout=10)
            if proc.returncode:
                raise PlanningError(f'Core probe failed at {stage}: {proc.stderr.strip()}; inspect adapter before retrying')
            json.loads(proc.stdout)
        proc = subprocess.run(['bash', str(probe / '.specify/scripts/bash/check-prerequisites.sh'), '--json'],
                              env=env, capture_output=True, text=True, timeout=10)
        if proc.returncode:
            raise PlanningError(f'Prerequisite wrapper probe failed: {proc.stderr.strip()}')
        json.loads(proc.stdout)
        if (probe / '.specify/feature.json').exists():
            raise PlanningError('Core probe unexpectedly persisted shared feature selection')


def verify(root, key, probe=True):
    pin, assets = bundle()
    inspect_tree(root)
    old = receipt(key)
    if not old or old['digest'] != pin['digest']:
        raise PlanningError('Core unavailable for this project; run speckit prepare --dry-run, review, then apply its preview')
    if read_json(directory(key) / 'journal.json'):
        raise PlanningError('Preparation journal is pending; rollback it before verifying')
    for name, (data, mode) in assets.items():
        path = safe(root, name)
        if (name not in old['files'] or not path.is_file() or sha(path.read_bytes()) != sha(data)
                or path.stat().st_mode & 0o777 != mode):
            raise PlanningError(f'Core verification failed: {name}; restore the managed file or explicitly rollback')
    project_policy.enforce(project_policy.inspect(root), 'speckit')
    if probe:
        smoke(root)
    return {'state': 'verified', 'version': pin['version'], 'digest': pin['digest'],
            'prepared': True, 'trusted': 'unknown', 'native_discovery': 'unverified',
            'skills_present': ['codex', 'claude'], 'network_required': False}


def restore(root, journal):
    # Validate the complete rollback before removing any file. Refuse user edits.
    pin, assets = bundle()
    if (not isinstance(journal, dict) or journal.get('schema') != 1
            or set(journal.get('files', {})) != set(assets)):
        raise PlanningError('Invalid rollback journal; restore its private backup before retrying')
    allowed_dirs = {str(parent) for name in assets for parent in Path(name).parents if str(parent) != '.'}
    if any(name not in allowed_dirs for name in journal.get('created_dirs', [])):
        raise PlanningError('Invalid rollback directory; restore the private journal')
    for name, entry in journal['files'].items():
        if entry.get('after') != sha(assets[name][0]):
            raise PlanningError('Rollback digest differs from pinned ownership; inspect private state')
    for name, entry in journal['files'].items():
        path = safe(root, name)
        current = sha(path.read_bytes()) if path.exists() else None
        before = sha(base64.b64decode(entry['before'])) if entry['before'] is not None else None
        if current not in (before, entry['after'], None):
            raise PlanningError(f'Rollback preserves modified file: {name}; reconcile it and retry rollback')
    for name, entry in journal['files'].items():
        path = safe(root, name)
        if entry['before'] is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(entry['before']))
            path.chmod(entry['mode'])
    for name in sorted(journal.get('created_dirs', []), key=lambda n: len(Path(n).parts), reverse=True):
        path = root / name
        if path.is_symlink() or '..' in Path(name).parts or Path(name).is_absolute():
            raise PlanningError('Unsafe directory in rollback journal; inspect private state')
        try:
            path.rmdir()
        except (FileNotFoundError, OSError):
            pass  # Authored additions keep their parent directories.


def prepare(root, key, expected, delivery, review):
    state = directory(key)
    with locked(state):
        plan, assets = preview(root, key)
        # A first preview has no assigned ID; bind to root identity instead.
        comparison = dict(plan, project_id=None)
        comparison.pop('preview_digest')
        unregistered_digest = sha(json.dumps(comparison, sort_keys=True).encode())
        if expected not in (plan['preview_digest'], unregistered_digest):
            raise PlanningError('Preview changed or missing; run prepare --dry-run again and review the exact diff')
        if plan['policy']['review_required'] and review != plan['policy']['digest']:
            raise PlanningError('Project policy review required; read the preview policy files and pass --policy-review DIGEST')
        if not delivery:
            raise PlanningError('Pass --delivery-mode under the worker authority; a preference does not authorize project writes')
        if all(c['action'] == 'keep' for c in plan['changes']):
            return verify(root, key)
        old = receipt(key)
        journal = {'schema': 1, 'state': 'preparing', 'root': str(root), 'files': {},
                   'created_dirs': [], 'previous_receipt': old, 'delivery_mode': delivery}
        for name, (data, mode) in assets.items():
            path = safe(root, name)
            journal['files'][name] = {'before': base64.b64encode(path.read_bytes()).decode() if path.exists() else None,
                                      'mode': path.stat().st_mode & 0o777 if path.exists() else mode, 'after': sha(data)}
            parent = path.parent
            while parent != root and not parent.exists():
                rel = str(parent.relative_to(root))
                if rel not in journal['created_dirs']:
                    journal['created_dirs'].append(rel)
                parent = parent.parent
        atomic(state / 'journal.json', journal)
        try:
            # Staging shares the project filesystem; the private journal retains the backup.
            with tempfile.TemporaryDirectory(prefix='.core-stage-', dir=root) as tmp:
                staging = Path(tmp)
                for name, (data, mode) in assets.items():
                    staged = staging / name
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    staged.write_bytes(data)
                    staged.chmod(mode)
                for change in plan['changes']:
                    if change['action'] == 'create':
                        dest = safe(root, change['path'])
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        # Exclusive creation prevents overwriting a file that appeared since preflight.
                        with dest.open('xb') as stream:
                            stream.write((staging / change['path']).read_bytes())
                        dest.chmod(change['mode'])
            smoke(root)
            record = {'schema': 1, 'state': 'verified', 'digest': plan['core']['digest'],
                      'version': plan['core']['version'], 'root': str(root), 'delivery_mode': delivery,
                      'files': {c['path']: {'sha256': c['after'], 'mode': c['mode']} for c in plan['changes']}}
            atomic(state / 'receipt.json', record)
            # Keep exact pre-state as the explicit rollback receipt; active journal is removed last.
            atomic(state / 'backup.json', journal)
            (state / 'journal.json').unlink()
            (state / 'failure.json').unlink(missing_ok=True)
            return verify(root, key, probe=False)
        except Exception as exc:
            try:
                restore(root, journal)
                if old:
                    atomic(state / 'receipt.json', old)
                else:
                    (state / 'receipt.json').unlink(missing_ok=True)
                (state / 'journal.json').unlink()
            finally:
                atomic(state / 'failure.json', {'state': 'failed', 'cause': str(exc)})
            raise


def rollback(root, key):
    if not key:
        raise PlanningError('Project has no managed preparation; inspect planning status')
    state = directory(key)
    with locked(state):
        journal = read_json(state / 'journal.json') or read_json(state / 'backup.json')
        if not journal:
            raise PlanningError('No rollback backup; preserve authored files and inspect planning status')
        # A moved project is accepted only through registry relocation; the ID still owns its files.
        restore(root, journal)
        if journal['previous_receipt']:
            atomic(state / 'receipt.json', journal['previous_receipt'])
        else:
            (state / 'receipt.json').unlink(missing_ok=True)
        for name in ('journal.json', 'backup.json', 'failure.json'):
            (state / name).unlink(missing_ok=True)
    return {'state': 'rolled-back', 'retained': 'All authored files and mode preferences; speckit is pending until prepared again'}
