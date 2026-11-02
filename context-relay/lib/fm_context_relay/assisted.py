"""Explicit assisted handoff. The CLI help owns the attempt protocol.

Firstmate owns session start and lock release; pane_close owns terminal closure.
This module never acquires, deletes or copies a session lock or session identity.
"""

import argparse
import contextlib
import json
import io
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid

from . import common, config, events, identity, pane_close, session

HELP = """Local Linux/Herdr relay. Hooks request a safe point; they do not launch.
relay start is the guided entrypoint (relay start --help).
prepare requires native ownership, pending policy, assisted mode and a safe point.
--notes JSON lists pending_work, decisions, instructions, dependencies, blockers,
uncertain_operations, pending_notices, references and a safe_point explanation.
References must exist relative to this home. --launch JSON preserves native argv
and allowed configuration, without resume/continue or reused identity.
--new-pane must be an empty sibling in the same tab/home.
confirm binds explicit operator words to the package hash; preparation grants no
retirement authority. retire uses fm-lock-release.sh under the outgoing owner.
A retained lock remains until native /exit; never delete locks or restart outgoing
startup. launch runs only after verified native exit and no other live home session.
Intent is saved before delivery; uncertain delivery cannot be retried blindly.
accept requires fresh native SID/PID, completed startup, own lock, matching package,
argv/config and explicit recovery review. Nonempty recovery blockers forbid dispatch.
Inherited task holds remain unresolved. Only durable successor-ready/successor-accepted receipts open the
successor barrier. pane-close has independent inspection and confirmation guards.
abort is allowed only before retire/launch. status --package is read-only.
The PreToolUse guard permits native reads and one simple relay CLI invocation;
compound scripts, dispatch and queue acknowledgments remain blocked.
Every transition/rejection is journaled. Firstmate's lock remains the authority.
Recovery allows one fresh successor only after proving no live owner/lock and an
empty target. Replaced launch generations cannot execute late. Another failure
preserves the package/outbox with no further automatic retries.
Exit: 0 transition/no-op, 1 preserved refusal, 2 usage/environment."""

NOTE_KEYS = ('pending_work', 'decisions', 'instructions', 'dependencies', 'blockers',
             'uncertain_operations', 'pending_notices', 'references')
ENV_KEYS = {'PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'TERM', 'LANG', 'LC_ALL', 'XDG_CONFIG_HOME', 'BASH_ENV',
            'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_DISABLE_1M_CONTEXT',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC', 'DISABLE_AUTOUPDATER',
            'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL',
            'CLAUDE_CODE_SUBAGENT_MODEL', 'FM_HOME', 'FM_ROOT_OVERRIDE',
            'FM_CONTEXT_RELAY_THROTTLE_SECONDS', 'FM_CONTEXT_RELAY_SNAPSHOT_TIMEOUT',
            'FM_HERDR_LAB_STATE_DIR', 'FM_RUNTIME_BACKEND', 'FM_STARTUP_NETWORK_MODE',
            'LAB_EVIDENCE', 'LAB_RELAY_HOOK', 'LAB_HARNESS'}
ENV_KEYS.add('TMUX_TMPDIR')
require = pane_close.require
load = pane_close.load


def digest(path):
    return common.sha256_bytes(Path(path).read_bytes())


def put(path, obj):
    common.atomic_write_json(str(path), obj, 0o600)


def command(argv, home, timeout=30):
    env = dict(os.environ, FM_HOME=str(home), FM_ROOT_OVERRIDE=str(home),
               FM_STATE_OVERRIDE=str(home/'state'), FM_CONFIG_OVERRIDE=str(home/'config'))
    result = subprocess.run(argv, cwd=home, env=env, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, '%s: %s' % (Path(argv[0]).name, result.stderr[-500:] or result.stdout[-500:]))
    return result.stdout


def native(home, harness, payload=None):
    return identity.native(harness, home, payload or {}, worker_hook=payload is not None)


def argv_of(pid):
    return [v.decode() for v in Path('/proc/%s/cmdline' % pid).read_bytes().split(b'\0') if v]


