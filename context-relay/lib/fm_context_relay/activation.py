"""Reviewable assisted installation and byte-exact, guarded rollback.

Only the installer writes harness settings. Dry runs enumerate all target
bytes; applying requires an explicit backup directory and never starts relay.
"""
import difflib
import json
import os
import shlex
from pathlib import Path

from . import common, config, install, thresholds

SKILLS = ('preparing-assisted-relay', 'controlling-relay-mode')
SKILL_PATHS = tuple('%s/%s/SKILL.md' % (base, name)
                    for base in ('.claude/skills', '.agents/skills') for name in SKILLS)


def safe_path(home, relative):
    path = home / relative
    resolved = path.resolve()
    # The usual .claude/skills -> ../.agents/skills link is supported, only
    # when it stays inside this home. Settings/config may not be redirected.
    if home not in resolved.parents:
        raise RuntimeError('path outside this home: ' + str(path))
    if relative not in SKILL_PATHS and resolved != path:
        raise RuntimeError('path contains symlink: ' + str(path))
    if path.is_symlink():
        raise RuntimeError('symlink file: ' + str(path))
    return resolved


def proposal(home, runtime_root):
    settings_path = safe_path(home, '.claude/settings.local.json')
    if install._git(str(home), 'ls-files', '--error-unmatch', '--', str(settings_path.relative_to(home)))[0] == 0:
        raise RuntimeError('settings.local.json is tracked; refusing to modify')
    obj, settings_before, problem = install._load_settings(str(settings_path))
    if problem:
        raise RuntimeError(problem)
    # Resolve the effective status line, preserving its output and padding.
    status_line = None
    for candidate in (Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home()/'.claude')/'settings.json',
                      home/'.claude/settings.json', settings_path):
        settings = common.read_json(str(candidate)) or {}
        if 'statusLine' in settings:
            status_line = settings['statusLine']
    if status_line is not None and (not isinstance(status_line, dict) or status_line.get('type') != 'command'
                                    or not isinstance(status_line.get('command'), str)):
        raise RuntimeError('incompatible statusLine; refusing to replace')
    status_line = (status_line or {'type': 'command', 'command': ''}).copy()
    forward = status_line['command']
    if forward.rstrip().endswith(install.MARKER):
        parts = shlex.split(forward)
        if len(parts) != 6 or Path(parts[0]).name != 'fm-context-relay-statusline' or parts[1:3] != ['--home', str(home)] or parts[3] != '--forward':
            raise RuntimeError('marked but unknown statusLine')
        forward = parts[4]
    status_line['command'] = '%s --home %s --forward %s %s' % (
        shlex.quote(str(runtime_root/'bin/fm-context-relay-statusline')), shlex.quote(str(home)), shlex.quote(forward), install.MARKER)
    new, problem = install.merge_claude(obj, str(home))
    if problem:
        raise RuntimeError(problem)
    new['statusLine'] = status_line
    # Commands point at the permanent installation, even when previewed from
    # an isolated worktree. Applying verifies its bytes before writing.
    for groups in new['hooks'].values():
        for group in groups:
            for handler in group.get('hooks', []):
                if install.is_ours(handler):
                    event = next((e for e, _ in install.CLAUDE_EVENTS
                                  if handler['command'] == install.hook_command(str(home), 'claude', e)), None)
                    if event:
                        binary = 'fm-context-relay-guard' if event == 'PreToolUse' else 'fm-context-relay-hook'
                        handler['command'] = '%s --home %s claude %s %s' % (
                            shlex.quote(str(runtime_root/'bin'/binary)), shlex.quote(str(home)), event, install.MARKER)
    cfg_path = safe_path(home, 'config/context-relay.json')
    raw = common.read_bytes(str(cfg_path))
    obj, problem = config.parse_bytes(raw) if raw is not None else (config.DEFAULTS.copy(), None)
    if problem or config.validate(obj)[0]:
        raise RuntimeError('invalid context-relay.json; repair before installing')
    text = thresholds.initial_for(config.Settings(obj, 'file'))
    obj['relay_mode'] = 'assisted'
    threshold_path = safe_path(home, 'config/umbrales-relevo')
    existing = common.read_bytes(str(threshold_path))
    if existing is not None:
        thresholds.parse_lines(existing.decode('utf-8'))
    if install._git(str(home), 'check-ignore', '-q', '--', 'config/umbrales-relevo')[0] != 0:
        raise RuntimeError('config/umbrales-relevo must be gitignored')
    settings_bytes = settings_before if settings_before is not None and new == json.loads(settings_before) else common.dump_json(new)
    result = {settings_path: settings_bytes, cfg_path: common.dump_json(obj),
              threshold_path: existing if existing is not None else text.encode('utf-8')}
    for name in SKILLS:
        skill = safe_path(home, '.claude/skills/%s/SKILL.md' % name)
        skill_source = (Path(install.ext_root())/'skills'/name/'SKILL.md').read_text()
        skill_source = skill_source.replace('Use the installed `fm-context-relay` CLI',
            'Use `%s`' % shlex.join([str(runtime_root/'bin/fm-context-relay'), '--home', str(home)]))
        result[skill] = skill_source.encode('utf-8')
        if install._git(str(home), 'ls-files', '--error-unmatch', '--', str(skill.relative_to(home)))[0] == 0:
            raise RuntimeError('tracked skill; refusing to replace')
        if install._git(str(home), 'check-ignore', '-q', '--', str(skill.relative_to(home)))[0] != 0:
            exclude = safe_path(home, '.git/info/exclude')
            data = result.get(exclude, common.read_bytes(str(exclude)) or b'')
            result[exclude] = data + (b'\n' if data and not data.endswith(b'\n') else b'') + (
                '/' + str(skill.parent.relative_to(home)) + '/\n').encode('utf-8')
    return result


