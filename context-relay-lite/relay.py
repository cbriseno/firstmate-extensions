#!/usr/bin/env python3
"""Claude/Linux/Herdr local relay; installation alone grants no consent.

Hook: relay-hook FIRSTMATE_HOME (Claude Stop/PostToolUse JSON on stdin).
Check: relay.py --home FIRSTMATE_HOME --dry-run < hook-payload.json
Consent: config/relay-lite.json: enabled, threshold_tokens (default 400000),
stow_timeout_seconds (1800), startup_timeout_seconds (180), exit_timeout_seconds
(120). Errors stop here, without retry or automatic recovery; see controller.log.
"""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location(
    'relay_measure', Path(__file__).resolve().with_name('measure.py'))
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)  # Read-only reader; no old relay state or config.


def need(value, message):
    if not value:
        raise RuntimeError(message)


def read(path, default=None):
    return path.read_text().strip() if path.exists() else default


def load(path):
    return json.loads(read(path, '{}'))


def put(path, value):
    temporary = path.with_name(path.name + '.tmp-' + str(os.getpid()))
    with temporary.open('w') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def process(pid):
    try:
        row = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'start': row[19], 'tty': row[4], 'session': row[3]} if row[0] != 'Z' else None
    except FileNotFoundError:
        return None


def ancestor(pid):
    current = os.getpid()
    while current > 1:
        if current == pid:
            return True
        current = int(Path(f'/proc/{current}/stat').read_text().rsplit(')', 1)[1].split()[1])
    return False