def no_resume(argv):
    require(not any(a in ('resume', 'fork') or a.startswith(('--resume', '--continue', '--fork-session', '--session-id'))
                    or a.startswith('-r') or (a == '-c' and
                        (i+1 >= len(argv) or '=' not in argv[i+1]))
                    for i, a in enumerate(argv)), 'resume/continue/session-id prohibido')


def peers(home, harness, allowed=()):
    """Read only processes whose kernel cwd is this exact home.

    No process-global session store or other home endpoint is consulted.
    """
    found = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit() or int(path.name) in allowed:
            continue
        try:
            name = (path/'comm').read_text().strip()
            if not (name == 'codex' or name.startswith('claude')):
                continue
            if (path/'cwd').resolve(strict=True) != home:
                continue
            row = pane_close.process(int(path.name))
            if row and row['state'] != 'Z':
                found.append(int(path.name))
        except FileNotFoundError:
            pass
        except PermissionError:
            # Kernel forbids other users' processes; they cannot own this
            # private home. Same-user ambiguity is refused below.
            if path.stat().st_uid == os.getuid():
                raise
    require(not found, 'another live session in this home: %s' % found)


def versions(home, harness, backend):
    extension = Path(__file__).resolve().parents[2]
    files = list(Path(__file__).parent.glob('*.py')) + list((extension/'bin').glob('fm-context-relay*'))
    return dict(firstmate=command(['git', '-C', str(home), 'rev-parse', 'HEAD'], home).strip(),
                extension_files={str(path.relative_to(extension)): digest(path) for path in sorted(files)},
                core_files={name: digest(home/'bin'/name) for name in
                    ('fm-session-start.sh', 'fm-lock.sh', 'fm-lock-release.sh', 'fm-lock-release.py',
                     'fm-session-lock-lib.sh', 'fm-primary-scope-lib.sh')},
                harness=command([harness, '--version'], home).strip(),
                herdr=backend.call('status', '--json'))


def version_key(value):
    # Runtime counts and foreground-client presence change during a handoff.
    server = value['herdr'].get('server', {})
    return dict(firstmate=value['firstmate'], core_files=value['core_files'], extension_files=value['extension_files'],
                harness=value['harness'],
                server={k: server.get(k) for k in ('version', 'protocol', 'compatible', 'session')},
                client={k: value['herdr'].get('client', {}).get(k) for k in
                        ('version', 'protocol', 'channel', 'endpoint_protocol_generation')})


def harness_files(home, harness, launch, original):
    key = 'CODEX_HOME' if harness == 'codex' else 'CLAUDE_CONFIG_DIR'
    directory = os.environ.get(key) or str(Path.home()/('.codex' if harness == 'codex' else '.claude'))
    require(launch['env'].get(key) in (None, directory), 'harness configuration/environment mismatch')
    root = Path(directory)
    require(root.is_absolute() and root.resolve() == root, 'noncanonical harness directory')
    paths = [home/'config/umbrales-relevo'] + ([root/'config.toml', root/'hooks.json', home/'.codex/config.toml'] if harness == 'codex'
             else [root/'settings.json', home/'.claude/settings.json', home/'.claude/settings.local.json'])
    if '--settings' in original:
        setting = original[original.index('--settings')+1]
        if not setting.startswith('{'):
            paths.append(Path(setting))
    if launch['env'].get('BASH_ENV'):
        paths.append(Path(launch['env']['BASH_ENV']))
    result = {}
    for path in paths:
        require(path.is_absolute() and path.resolve() == path, 'noncanonical settings')
        result[str(path)] = digest(path) if path.exists() else None
    return result


