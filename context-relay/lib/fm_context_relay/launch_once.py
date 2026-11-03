"""Attempt-local execution fence for a delayed Herdr launch and one recovery.

This serializes process creation, not leadership: only Firstmate startup owns
the home lock. A superseded queued command cannot start a stale generation.
The fence is held by the launcher parent until the native frontend exits.
"""

import argparse
import os
from pathlib import Path
import shlex
import subprocess

from . import assisted, automatic, common, herdr_session, pane_close

require = pane_close.require


def free_home(home, attempt, harness):
    require(pane_close.process(attempt['outgoing']['process']['pid']) is None,
            'outgoing frontend still alive; no recuperar')
    assisted.peers(home, harness)
    # Inspect only. Normal startup owns stale-lock handling; never unlink here.
    lock = home/'state/.lock'
    if lock.exists():
        text = lock.read_text().strip()
        require(text.isdigit() and pane_close.process(int(text)) is None,
                'lock belongs to a live session or is ambiguous')
    require(not (home/'state/.lock.acquire').exists(), 'lock acquisition in progress')


def preflight(args, paths, directory, attempt, package):
    home = Path(paths.home)
    auth = assisted.load(directory/'authorization.json')
    free_home(home, attempt, auth['harness'])
    backend = pane_close.Backend(args)
    require(backend.incarnation() == package['backend'], 'backend incarnation changed')
    require(assisted.digest(paths.config) == package['config_sha256'], 'configuration changed')
    require(assisted.version_key(assisted.versions(home, auth['harness'], backend)) == package['version_key'],
            'versiones cambiaron')
    require(backend.terminal(auth['new_pane']) == package['destination'], 'target changed')
    pane_close.empty_shell(backend, package['destination'])
    return backend, auth


def submit(args, paths, directory, attempt, package, recovery=False):
    """Caller holds assisted.lock. Record generation BEFORE sending any text."""
    backend, auth = preflight(args, paths, directory, attempt, package)
    with common.FileLock(str(directory/'.launch-execution.lock'), timeout=0.2):
        target = directory/'launch-generation.json'
        previous = common.read_json(str(target))
        if recovery:
            if previous is None:
                require(attempt['state'] == 'launch-failed' and not (directory/'launch-submitted.json').exists(),
                        'rejection before original delivery is unproven')
                previous = dict(generation=1)
            require(previous and previous.get('generation') == 1, 'recovery already consumed or original launch missing')
            require(attempt.get('recoveries', 0) == 0, 'only one recovery allowed')
            attempt['recoveries'] = 1
            generation = 2
        else:
            require(previous is None, 'launch already reserved')
            generation = 1
        automatic.write(target, dict(generation=generation, attempt=attempt['id'], at=common.iso()))
        assisted.save(directory, attempt, 'recovery-intent' if recovery else 'launch-intent')
    launch = assisted.load(directory/'launch.json')
    env = dict(launch['env'], FM_HOME=paths.home, FM_ROOT_OVERRIDE=paths.home,
               HERDR_ENV='1', HERDR_SESSION=args.herdr_session, HERDR_PANE_ID=auth['new_pane'],
               HERDR_TAB_ID=auth['tab'], HERDR_WORKSPACE_ID=package['destination']['workspace'],
               HERDR_SOCKET_PATH=package['backend']['socket'],
               FM_CONTEXT_RELAY_PARK_SHELL=str(package['destination']['shell']['pid']))
    root = Path(__file__).resolve().parents[2]
    argv = [str(root/'bin/fm-context-relay'), '--home', paths.home, 'relay', 'exec-launch',
            '--handoff', str(directory), '--generation', str(generation), '--herdr-session', args.herdr_session]
    if args.herdr_lab_helper:
        argv += ['--herdr-lab-helper', args.herdr_lab_helper]
    cmd = shlex.join(['source', str(root/'bin/fm-context-relay-shell'), '--home', paths.home, '--',
                     'env', '-i', *[k+'='+v for k, v in env.items()], *argv])
    try:
        p = backend.invoke(['pane', 'run', auth['new_pane'], cmd])
        require(p.returncode == 0, 'Herdr refused delivery: '+p.stderr[-500:])
        automatic.write(directory/('recovery-submitted.json' if recovery else 'launch-submitted.json'),
                        dict(at=common.iso(), generation=generation, rc=p.returncode,
                             stdout=p.stdout, stderr=p.stderr, pane=auth['new_pane'],
                             command_sha256=common.sha256_bytes(cmd.encode())))
    except Exception:
        assisted.save(directory, attempt, 'recovery-uncertain' if recovery else 'launch-uncertain')
        raise
    assisted.save(directory, attempt, 'recovery-launched' if recovery else 'launched')