def run(args, paths):
    if args.harness != 'claude':
        raise RuntimeError('--assisted installs Claude only; Codex preparation is separate')
    if args.codex_home:
        raise RuntimeError('--codex-home cannot be used with Claude --assisted')
    home = Path(paths.home)
    problems = install.check_home(str(home))
    if problems:
        raise RuntimeError('; '.join(problems))
    version = install.claude_version()
    if version is not None and version < install.MIN_CLAUDE and not args.force:
        raise RuntimeError('Claude Code older than measured minimum')
    runtime = Path(args.runtime_root or install.ext_root()).absolute()
    proposed = proposal(home, runtime)
    changes = []
    for path, after in proposed.items():
        before = common.read_bytes(str(path))
        if before == after:
            continue
        changes.append((path, before, after))
        print('Archivo: ' + str(path))
        print('\n'.join(difflib.unified_diff((before or b'').decode('utf-8').splitlines(),
            after.decode('utf-8').splitlines(), fromfile=str(path), tofile=str(path)+' (proposed)', lineterm='')))
    if args.dry_run:
        print('Dry run: %d files; no writes or relay. Backup: %s' % (len(changes), args.backup_dir or '(obligatorio al aplicar)'))
        return 0
    # Do not install pointers to a different or stale release.
    for subtree in ('bin', 'lib', 'skills'):
        for source in (Path(install.ext_root())/subtree).rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc':
                target = runtime/source.relative_to(install.ext_root())
                if common.read_bytes(str(target)) != source.read_bytes():
                    raise RuntimeError('stable path does not yet contain this version: ' + str(target))
    if not changes:
        print('Unchanged; assisted already prepared. No relay started.')
        return 0
    if not args.backup_dir:
        raise RuntimeError('--backup-dir is required to apply --assisted')
    backup = Path(args.backup_dir).absolute()
    if backup.exists():
        raise RuntimeError('backup exists; use a new path')
    backup.mkdir(parents=True, mode=0o700)
    manifest = {'home': str(home), 'entries': []}
    for i, (path, before, after) in enumerate(changes):
        saved = '%d.before' % i
        if before is not None:
            common.atomic_write_bytes(str(backup/saved), before, 0o600)
        manifest['entries'].append({'path': str(path), 'before': saved if before is not None else None,
            'mode': path.stat().st_mode & 0o777 if before is not None else None,
            'after_sha256': common.sha256_bytes(after),
            'before_sha256': common.sha256_bytes(before) if before is not None else None})
    common.atomic_write_json(str(backup/'manifest.json'), manifest, 0o600)
    # Preflight all files before the first replacement; each replacement also
    # checks for concurrent edits. Interrupted installs remain rollback-able.
    for path, before, _ in changes:
        if common.read_bytes(str(path)) != before:
            raise RuntimeError('file changed during plan: ' + str(path))
    for path, before, after in changes:
        if common.read_bytes(str(path)) != before:
            raise RuntimeError('file changed; preserve backup: ' + str(path))
        common.atomic_write_bytes(str(path), after)
    print('Assisted prepared. Backup: %s. No relay started.' % backup)
    return 0


def rollback(args, paths):
    backup = Path(args.backup_dir).absolute()
    manifest = json.loads((backup/'manifest.json').read_text())
    if manifest.get('home') != paths.home:
        raise RuntimeError('backup belongs to another home')
    entries = []
    for entry in manifest['entries']:
        path = Path(entry['path'])
        relative = path.relative_to(paths.home)
        if str(relative) not in SKILL_PATHS + ('.claude/settings.local.json', 'config/context-relay.json',
                                             'config/umbrales-relevo', '.git/info/exclude'):
            raise RuntimeError('unmanaged backup path')
        if path.resolve() != path:
            raise RuntimeError('rollback path contains symlink')
        saved = entry['before']
        if saved is not None and (Path(saved).name != saved or not saved.endswith('.before')):
            raise RuntimeError('invalid backup file')
        before = (backup/saved).read_bytes() if saved else None
        if (common.sha256_bytes(before) if before is not None else None) != entry['before_sha256']:
            raise RuntimeError('backup modified')
        current = common.read_bytes(str(path))
        sha = common.sha256_bytes(current) if current is not None else None
        if sha not in (entry['after_sha256'], entry['before_sha256']):
            raise RuntimeError('later edit; refusing to overwrite ' + str(path))
        entries.append((path, before, entry.get('mode')))
    if not args.dry_run:
        for path, before, mode in reversed(entries):
            if before is None:
                path.unlink(missing_ok=True)
            else:
                common.atomic_write_bytes(str(path), before, mode)
    print(('Dry run: would restore' if args.dry_run else 'Restored') + ' %d files; metrics and history retained.' % len(entries))
    return 0