def capture(home, references=()):
    snapshot = json.loads(command(['bash', str(home/'bin/fm-fleet-snapshot.sh'), '--json'], home))
    require(snapshot.get('fm_home') == str(home), 'snapshot belongs to another home')
    files = {}
    paths = [home/'data/backlog.md', *home.glob('state/*.meta'), *home.glob('state/*.status'),
             *home.glob('state/*.inbox/*.msg'), *home.glob('state/*.inbox/handled/*.msg')]
    for relative in references:
        path = home/relative
        require(isinstance(relative, str) and not Path(relative).is_absolute()
                and home in path.resolve().parents, 'reference outside this home')
        require(path.is_file() and path.resolve() == path, 'referencia ausente/symlink: '+relative)
        paths.append(path)
    # Bounded queue sources, without consuming or acknowledging them.
    paths += [p for p in home.glob('state/.wake*') if p.is_file() and not p.is_symlink()]
    for path in sorted(set(paths)):
        require(path.resolve() == path, 'capture does not follow symlinks: '+str(path))
        data = path.read_bytes()
        require(len(data) <= 8*1024*1024, 'fuente demasiado grande: '+str(path))
        files[str(path.relative_to(home))] = dict(sha256=common.sha256_bytes(data), text=data.decode('utf-8'))
    return dict(at=common.iso(), snapshot=snapshot, files=files)


def validate_package(directory, attempt, check_harness=True):
    package = load(directory/'package.json')
    require(digest(directory/'package.json') == attempt['checkpoint_sha256'], 'package changed')
    require(package['attempt'] == attempt['id'] and package['home'] == attempt['home'], 'paquete ajeno')
    if 'transport_helper_sha256' in package:
        require(package['transport_helper_sha256'] == (digest(attempt['helper']) if attempt.get('helper') else None),
                'transport helper changed')
    for name, sha in package['artifacts'].items():
        require('/' not in name and (directory/name).resolve() == directory/name
                and digest(directory/name) == sha, 'artifact changed or is symlink: '+name)
    for name, sha in (package['harness_files'].items() if check_harness else ()):
        path = Path(name)
        require(path.resolve() == path and (digest(path) if path.exists() else None) == sha,
                'harness configuration changed: '+name)
    return package


def event(directory, kind, **detail):
    common.append_line(str(directory/'events.jsonl'), json.dumps(dict(at=common.iso(), kind=kind, **detail)))
    events.emit(directory.parents[3], kind, directory.name)


def save(directory, attempt, state):
    event(directory, 'transition', before=attempt['state'], after=state)
    attempt.update(state=state, updated_at=common.iso())
    put(directory/'attempt.json', attempt)


def owner_for(home, attempt, backend, outgoing):
    auth = load(Path(attempt['directory'])/'authorization.json')
    owner = pane_close.current_owner(home, auth['harness'], backend, auth['old_pane' if outgoing else 'new_pane'])
    if outgoing:
        require(owner == attempt['outgoing'], 'owner is not this attempt outgoing primary')
    else:
        require(owner['sid'] != auth['old_sid'] and owner['process']['pid'] != auth['old_frontend_pid'], 'successor reused identity')
    return owner


def confirmation(directory, attempt):
    value = load(directory/'confirmation.json')
    require(value['attempt'] == attempt['id'] and value['checkpoint_sha256'] == attempt['checkpoint_sha256']
            and bool(value['confirmation'].strip()), 'current confirmation missing')
    return value