def execute(args, paths):
    """Called in destination terminal, without inherited native session ID."""
    directory = Path(args.handoff)
    home = Path(paths.home)
    try:
        require(directory.parent == home/'data/context-relay/attempts' and directory.resolve() == directory,
                'package outside home')
        with common.FileLock(str(directory/'.launch-execution.lock'), timeout=0.2):
            attempt = assisted.load(directory/'attempt.json')
            require(attempt['home'] == paths.home and attempt['directory'] == str(directory), 'unrelated attempt')
            generation = assisted.load(directory/'launch-generation.json')
            require(generation.get('attempt') == attempt['id'] and generation.get('generation') == args.generation,
                    'launch command superseded: do not start')
            receipt = directory/('launch-execution-%d.json' % args.generation)
            require(not receipt.exists(), 'generation already executed; do not repeat startup')
            package = assisted.validate_package(directory, attempt)
            assisted.confirmation(directory, attempt)
            require(attempt['state'] in ('launch-intent', 'launch-uncertain', 'launched',
                                        'recovery-intent', 'recovery-uncertain', 'recovery-launched'), 'execution out of phase')
            require(herdr_session.normalize(args.herdr_session) == herdr_session.normalize(attempt['herdr_session'])
                    and args.herdr_lab_helper == attempt['helper'],
                    'backend ajeno')
            # empty_shell cannot hold while this very launcher is foreground.
            # Kernel/native ownership and package checks are still repeated.
            auth = assisted.load(directory/'authorization.json')
            free_home(home, attempt, auth['harness'])
            require(os.environ.get('HERDR_PANE_ID') == auth['new_pane'], 'launcher en otro panel')
            backend = pane_close.Backend(args)
            require(backend.incarnation() == package['backend'], 'backend incarnation changed')
            require(backend.terminal(auth['new_pane']) == package['destination'], 'target terminal changed')
            require(assisted.version_key(assisted.versions(home, auth['harness'], backend)) == package['version_key'],
                    'versiones cambiaron')
            if (directory/'automatic-authorization.json').exists():
                require(automatic.authority(paths)['id'] == assisted.load(directory/'automatic-authorization.json')['id'],
                        'automatic authority changed before execution')
            require(assisted.digest(paths.config) == package['config_sha256'], 'configuration changed')
            launch = assisted.load(directory/'launch.json')
            status = dict(at=common.iso(), generation=args.generation, state='starting', attempt=attempt['id'])
            automatic.write(receipt, status)
            try:
                child = subprocess.Popen(launch['argv'], cwd=home)
                status.update(state='running', launcher_pid=os.getpid(), child_pid=child.pid)
                automatic.write(receipt, status)
                rc = child.wait()
                status.update(state='exited', rc=rc, ended_at=common.iso())
                automatic.write(receipt, status)
                return rc
            except OSError as exc:
                status.update(state='failed', error=str(exc), ended_at=common.iso())
                automatic.write(receipt, status)
                return 1
    except (pane_close.Preserve, OSError, ValueError, common.LockTimeout) as exc:
        print('Launch preserved: '+str(exc), flush=True)
        return 1


def add_parser(actions):
    p = actions.add_parser('exec-launch', help=argparse.SUPPRESS)
    p.add_argument('--handoff', required=True)
    p.add_argument('--generation', required=True, type=int, choices=(1, 2))
    p.add_argument('--herdr-session', required=True)
    p.add_argument('--herdr-lab-helper')
    p.set_defaults(func=execute)
