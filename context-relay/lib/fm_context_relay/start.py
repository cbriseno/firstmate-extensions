"""Operator entrypoint; the existing assisted engine owns every transition.

The detached driver submits exit/prompt once, never fabricates native receipts,
never signals a frontend, and never reclaims a lock. Hooks never call this file.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

from . import assisted as relay, automatic, automatic_preflight, common, config, herdr_session, pane_close, relay_notifications, session

HELP = """Guided entry: fm-context-relay relay start.
The native outgoing owner reconciles operations and writes safe-point.json using
the relay prepare notes format plus owner_sid and owner_pid. relay start [--notes
PATH] [--harness claude|codex] requires an unambiguous native owner, assisted mode,
current pending policy and complete notes before creating a same-tab empty sibling.
It preserves argv/configuration and shows summary, package and hash without retiring.
The operator then confirms ONCE with the same command plus --confirm HASH and
--confirmation TEXT. A relay request alone does not authorize retirement. Non-TTY
returns awaiting-confirmation; a TTY offers confirmation or abort. --abort preserves
the outgoing owner and package before retirement. Options cannot mutate a prepared
package. Supported retirement is followed by a separate driver observing a NEW Stop
before sending /exit once. Real exit and the parked original shell are verified.
The same harness starts fresh and receives the package once when its native TUI is
ready. Own startup, lock and explicit successor acceptance are required. Trust,
permissions and provider questions are never auto-accepted. Only the verified empty
retired panel may be finalized. Timeouts preserve the attempt; do not resend /exit,
launch or prompt, forge acceptance, or revive the outgoing SID.
With --automatic, use only current durable auto authorize authority, never invented
operator words. Notes additionally include useful_work and automatic_safe_point with
decisions_saved/own_operations_reconciled=true, and captain_call_in_progress,
landing_in_progress, critical_operation_in_progress=false. Each live worker must
acknowledge the exact pause key. Unknown models, incomplete measurements, expired
limits/deadlines and revoked authority refuse admission. The system binds confirmation
to authority and uses the same guarded protocol. Full preflight/alert arming precedes
retirement; one fresh recovery is allowed after a post-retirement failure. No shared
daemon or unrelated session is modified."""
require = pane_close.require
load = pane_close.load
EXT = Path(__file__).resolve().parents[2]


def invoke(action, paths, args, directory=None, **extra):
    value = argparse.Namespace(action=action, herdr_session=args.herdr_session,
                               herdr_lab_helper=args.herdr_lab_helper, **extra)
    if directory:
        value.handoff = str(directory)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        rc = relay.run(value, paths)
    result = json.loads(output.getvalue())
    require(rc == 0, result.get('reason', 'transition refused'))
    return result


def identify(home, harness, backend):
    found = []
    for name in ((harness,) if harness else ('claude', 'codex')):
        try:
            owner = pane_close.current_owner(home, name, backend, os.environ.get('HERDR_PANE_ID'))
            found.append((name, owner))
        except (pane_close.Preserve, OSError, ValueError, RuntimeError):
            pass
    require(len(found) == 1, 'unambiguous native primary owner required; workers and external terminals cannot start relay')
    return found[0]


def preflight(paths, args):
    home = Path(paths.home)
    loaded = config.load(paths)
    if getattr(args, 'automatic', False):
        automatic.authority(paths)
    else:
        require(loaded.settings.value('relay_mode') == 'assisted', 'relay_mode is not assisted; no relay started')
    require(loaded.status == 'ok' and not loaded.errors, 'invalid configuration; repair before relay')
    backend = pane_close.Backend(args)
    harness, owner = identify(home, args.harness, backend)
    if getattr(args, 'automatic', False):
        _, _, _, _, shared, _ = relay.native(home, harness)
        original = relay.argv_of(owner['process']['pid'])
        require(not shared and not any(a in ('exec', 'app-server') for a in original[1:]),
                'auto requires verified TUI primary; shared/headless runtime unsupported')
        require(harness != 'claude' or not any(a == '-p' or a.startswith('--print') for a in original[1:]),
                'auto does not support Claude print mode')
    relay.peers(home, harness, [owner['process']['pid']])
    record = load(Path(paths.sessions)/(harness+'-'+owner['sid']+'.json'))
    require(record.get('relay', {}).get('pending') is True, 'no pending policy; continue current session')
    require(record['relay'].get('policy') == session.relay_policy(loaded.settings, harness)
            and str(record.get('owner_pid')) == str(owner['process']['pid']), 'stale request or different owner/policy')
    return backend, harness, owner, record


def preview(paths, args):
    backend, harness, owner, record = preflight(paths, args)
    home = Path(paths.home)
    notes_path = Path(args.notes or Path(paths.state_dir)/'safe-point.json')
    require(notes_path.is_file(), 'safe point unverified: notes missing; primary must prepare safe-point.json with its SID/PID')
    notes = load(notes_path)
    require(all(isinstance(notes.get(k), list) for k in relay.NOTE_KEYS)
            and isinstance(notes.get('safe_point'), str) and len(notes['safe_point'].strip()) >= 20
            and notes.get('owner_sid') == owner['sid'] and str(notes.get('owner_pid')) == str(owner['process']['pid']),
            'safe point unverified: notes incomplete or from another session')
    reservation = None
    if getattr(args, 'automatic', False):
        # Admission happens before a pane or an assisted attempt is created.
        operational = relay.capture(home, notes['references'])
        reservation = automatic.reserve(paths, record, notes, operational)
        args.automatic_reservation = reservation['id']
    original = relay.argv_of(owner['process']['pid'])
    relay.no_resume(original)
    launch = dict(argv=original, env={k:v for k,v in os.environ.items() if k in relay.ENV_KEYS})
    # Capture and validate real config before creating any pane.
    relay.harness_files(home, harness, launch, original)
    old = backend.terminal(owner['pane'])
    wrapped = os.environ.get('FM_CONTEXT_RELAY_PARK_SHELL') == str(old['shell']['pid'])
    # Fail before retire if the terminal cannot provide identity/input evidence.
    pane_close.input_counters(old['shell'])
    new = backend.call('pane', 'split', owner['pane'], '--direction', 'right', '--cwd', str(home), '--no-focus')['pane']
    new_pane = new['pane_id']
    # A new shell may need a short interval to reach its prompt, never send text
    # until the standard kernel predicate proves it is the sole process.
    until(lambda: shell_ready(backend, new_pane), 'sibling pane is not empty', seconds=10)
    scratch = Path(paths.state_dir)/'start-launch.json'
    relay.put(scratch, launch)
    result = invoke('prepare', paths, args, harness=harness, notes=str(notes_path), launch=str(scratch),
                    new_pane=new_pane, authority='Explicit primary safe point; operator confirmation pending',
                    park_after_exit=not wrapped,
                    automatic_reservation=reservation['id'] if reservation else None)
    directory = Path(result['handoff'])
    relay.put(directory/'start.json', dict(stop_nonce=record.get('last_stop_nonce'),
        owner=owner, park_after_exit=not wrapped))
    if reservation:
        automatic.update(paths, reservation['id'], 'prepared', handoff=str(directory))
    return directory


def shell_ready(backend, pane):
    try:
        terminal = backend.terminal(pane)
        pane_close.empty_shell(backend, terminal)
        return terminal
    except (pane_close.Preserve, OSError):
        return None


def summary(directory):
    attempt = load(directory/'attempt.json')
    package = relay.validate_package(directory, attempt)
    auth = load(directory/'authorization.json')
    notes = load(directory/'notes.json')
    return dict(status='awaiting-confirmation', handoff=str(directory), checkpoint_sha256=attempt['checkpoint_sha256'],
        summary='This session retires, the same harness opens with fresh identity, and the retired pane closes ONLY after own acceptance.',
        preserves='Pending work, decisions, instructions, blockers, uncertainties, references, state, queue and inbox; held tasks remain unapproved.',
        created_pane=auth['new_pane'], pane_to_close=auth['old_pane'],
        pending_work=notes['pending_work'], blockers=notes['blockers'], uncertain_operations=notes['uncertain_operations'],
        safe_point=notes['safe_point'], policy=load(directory/'policy-request.json')['relay'],
        references=notes['references'], artifacts=package['artifacts'],
        confirm_command='relay start --confirm '+attempt['checkpoint_sha256']+" --confirmation 'explicit operator words after reading this summary'",
        abort_command='relay start --abort')


def start(args, paths):
    directory = None
    spawned = False
    try:
        require(not (args.abort and (args.confirm or args.confirmation)), '--abort cannot be combined with confirmation')
        if args.herdr_session is None:
            args.herdr_session = os.environ.get('HERDR_SESSION')
        args.herdr_session = herdr_session.normalize(args.herdr_session)
        with common.FileLock(str(Path(paths.state_dir)/'start.lock')):
            active = Path(paths.state_dir)/'active.json'
            if active.exists():
                pointer = load(active)
                directory = Path(pointer['directory'])
                require(directory.parent == Path(paths.home)/'data/context-relay/attempts' and directory.resolve() == directory,
                        'attempt belongs to another home; inspect')
                attempt = load(directory/'attempt.json')
                require(pointer.get('attempt') == attempt['id'], 'barrier does not match attempt')
                if attempt['state'] == 'accepted' and not (args.confirm or args.confirmation or args.abort):
                    directory = preview(paths, args)
                    attempt = load(directory/'attempt.json')
                require((directory/'start.json').exists(), 'manual attempt exists; use relay status, do not start another')
                # Infer backend from this caller, never follow foreign endpoints.
                backend = pane_close.Backend(args)
                relay.owner_for(Path(paths.home), attempt, backend, True)
                if getattr(args, 'automatic', False) and attempt.get('automatic_reservation') and attempt['state'] not in ('prepared', 'confirmed'):
                    print(json.dumps(dict(status=attempt['state'], no_resubmission=True)))
                    return 0
                require(attempt['state'] in ('prepared', 'confirmed'), 'retirement started; do not repeat; inspect relay status and driver.json')
            else:
                require(not args.confirm and not args.confirmation and not args.abort, 'no prepared package to confirm or abort')
                directory = preview(paths, args)
                attempt = load(directory/'attempt.json')
            if args.abort:
                result = invoke('abort', paths, args, directory)
                print(json.dumps(dict(result, summary='Aborted before retirement; owner, context and package preserved.'), ensure_ascii=False))
                return 0
            view = summary(directory)
            if getattr(args, 'automatic', False):
                require(attempt.get('automatic_reservation'), 'assisted package does not authorize automatic confirmation')
                auth = automatic.verify_reservation(paths, attempt['automatic_reservation'], load(directory/'policy-request.json'))
                require(load(directory/'automatic-authorization.json') == auth, 'authority changed after preparation')
                args.confirm = attempt['checkpoint_sha256']
                args.confirmation = 'System confirmation under durable authority '+auth['id']+': '+auth['captain_words']
                print('Automatic relay: safe point saved; session will transfer to the same harness.', flush=True)
            if not args.confirmation:
                print(json.dumps(view, ensure_ascii=False, indent=2), flush=True)
                if not sys.stdin.isatty():
                    return 0
                answer = input('Confirm: yes '+attempt['checkpoint_sha256']+' (any other answer aborts): ').strip()
                if answer != 'yes '+attempt['checkpoint_sha256']:
                    invoke('abort', paths, args, directory)
                    print('Aborted before retirement; session retains authority and context.')
                    return 0
                args.confirmation, args.confirm = answer, attempt['checkpoint_sha256']
            require(args.confirm == attempt['checkpoint_sha256'] and len(args.confirmation.strip()) >= 20,
                    'confirmation missing current hash; read summary and use --confirm HASH --confirmation QUOTE')
            invoke('confirm', paths, args, directory, checkpoint_sha256=args.confirm, confirmation=args.confirmation)
            if getattr(args, 'automatic', False):
                attempt = load(directory/'attempt.json')
                automatic_preflight.verify(args, paths, directory, attempt)
                relay_notifications.arm(paths, directory, attempt)
                relay.put(directory/'automatic-confirmation.json', dict(at=common.iso(),
                    authorization=auth['id'], checkpoint_sha256=args.confirm, kind='system-standing-authorization'))
            require(not (directory/'driver-intent.json').exists(), 'driver already sent; inspect, do not repeat')
            auth = load(directory/'authorization.json')
            latest = load(Path(paths.sessions)/(auth['harness']+'-'+auth['old_sid']+'.json'))
            relay.put(directory/'driver-intent.json', dict(at=common.iso(), checkpoint_sha256=args.confirm,
                                                          stop_nonce=latest.get('last_stop_nonce'),
                                                          user_prompt_nonce=latest.get('last_user_prompt_nonce')))
            argv = [str(EXT/'bin/fm-context-relay'), '--home', paths.home, 'relay', '_drive',
                    '--handoff', str(directory), '--herdr-session', args.herdr_session]
            if args.herdr_lab_helper:
                argv += ['--herdr-lab-helper', args.herdr_lab_helper]
            with (directory/'driver.log').open('a') as log:
                proc = subprocess.Popen(argv, cwd=paths.home, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                        start_new_session=True, close_fds=True)
                spawned = True
            relay.put(directory/'driver-process.json', dict(pid=proc.pid, at=common.iso()))
            until(lambda:(directory/'driver-ready.json').exists(), 'driver not ready; outgoing owner preserved', seconds=10)
            result = invoke('retire', paths, args, directory)
            if attempt.get('automatic_reservation'):
                automatic.update(paths, attempt['automatic_reservation'], 'retired-awaiting-exit', handoff=str(directory))
            print(json.dumps(dict(result, handoff=str(directory),
                summary='Retirement recorded. End this turn without further operations; driver waits for Stop, sends /exit and delivers package. Run no more relay commands.'), ensure_ascii=False))
            return 0
    except (pane_close.Preserve, OSError, ValueError, KeyError, TypeError, RuntimeError, EOFError, subprocess.SubprocessError) as exc:
        if directory and directory.is_dir():
            if spawned:
                relay.put(directory/'driver-cancel.json', dict(reason=str(exc), at=common.iso()))
            relay.event(directory, 'start-refused', reason=str(exc))
            if getattr(args, 'automatic', False):
                attempt = load(directory/'attempt.json')
                if attempt['state'] in ('prepared', 'confirmed'):
                    try:
                        invoke('abort', paths, args, directory)
                    except (pane_close.Preserve, OSError, ValueError):
                        pass
                relay_notifications.emit(paths, directory, attempt, str(exc),
                    'Outgoing owner retains control before retirement. Inspect package and switch to assisted or warn.')
                automatic.update(paths, attempt['automatic_reservation'], 'failed-before-retirement', reason=str(exc))
        elif getattr(args, 'automatic_reservation', None):
            automatic.update(paths, args.automatic_reservation, 'failed-before-preparation', reason=str(exc))
            automatic.record_failure(paths, args.automatic_reservation, str(exc),
                'Outgoing owner retains control. Inspect failure and use assisted or warn before another attempt.')
        print(json.dumps(dict(status='preserved', reason=str(exc)), ensure_ascii=False))
        return 1


def until(predicate, reason, seconds=120):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(.2)
    raise pane_close.Preserve(reason+'; attempt preserved, no redelivery')


def send_once(backend, directory, name, pane, text):
    target = directory/(name+'-intent.json')
    require(not target.exists(), name+' already attempted; do not resend')
    relay.put(target, dict(at=common.iso(), pane=pane, text_sha256=common.sha256_bytes(text.encode())))
    # Herdr's agent transport rejects a blocked permission/trust/question UI
    # before writing any input. Never fall back to raw terminal input here.
    result = backend.invoke(['agent', 'prompt', pane, text])
    require(result.returncode == 0, name+': delivery refused or uncertain: '+result.stderr[-400:])
    relay.put(directory/(name+'-submitted.json'), dict(at=common.iso(), pane=pane))


def drive(args, paths):
    directory = Path(args.handoff)
    is_auto = False
    try:
        require(directory.parent == Path(paths.home)/'data/context-relay/attempts' and directory.resolve() == directory,
                'driver outside home')
        with common.FileLock(str(directory/'.driver.lock')):
            attempt = load(directory/'attempt.json')
            is_auto = bool(attempt.get('automatic_reservation'))
            def enabled():
                if is_auto:
                    current = automatic.authority(paths)
                    require(current['id'] == load(directory/'automatic-authorization.json')['id'],
                            'automatic authority changed; preserve attempt')
                    original = load(directory/'authorization.json')
                    record = load(Path(paths.sessions)/(original['harness']+'-'+original['old_sid']+'.json'))
                    require(record.get('last_user_prompt_nonce') == load(directory/'driver-intent.json').get('user_prompt_nonce'),
                            'new operator input; stop relay and preserve state')
                return True
            def wait(predicate, reason, seconds=120):
                def checked():
                    enabled()
                    return predicate()
                return until(checked, reason, seconds)
            enabled()
            package = relay.validate_package(directory, attempt)
            require(attempt['home'] == paths.home and herdr_session.normalize(args.herdr_session) == herdr_session.normalize(attempt['herdr_session'])
                    and args.herdr_lab_helper == attempt['helper'], 'driver belongs to another home/backend')
            relay.confirmation(directory, attempt)
            backend = pane_close.Backend(args)
            require(backend.incarnation() == package['backend'], 'backend changed')
            auth, start_record = load(directory/'authorization.json'), load(directory/'start.json')
            require(not (directory/'driver.json').exists(), 'driver already executed; do not repeat')
            relay.put(directory/'driver-ready.json', dict(at=common.iso()))
            def retired():
                require(not (directory/'driver-cancel.json').exists(), 'start canceled; do not retire or launch')
                return (directory/'outgoing-retired.json').exists()
            wait(retired, 'retiro no confirmado')
            record_path = Path(paths.sessions)/(auth['harness']+'-'+auth['old_sid']+'.json')
            # The admitted Stop hook owns this fresh nonce. A stale last_event
            # or a status-line repaint must never count as completion of start.
            def stopped():
                record = load(record_path)
                return record.get('last_stop_nonce') and record['last_stop_nonce'] != load(directory/'driver-intent.json')['stop_nonce']
            wait(stopped, 'no new outgoing Stop observed')
            wait(lambda: backend.pane(auth['old_pane']).get('agent_status') == 'idle',
                  'outgoing still busy or awaiting a decision; do not send /exit')
            require(pane_close.identity(auth['old_frontend_pid']) == attempt['outgoing']['process'], 'outgoing identity changed')
            require(backend.terminal(auth['old_pane']) == load(directory/'pane-close-prepared.json')['predecessor'], 'outgoing pane changed')
            enabled()
            send_once(backend, directory, 'exit', auth['old_pane'], '/exit')
            wait(lambda:pane_close.process(auth['old_frontend_pid']) is None, 'outgoing did not exit through /exit')
            if start_record['park_after_exit']:
                predecessor = load(directory/'pane-close-prepared.json')['predecessor']
                wait(lambda:shell_ready(backend, auth['old_pane']), 'retired shell is not free')
                pane_close.empty_shell(backend, predecessor)
                cmd = shlex.join(['source', str(EXT/'bin/fm-context-relay-shell'), '--park', str(directory)])
                result = backend.invoke(['pane', 'run', auth['old_pane'], cmd])
                require(result.returncode == 0, 'parking uncertain; do not launch')
                wait(lambda:(directory/'shell-parked.json').exists(), 'original shell did not confirm parking')
            def startup():
                home = Path(paths.home)
                try:
                    pid = int((home/'state/.lock').read_text().strip())
                    if auth['harness'] == 'claude':
                        sid = (home/'state/.lock-session').read_text().strip()
                    else:
                        candidates = [common.read_json(str(p)) for p in Path(paths.sessions).glob('codex-*.json')]
                        candidates = [r for r in candidates if isinstance(r, dict) and r.get('harness') == 'codex'
                                      and str(r.get('owner_pid')) == str(pid) and r.get('session_id') != auth['old_sid']]
                        require(len(candidates) == 1, 'Codex successor session not yet unambiguously observed')
                        sid = candidates[0]['session_id']
                    require(pid != auth['old_frontend_pid'] and sid != auth['old_sid'], 'identidad reutilizada')
                    require((home/'state/.session-start-complete').read_text().strip() == str(pid), 'startup incomplete')
                    require(relay.argv_of(pid) == package['native_argv'], 'successor argv changed')
                    require(relay.processes.executable_digest(pid) == package['executable_sha256'], 'executable changed')
                    terminal = backend.terminal(auth['new_pane'])
                    require(terminal == package['destination'] and pane_close.identity(pid)['tty'] == terminal['shell']['tty'], 'successor terminal changed')
                    return True
                except (OSError, ValueError, pane_close.Preserve):
                    return False
            def execution_alive():
                attempt_now = load(directory/'attempt.json')
                generation = 2 if attempt_now.get('recoveries') else 1
                result = common.read_json(str(directory/('launch-execution-%d.json' % generation)))
                require(not result or result.get('state') not in ('exited', 'failed'),
                        'new frontend exited before verified startup')
                # Native Codex starts SessionStart only after the first prompt.
                # Prove the exact frontend/terminal and supported transport
                # readiness before sending that prompt, without faking a SID
                # or bootstrapping the home on the driver's behalf.
                try:
                    info = backend.call('pane', 'process-info', '--pane', auth['new_pane'])['process_info']
                    candidates = []
                    for row in info.get('foreground_processes', []):
                        pid = row.get('pid')
                        if type(pid) is int and relay.argv_of(pid) == package['native_argv']:
                            require(relay.processes.executable_digest(pid) == package['executable_sha256'], 'new binary changed')
                            require(pane_close.identity(pid)['tty'] == package['destination']['shell']['tty'], 'frontend belongs to another terminal')
                            candidates.append(pid)
                    pane = backend.pane(auth['new_pane'])
                    observation = dict(at=common.iso(), native_pids=candidates, process_info=info, pane=pane)
                    relay.put(directory/'frontend-observation.json', observation)
                    return (len(candidates) == 1 and candidates[0] != auth['old_frontend_pid']
                            and backend.terminal(auth['new_pane']) == package['destination']
                            and pane.get('agent') == auth['harness'] and pane.get('agent_status') == 'idle')
                except (OSError, ValueError, pane_close.Preserve):
                    return False
            try:
                enabled()
                invoke('launch', paths, args, directory)
                wait(execution_alive, 'successor own startup unverified')
            except (pane_close.Preserve, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as failure:
                if not is_auto:
                    raise
                attempt = load(directory/'attempt.json')
                if attempt['state'] == 'retired-awaiting-exit':
                    relay.save(directory, attempt, 'launch-failed')
                command = [str(EXT/'bin/fm-context-relay'), '--home', paths.home, 'relay', 'recover',
                           '--handoff', str(directory), '--herdr-session', args.herdr_session]
                if args.herdr_lab_helper:
                    command += ['--herdr-lab-helper', args.herdr_lab_helper]
                relay_notifications.emit(paths, directory, attempt, str(failure),
                    'One NEW session allowed, same harness/configuration, never resume: '+shlex.join(command))
                enabled()
                wait(lambda:shell_ready(backend, auth['new_pane']),
                     'target still busy/ambiguous; do not recover', seconds=10)
                # The shared recovery entry repeats free-home, original process,
                # terminal, generation fence and package checks. A live or
                # ambiguous session refuses instead of creating another primary.
                invoke('recover', paths, args, directory)
                automatic.update(paths, attempt['automatic_reservation'], 'recovering', recoveries=1,
                                 handoff=str(directory), failure=str(failure))
                wait(execution_alive, 'recovery did not establish own startup')
            enabled()
            send_once(backend, directory, 'prompt', auth['new_pane'], (directory/'successor-prompt.txt').read_text())
            wait(startup, 'successor own startup unverified after first prompt')
            # Only the successor can accept and close; the driver never writes
            # receipts and never calls the finalizer on its behalf.
            wait(lambda:(directory/'successor-accepted.json').exists(), 'successor did not accept; inspect package', seconds=240)
            wait(lambda:(directory/'pane-close-result.json').exists(), 'retired pane still preserved', seconds=240)
            require(load(directory/'pane-close-result.json')['status'] in ('closed', 'already-closed'), 'close unverified')
            relay.put(directory/'driver.json', dict(status='completed', at=common.iso()))
            return 0
    except (pane_close.Preserve, OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        if directory.is_dir() and directory.parent == Path(paths.home)/'data/context-relay/attempts':
            relay.put(directory/'driver.json', dict(status='preserved', reason=str(exc), at=common.iso()))
            relay.event(directory, 'driver-preserved', reason=str(exc))
            if is_auto:
                attempt = load(directory/'attempt.json')
                relay_notifications.emit(paths, directory, attempt, str(exc),
                    'No automatic retries remain. Inspect package/driver; with home free start a NEW primary '
                    'of the same harness through normal startup, without resume or reused SID. Preserve uncertainties.')
                automatic.update(paths, attempt['automatic_reservation'], 'preserved-after-failure',
                                 reason=str(exc), handoff=str(directory))
        print(str(exc), flush=True)
        return 1


def add_parser(actions):
    p = actions.add_parser('start', help='single entrypoint; summary and one operator confirmation', description=HELP,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--harness', choices=('claude', 'codex'))
    p.add_argument('--notes')
    p.add_argument('--confirm', metavar='HASH')
    p.add_argument('--confirmation')
    p.add_argument('--abort', action='store_true')
    p.add_argument('--automatic', action='store_true', help='use only durable auto authorize authority, without interactive prompt')
    p.add_argument('--herdr-session')
    p.add_argument('--herdr-lab-helper')
    p.set_defaults(func=start)
    p = actions.add_parser('_drive', help=argparse.SUPPRESS)
    p.add_argument('--handoff', required=True)
    p.add_argument('--herdr-session', required=True)
    p.add_argument('--herdr-lab-helper')
    p.set_defaults(func=drive)