def run(args, paths):
    directory = None
    try:
        home = Path(paths.home)
        require(home.is_absolute() and home.resolve() == home, 'noncanonical home')
        state = Path(paths.state_dir)
        with common.FileLock(str(state/'assisted.lock')):
            if args.action == 'prepare':
                return prepare(args, paths)
            directory = Path(args.handoff)
            require(directory.parent == home/'data/context-relay/attempts' and directory.resolve() == directory,
                    'package outside this home attempt directory')
            attempt = load(directory/'attempt.json')
            require(attempt['home'] == str(home) and attempt['directory'] == str(directory), 'attempt belongs to another home')
            if args.action == 'status':
                value = attempt
                if args.package:
                    package = validate_package(directory, attempt)
                    notes = load(directory/'notes.json')
                    value = dict(successor_prompt=(directory/'successor-prompt.txt').read_text(),
                                 attempt=attempt, notes=notes, package=package,
                                 confirmation=confirmation(directory, attempt) if (directory/'confirmation.json').exists() else None,
                                 operational=load(directory/'operational.json'),
                                 current=capture(home, notes['references']))
                print(json.dumps(value, ensure_ascii=False, indent=2))
                return 0
            return advance(args, paths, directory, attempt)
    except (pane_close.Preserve, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        directory = directory or getattr(args, '_attempt_directory', None)
        if directory and directory.is_dir():
            event(directory, 'refused', action=args.action, reason=str(exc))
        else:
            common.append_line(str(Path(paths.data_dir)/'attempt-events.jsonl'), json.dumps(
                dict(at=common.iso(), kind='refused', action=args.action, reason=str(exc))))
        print(json.dumps(dict(status='preserved', reason=str(exc)), ensure_ascii=False))
        return 1


def prepare(args, paths):
    home = Path(paths.home)
    loaded = config.load(paths)
    mode = loaded.settings.value('relay_mode')
    reservation = getattr(args, 'automatic_reservation', None)
    require(mode == 'assisted' or mode == 'auto' and reservation,
            'relay_mode is not assisted or automatic authority is missing')
    require(loaded.status == 'ok', 'invalid current configuration; do not transfer using fallback')
    backend = pane_close.Backend(args)
    owner = pane_close.current_owner(home, args.harness, backend, os.environ.get('HERDR_PANE_ID'))
    active = Path(paths.state_dir)/'active.json'
    if active.exists():
        previous = Path(load(active)['directory'])
        require(previous.parent == home/'data/context-relay/attempts' and previous.resolve() == previous,
                'barrier refers to an unrelated path')
        prior = load(previous/'attempt.json')
        require(prior['state'] == 'accepted' and load(previous/'successor-ready.json')['owner'] == owner,
                'active attempt already exists; inspect')
    peers(home, args.harness, [owner['process']['pid']])
    sid = owner['sid']
    record = load(Path(paths.sessions)/(args.harness+'-'+sid+'.json'))
    require(record.get('relay', {}).get('pending') is True, 'policy is not awaiting safe point')
    current_policy = session.relay_policy(loaded.settings, args.harness)
    require(current_policy and record['relay'].get('policy') == current_policy
            and record.get('session_id') == sid and record.get('harness') == args.harness
            and str(record.get('owner_pid')) == str(owner['process']['pid']), 'stale request or different owner/policy')
    auto_auth = None
    if mode == 'auto':
        from . import automatic
        auto_auth = automatic.verify_reservation(paths, reservation, record)
    notes = load(Path(args.notes))
    require(all(isinstance(notes.get(k), list) for k in NOTE_KEYS) and isinstance(notes.get('safe_point'), str)
            and len(notes['safe_point'].strip()) >= 20, 'punto seguro incompleto')
    launch = load(Path(args.launch))
    argv = launch.get('argv')
    require(isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv), 'invalid argv')
    no_resume(argv)
    original = argv_of(owner['process']['pid'])
    no_resume(original)
    require(argv[-len(original):] == original, 'launcher does not preserve outgoing harness argv/configuration')
    require(set(launch.get('env', {})) <= ENV_KEYS and all(isinstance(v, str) for v in launch['env'].values()),
            'entorno launcher no permitido')
    for key in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_DISABLE_1M_CONTEXT', 'BASH_ENV',
                'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL', 'CLAUDE_CODE_SUBAGENT_MODEL'):
        require(launch['env'].get(key) == os.environ.get(key), 'harness selection/configuration changed: '+key)
    old, new = backend.terminal(owner['pane']), backend.terminal(args.new_pane)
    require(getattr(args, 'park_after_exit', False) or os.environ.get('FM_CONTEXT_RELAY_PARK_SHELL') == str(old['shell']['pid']),
            'primary did not start through fm-context-relay-shell in its original shell')
    require(old['tab'] == new['tab'] and old['pane'] != new['pane'], 'target is not a sibling pane')
    pane_close.empty_shell(backend, new)
    attempt_id = str(uuid.uuid4())
    directory = home/'data/context-relay/attempts'/attempt_id
    directory.mkdir(parents=True, mode=0o700)
    args._attempt_directory = directory
    auth = dict(home=str(home), harness=args.harness, authority=args.authority, old_sid=sid,
                old_frontend_pid=owner['process']['pid'], old_frontend_start=owner['process']['start'],
                old_pane=old['pane'], new_pane=new['pane'], tab=old['tab'],
                park_after_exit=bool(getattr(args, 'park_after_exit', False)))
    require(args.authority.strip(), 'preparation authority missing')
    put(directory/'authorization.json', auth)
    if auto_auth:
        put(directory/'automatic-authorization.json', auto_auth)
    put(directory/'notes.json', notes)
    put(directory/'launch.json', launch)
    put(directory/'operational.json', capture(home, notes['references']))
    put(directory/'policy-request.json', record)
    version = versions(home, args.harness, backend)
    put(directory/'versions.json', version)
    cli = Path(__file__).resolve().parents[2]/'bin/fm-context-relay'
    prefix = shlex.join([str(cli), '--home', str(home), 'relay', 'accept', '--handoff', str(directory),
                         '--herdr-session', args.herdr_session])
    if args.herdr_lab_helper:
        prefix += ' --herdr-lab-helper '+shlex.quote(args.herdr_lab_helper)
    prompt = ('You are the NEW primary of this Firstmate home. Never resume the old SID.\n'
              'Read the complete package through this admitted read-only command if Read is unavailable:\n'
              +shlex.join([str(cli), '--home', str(home), 'relay', 'status', '--handoff', str(directory), '--package'])+'\n'
              'That status response includes package, notes, operational, current and confirmation. Review those fields directly; use file reads only if information is missing.\n'
              'Compare operational with current and preserve unresolved approvals/blockers.\n'
              'Do no dispatch or queue acknowledgement until you accept with your OWN native authority.\n'
              'Run exactly this command with a concrete recovery review and JSON blockers list:\n'
              +prefix+" --review 'YOUR_REVIEW' --blockers '[]'\n"
              'Replace YOUR_REVIEW with your concrete recovery review (at least 20 characters).\n'
              'Before acceptance use one CLI command at a time, without echo, wrappers or compound shell syntax.\n'
              'After acceptance, continue the pending_work from notes.json exactly once.\n')
    prompt += ('After acceptance you may combine the pending file check and write with pane-close inspect in one shell call. '
               'Visually inspect its screen before your own ready confirmation. Then ready && finalize may share one call.\n')
    common.atomic_write_bytes(str(directory/'successor-prompt.txt'), prompt.encode(), 0o600)
    close_args = argparse.Namespace(action='prepare', handoff=str(directory), herdr_session=args.herdr_session,
                                    herdr_lab_helper=args.herdr_lab_helper)
    # Capture the finalizer before hashing its prompt augmentation.
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        require(pane_close.execute(close_args, paths) == 0, 'close preparation refused')
    put(directory/'pane-close-preparation-result.json', json.loads(output.getvalue()))
    artifacts = {p.name: digest(p) for p in directory.iterdir() if p.is_file() and not p.name.startswith('.')}
    package = dict(schema='fm-assisted-package.v1', attempt=attempt_id, home=str(home), at=common.iso(),
                   artifacts=artifacts, config_sha256=digest(paths.config), native_argv=original,
                   executable_sha256=digest('/proc/%d/exe' % owner['process']['pid']),
                   harness_files=harness_files(home, args.harness, launch, original),
                   version_key=version_key(version), backend=backend.incarnation(), destination=new,
                   transport_helper_sha256=digest(args.herdr_lab_helper) if args.herdr_lab_helper else None)
    put(directory/'package.json', package)
    attempt = dict(schema='fm-assisted-attempt.v1', id=attempt_id, home=str(home), directory=str(directory),
                   state='prepared', outgoing=owner, checkpoint_sha256=digest(directory/'package.json'),
                   created_at=common.iso(), herdr_session=args.herdr_session, helper=args.herdr_lab_helper)
    if auto_auth:
        attempt['automatic_reservation'] = reservation
    put(directory/'attempt.json', attempt)
    put(Path(paths.state_dir)/'active.json', dict(directory=str(directory), attempt=attempt_id))
    event(directory, 'prepared', outgoing=owner)
    print(json.dumps(dict(status='prepared', handoff=str(directory), checkpoint_sha256=attempt['checkpoint_sha256'])))
    return 0