class Relay:
    def __init__(self, home):
        self.home = home.resolve(strict=True)
        self.directory = self.home / 'state/relay-lite'
        self.ticket_path = self.directory / 'ticket.json'
        self.ticket = {}

    def config(self):
        value = load(self.home / 'config/relay-lite.json')
        need(value.get('enabled') is True, 'relay disabled')
        return value

    def save(self, phase, **values):
        self.ticket.update(values, phase=phase, updated_at=time.time())
        put(self.ticket_path, json.dumps(self.ticket, ensure_ascii=False) + '\n')

    def call(self, *args):
        result = subprocess.run(['herdr', '--session', self.ticket['herdr_session'], *args],
                                capture_output=True, text=True, timeout=15)
        need(result.returncode == 0, f'Herdr rejected or uncertain {args[:2]}: {result.stderr or result.stdout}')
        if args[0] == 'agent' or args[:2] == ('pane', 'run'):
            return {}  # These transport commands are checked by exit status.
        value = json.loads(result.stdout)
        need('error' not in value, f'Herdr error: {value}')
        return value.get('result', value)

    def pane(self, pane):
        value = self.call('pane', 'get', pane)['pane']
        need(value['pane_id'] == pane, 'exact pane changed')
        return value

    def terminal(self, pane):
        value = self.pane(pane)
        info = self.call('pane', 'process-info', '--pane', pane)['process_info']
        return {key: value[key] for key in ('pane_id', 'terminal_id', 'tab_id', 'workspace_id')} | {
            'shell': process(info['shell_pid'])}

    def wait(self, predicate, seconds, reason):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.config()
            if predicate():
                return
            time.sleep(.5)
        raise RuntimeError(reason)

    def owner(self):
        return (process(self.ticket['old_pid']) == self.ticket['old_process']
                and read(self.home / 'state/.lock') == str(self.ticket['old_pid'])
                and read(self.home / 'state/.lock-session') == self.ticket['sid'])

    def idle(self):
        need(self.owner(), 'old primary no longer matches')
        need(self.terminal(self.ticket['old_pane']) == self.ticket['old_terminal'], 'old terminal changed')
        return self.pane(self.ticket['old_pane']).get('agent_status') == 'idle'

    def prompt(self, phase, text):
        self.config()
        self.save(phase)  # Intention is durable BEFORE sending; no resend path.
        self.call('agent', 'prompt', self.ticket['old_pane'], text)

    def admit(self, payload, dry_run):
        config = self.config()
        sid = payload['session_id']
        need(re.fullmatch(r'[A-Za-z0-9._-]+', sid), 'invalid session id')
        pid = int(read(self.home / 'state/.lock', '0'))
        need(pid > 1 and ancestor(pid), 'hook is not a descendant of primary')
        need(read(self.home / 'state/.lock-session') == sid, 'hook is not primary session')
        transcript = Path(payload['transcript_path'])
        need(transcript.is_absolute() and transcript.stem == sid, 'foreign transcript')
        scan = measure.scan(measure.new_scan(), str(transcript), 'claude', 2)
        resident = measure.resident(scan)
        eligible = resident['status'] == 'ok' and resident['tokens'] >= int(config.get('threshold_tokens', 400000))
        if dry_run:
            print(json.dumps({'phase': 'measurement', 'eligible': eligible, 'resident': resident, 'dry_run': True}))
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        if payload.get('hook_event_name') == 'Stop':
            put(self.directory / ('stop-' + sid), str(time.time()))
        if not eligible:
            return
        with (self.directory / 'controller.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            if (self.directory / ('session-' + sid)).exists():
                return
            if time.time() - float(read(self.directory / 'last-relay', '0')) < 1800:
                return
            need(os.environ.get('HERDR_ENV') == '1' and os.environ.get('HERDR_PANE_ID'), 'local Herdr required')
            need(not os.environ.get('FM_TASK_ID'), 'workers cannot relay primary')
            argv = Path(f'/proc/{pid}/cmdline').read_bytes().decode().strip('\0').split('\0')
            model = scan['model']
            for i, arg in enumerate(argv):
                if arg == '--model' and i + 1 < len(argv):
                    model = argv[i + 1]
                elif arg.startswith('--model='):
                    model = arg.split('=', 1)[1]
            need(model and shutil.which('claude'), 'Claude executable/model unavailable')
            self.ticket = dict(sid=sid, nonce=uuid.uuid4().hex, old_pid=pid,
                               old_process=process(pid), model=model, claude=shutil.which('claude'),
                               old_pane=os.environ['HERDR_PANE_ID'],
                               herdr_session=os.environ.get('HERDR_SESSION') or 'default')
            self.save('requested')
            put(self.directory / ('session-' + sid), self.ticket['nonce'])
            with (self.directory / 'controller.log').open('a') as log:
                subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--home',
                                  str(self.home), '--drive'], stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=log, cwd=self.home, start_new_session=True)

    def drive(self):
        with (self.directory / 'controller.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.ticket = load(self.ticket_path)
            need(self.ticket['phase'] == 'requested', 'already attempted; inspect manually')
            try:
                config = self.config()
                seconds = float(config.get('stow_timeout_seconds', 1800))
                self.ticket['old_terminal'] = self.terminal(self.ticket['old_pane'])
                need(self.ticket['old_process'] and self.ticket['old_process']['tty'] != '0'
                     and self.ticket['old_process']['tty'] == self.ticket['old_terminal']['shell']['tty']
                     and self.ticket['old_process']['session'] == self.ticket['old_terminal']['shell']['session'], 'primary is not in captured terminal')
                self.wait(self.idle, seconds, 'old primary did not become idle')
                ready = self.directory / 'stow-ready'
                nonce = self.ticket['nonce']
                self.prompt('stowing', 'haz un /stow para un relevo; al terminar, solo si es reset-safe, '
                            f'escribe el archivo {ready} con este nonce: {nonce}')
                self.wait(lambda: read(ready) == nonce, seconds, 'stow-ready expired; old primary preserved')
                stop = self.directory / ('stop-' + self.ticket['sid'])
                self.wait(lambda: stop.exists() and stop.stat().st_mtime_ns >= ready.stat().st_mtime_ns
                          and self.idle(), seconds, 'stow turn did not finish; old primary preserved')
                self.save('stowed')
                self.config()
                self.save('opening')
                new = self.call('pane', 'split', self.ticket['old_pane'], '--direction', 'right',
                                '--cwd', str(self.home), '--no-focus')['pane']
                self.save('opened', new_pane=new['pane_id'])
                need(new['tab_id'] == self.ticket['old_terminal']['tab_id'], 'new pane is in another tab')
                self.wait(lambda: self.empty(new['pane_id']), 15, 'new pane not empty; old primary preserved')
                need(self.idle(), 'old primary became busy before exit')
                self.prompt('exiting', '/exit')  # Exactly one send, including uncertain outcomes.
                self.wait(lambda: process(self.ticket['old_pid']) != self.ticket['old_process'],
                          float(config.get('exit_timeout_seconds', 120)), '/exit uncertain; no resend or launch')
                put(self.directory / 'last-relay', str(time.time()))
                self.save('old-exited')
                prompt = ('toma el rol de firstmate; lee el resultado del stow y el estado actual; '
                          'conserva todos los pendientes. Tras startup normal, adquirir el lock y leer '
                          f'el estado, escribe {self.directory / "adopted"} con este nonce: {nonce}')
                command = shlex.join([self.ticket['claude'], '--model', self.ticket['model'], prompt])
                self.config()
                self.save('launching')
                self.call('pane', 'run', new['pane_id'], command)
                self.wait(lambda: self.adopted(nonce), float(config.get('startup_timeout_seconds', 180)),
                          'successor did not adopt; both panes preserved')
                self.save('adopted')
                if self.terminal(self.ticket['old_pane']) == self.ticket['old_terminal'] and self.empty(self.ticket['old_pane']):
                    self.save('closing')
                    self.call('pane', 'close', self.ticket['old_pane'])
                    self.save('done', cleanup_pending=False)
                else:
                    self.save('done', cleanup_pending=True)
            except Exception as exc:
                phase = self.ticket.get('phase')
                self.save('done' if phase in ('adopted', 'closing') else 'stopped',
                          failed_phase=phase, error=str(exc), cleanup_pending=True)
                print(f'RELAY-LITE STOPPED ({phase}): {exc}', flush=True)

    def empty(self, pane):
        info = self.call('pane', 'process-info', '--pane', pane)['process_info']
        shell = process(info['shell_pid'])
        children = read(Path(f'/proc/{info["shell_pid"]}/task/{info["shell_pid"]}/children'), 'unknown')
        return (shell is not None and not children and not self.pane(pane).get('agent')
                and info.get('foreground_process_group_id') == info['shell_pid']
                and [p['pid'] for p in info.get('foreground_processes', [])] == [info['shell_pid']])

    def adopted(self, nonce):
        if read(self.directory / 'adopted') != nonce:
            return False
        pid = int(read(self.home / 'state/.lock', '0'))
        if pid <= 1 or pid == self.ticket['old_pid'] or read(self.home / 'state/.session-start-complete') != str(pid):
            return False
        sid = read(self.home / 'state/.lock-session')
        new = process(pid)
        shell = self.terminal(self.ticket['new_pane'])['shell']
        return bool(sid and sid != self.ticket['sid'] and new and shell
                    and new['tty'] != '0' and new['tty'] == shell['tty'] and new['session'] == shell['session'])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true', help='measurement only; no writes, Herdr calls or launches')
    parser.add_argument('--drive', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    relay = Relay(args.home)
    try:
        if args.drive:
            need(not args.dry_run, 'drive cannot be dry-run')
            relay.drive()
        else:
            relay.admit(json.load(sys.stdin), args.dry_run)
    except Exception as exc:
        print(f'relay-lite: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