def advance(args, paths, directory, attempt):
    home = Path(paths.home)
    if attempt.get('automatic_reservation') and args.action in ('retire', 'launch', 'recover'):
        from . import automatic
        current = automatic.authority(paths)
        require(current['id'] == load(directory/'automatic-authorization.json')['id'],
                'automatic authority changed; do not act')
    package = validate_package(directory, attempt, check_harness=args.action != 'abort')
    require(args.herdr_session == attempt['herdr_session'] and args.herdr_lab_helper == attempt['helper'], 'attempt backend changed')
    backend = pane_close.Backend(args)
    require(backend.incarnation() == package['backend'], 'backend incarnation changed')
    if args.action == 'abort':
        require(attempt['state'] in ('prepared', 'confirmed'), 'retirement/launch started; no automatic rollback')
        owner_for(home, attempt, backend, True)
        active = Path(paths.state_dir)/'active.json'
        pointer = load(active)
        require(pointer.get('directory') == str(directory) and pointer.get('attempt') == attempt['id'],
                'otro intento activo; no retirar su barrera')
        save(directory, attempt, 'aborted')
        active.unlink()
        print(json.dumps(dict(status='aborted', handoff=str(directory))))
        return 0
    require(digest(paths.config) == package['config_sha256'], 'configuration changed')
    require(version_key(versions(home, load(directory/'authorization.json')['harness'], backend)) == package['version_key'], 'versiones cambiaron')
    if args.action == 'confirm':
        require(attempt['state'] in ('prepared', 'confirmed'), 'confirmation out of phase')
        require(args.checkpoint_sha256 == attempt['checkpoint_sha256'] and len(args.confirmation.strip()) >= 20,
                'insufficient confirmation/hash')
        target = directory/'confirmation.json'
        value = dict(attempt=attempt['id'], checkpoint_sha256=args.checkpoint_sha256,
                     confirmation=args.confirmation.strip())
        if target.exists():
            require(load(target) == value, 'a different confirmation is already recorded')
        else:
            put(target, value)
        if attempt['state'] != 'confirmed':
            save(directory, attempt, 'confirmed')
    elif args.action == 'retire':
        confirmation(directory, attempt)
        require(attempt['state'] == 'confirmed', 'retirement out of phase')
        owner_for(home, attempt, backend, True)
        if attempt.get('automatic_reservation'):
            from . import automatic, automatic_preflight
            automatic.verify_reservation(paths, attempt['automatic_reservation'], load(directory/'policy-request.json'))
            require(load(directory/'notification-armed.json')['state'] in ('armed', 'disabled'), 'alert arming missing')
            automatic_preflight.verify(args, paths, directory, attempt)
        auth = load(directory/'authorization.json')
        peers(home, auth['harness'], [auth['old_frontend_pid']])
        output = command(['bash', str(home/'bin/fm-lock-release.sh'), '--authority', auth['authority'],
                          '--receipt', str(directory/'lock-release')], home)
        result = json.loads(output)
        require(result.get('state') in ('retained', 'released', 'already-released'), 'release unconfirmed')
        put(directory/'outgoing-retired.json', dict(schema='fm-assisted-retirement.v1', attempt=attempt['id'],
            home=str(home), sid=auth['old_sid'], role='read-only retired primary', successor_pane=auth['new_pane'],
            checkpoint_sha256=attempt['checkpoint_sha256'], release=result,
            state='awaiting-native-exit', at=common.iso()))
        save(directory, attempt, 'retired-awaiting-exit')
    elif args.action == 'launch':
        confirmation(directory, attempt)
        if attempt['state'] in ('launch-intent', 'launch-uncertain', 'launched', 'accepted', 'recovery-blocked'):
            print(json.dumps(dict(status=attempt['state'], no_resubmission=True)))
            return 0
        require(attempt['state'] == 'retired-awaiting-exit', 'outgoing retirement missing')
        from . import launch_once
        launch_once.submit(args, paths, directory, attempt, package)
    elif args.action == 'recover':
        confirmation(directory, attempt)
        if attempt.get('recoveries', 0):
            print(json.dumps(dict(status=attempt['state'], no_resubmission=True, recoveries=attempt['recoveries'])))
            return 0
        require(attempt['state'] in ('launch-uncertain', 'launched', 'launch-failed'),
                'recovery requires a failed or completed launch')
        from . import launch_once
        launch_once.submit(args, paths, directory, attempt, package, recovery=True)
    elif args.action == 'accept':
        confirmation(directory, attempt)
        require(attempt['state'] in ('launched', 'launch-intent', 'launch-uncertain', 'accepted', 'recovery-blocked',
                                     'recovery-intent', 'recovery-launched', 'recovery-uncertain'), 'successor out of phase')
        require(pane_close.process(attempt['outgoing']['process']['pid']) is None, 'outgoing frontend still alive')
        owner = owner_for(home, attempt, backend, False)
        require(argv_of(owner['process']['pid']) == package['native_argv'], 'successor harness/configuration changed')
        require(digest('/proc/%d/exe' % owner['process']['pid']) == package['executable_sha256'], 'successor executable changed')
        peers(home, load(directory/'authorization.json')['harness'], [owner['process']['pid']])
        if attempt['state'] == 'accepted':
            require(load(directory/'successor-ready.json')['owner'] == owner, 'acceptance belongs to another successor')
        else:
            blockers = json.loads(args.blockers)
            require(isinstance(blockers, list) and len(args.review.strip()) >= 20, 'recovery/blocker review missing')
            notes = load(directory/'notes.json')
            # Receiving the role preserves task holds without declaring them
            # resolved. Only missing recovery facts hold the whole admission gate.
            inherited = notes['blockers'] + notes['uncertain_operations']
            recovery = capture(home, notes['references'])
            prior = load(directory/'operational.json')['files']
            recovery['changed_sources'] = sorted(k for k in set(prior)|set(recovery['files']) if prior.get(k) != recovery['files'].get(k))
            recovery.update(review=args.review, blockers=blockers, inherited_blockers=inherited,
                            attempt=attempt['id'], owner=owner,
                            checkpoint_sha256=attempt['checkpoint_sha256'])
            put(directory/'recovery.json', recovery)
            put(directory/'successor-ready.json', dict(attempt=attempt['id'], home=str(home), at=common.iso(),
                checkpoint_sha256=attempt['checkpoint_sha256'], sid=owner['sid'], pid=owner['process']['pid'],
                process_start=owner['process']['start'], pane=owner['pane'], owner=owner,
                recovery_sha256=digest(directory/'recovery.json'), blockers=blockers, inherited_blockers=inherited))
            auth = load(directory/'authorization.json')
            if not blockers:
                put(directory/'successor-accepted.json', dict(attempt=attempt['id'], home=str(home), at=common.iso(),
                    checkpoint_sha256=attempt['checkpoint_sha256'], old_sid=auth['old_sid'],
                    old_frontend_pid=auth['old_frontend_pid'], old_pane=auth['old_pane'],
                    successor_pid=owner['process']['pid'], successor_sid=owner['sid'], successor_start=owner['process']['start'],
                    ready_sha256=digest(directory/'successor-ready.json'), blockers=blockers, inherited_blockers=inherited))
            save(directory, attempt, 'recovery-blocked' if blockers else 'accepted')
            if not blockers and attempt.get('automatic_reservation'):
                from . import automatic, relay_notifications
                automatic.update(paths, attempt['automatic_reservation'], 'accepted', accepted=True,
                                 handoff=str(directory), successor=owner)
                relay_notifications.disarm(paths, directory, attempt)
    else:
        raise pane_close.Preserve('unknown action')
    print(json.dumps(dict(status=attempt['state'], handoff=str(directory))))
    return 0


def guard(paths, payload, harness=None):
    """Return None to allow, otherwise a denial reason. Never infers leadership."""
    active = Path(paths.state_dir)/'active.json'
    if not active.exists():
        return None
    pointer = load(active)
    directory = Path(pointer['directory'])
    require(directory.parent == Path(paths.home)/'data/context-relay/attempts', 'barrier refers to an unrelated path')
    attempt = load(directory/'attempt.json')
    require(pointer.get('attempt') == attempt['id'] and attempt['home'] == paths.home, 'barrier belongs to another attempt/home')
    if attempt['state'] == 'aborted':
        return None
    attempt_harness = load(directory/'authorization.json')['harness']
    harness = harness or attempt_harness
    sid, pid, _, cwd, _, _ = native(Path(paths.home), harness, payload)
    if cwd != Path(paths.home):
        # User-level hooks also run for real workers. A different native cwd
        # cannot become this home primary by inheriting its hook entries.
        return None
    if harness != attempt_harness:
        return 'Relay in progress: another harness cannot act as this home primary.'
    if attempt['state'] == 'accepted':
        ready = load(directory/'successor-ready.json')
        if (sid, pid, str(cwd)) == (ready['sid'], ready['pid'], paths.home):
            require((Path(paths.home)/'state/.lock').read_text().strip() == str(pid), 'successor lost lock')
            require((Path(paths.home)/'state/.session-start-complete').read_text().strip() == str(pid), 'successor startup incomplete')
            require(pane_close.process(pid)['start'] == ready['process_start'], 'successor PID reused')
            return None
    tool = payload.get('tool_name', '')
    if tool in ('Read', 'Glob', 'Grep', 'read_file', 'list_dir'):
        return None
    info = payload.get('tool_input') or {}
    text = info.get('command', info.get('cmd', '')) if isinstance(info, dict) else ''
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=';&|<>()')
        lexer.whitespace_split = True
        lexer.commenters = ''
        parts = list(lexer)
    except ValueError:
        parts = []
    cli = str(Path(__file__).resolve().parents[2]/'bin/fm-context-relay')
    prefix = [cli, '--home', paths.home, 'relay']
    if parts[:4] == prefix and len(parts) > 4 and parts[4] in ('start', 'accept', 'retire', 'abort', 'status'):
        # argparse validates all flags. Reject compound shell grammar, including
        # substitutions hidden inside a quoted review argument. Quoted
        # punctuation within a review is ordinary argv text, not shell syntax.
        operators = ';&|<>()'
        if (not any(c in text for c in ('\n', '`', '$'))
                and not any(token and all(c in operators for c in token) for token in parts)):
            return None
    return 'Relay in progress: dispatch, mutations and queue acknowledgment blocked until own acceptance; use relay status.'


def run_guard(args, paths):
    try:
        reason = guard(paths, json.load(sys.stdin), args.harness)
    except Exception as exc:
        common.log_error(paths, 'PreToolUse', str(exc))
        return 0  # A broken extension must not block the primary's tools.
    if reason:
        print(json.dumps(dict(hookSpecificOutput=dict(hookEventName='PreToolUse', permissionDecision='deny',
                                                      permissionDecisionReason=reason))), file=sys.stderr)
        return 2
    return 0


def add_parser(sub):
    parser = sub.add_parser('relay', help='explicit assisted relay attempt', description=HELP,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_subparsers(dest='action', required=True)
    from . import start, launch_once
    start.add_parser(actions)
    launch_once.add_parser(actions)
    for action in ('prepare', 'confirm', 'retire', 'launch', 'recover', 'accept', 'abort', 'status', 'guard'):
        p = actions.add_parser(action, description=HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
        if action == 'guard':
            p.add_argument('--harness', choices=('codex', 'claude'))
            p.set_defaults(func=run_guard)
            continue
        if action == 'prepare':
            p.add_argument('--harness', required=True, choices=('codex', 'claude'))
            for key in ('notes', 'launch', 'new-pane', 'authority'):
                p.add_argument('--'+key, required=True)
        else:
            p.add_argument('--handoff', required=True)
        if action != 'status':
            p.add_argument('--herdr-session', required=True)
            p.add_argument('--herdr-lab-helper')
        if action == 'confirm':
            p.add_argument('--checkpoint-sha256', required=True)
            p.add_argument('--confirmation', required=True)
        if action == 'accept':
            p.add_argument('--review', required=True)
            p.add_argument('--blockers', required=True)
        if action == 'status':
            p.add_argument('--package', action='store_true')
        p.set_defaults(func=run)
