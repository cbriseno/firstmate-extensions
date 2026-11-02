"""Bounded process inspection. Unknown safety evidence must never imply absence."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys


RELAY_UNAVAILABLE_CODE = 'process_evidence_unavailable'
RELAY_UNAVAILABLE_MESSAGE = ('Relay unavailable: POSIX ps cannot verify the process identity and terminal input evidence required for a safe handover on this platform.')


class Unknown(OSError):
    """The platform cannot establish the requested fact."""


def ps(pid=None):
    # Ask for fields individually: BSD ps expands command columns and permits
    # whitespace in command names. Never split command text into an argv.
    argv = ['ps', '-A'] if pid is None else ['ps', '-p', str(pid)]
    argv += ['-o', 'pid=', '-o', 'ppid=', '-o', 'comm=', '-o', 'command=']
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=5, env=dict(os.environ, LC_ALL='C'))
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unknown('process inspection unknown') from exc
    if result.returncode or len(result.stdout) > 4_000_000:
        raise Unknown('process inspection unknown')
    rows = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) < 3 or not fields[0].isdigit() or not fields[1].isdigit():
            raise Unknown('ambiguous ps output')
        # comm can itself contain spaces on BSD; do not invent its boundary.
        rows.append(dict(pid=int(fields[0]), ppid=int(fields[1]), comm='unknown', command='unknown'))
    if not rows:
        raise Unknown('empty process listing')
    return rows


def safety_evidence_available():
    return sys.platform == 'linux'


def linux_only():
    if sys.platform != 'linux':
        raise Unknown('process safety evidence unknown on this platform')


def process(pid):
    if sys.platform != 'linux':
        rows = ps()  # A successful complete listing is required to prove absence.
        if not any(row['pid'] == pid for row in rows):
            return None
        raise Unknown('process identity unknown on this platform')
    try:
        raw = Path('/proc/%d/stat' % pid).read_text()
    except FileNotFoundError:
        return None
    fields = raw[raw.rfind(')') + 2:].split()
    return dict(pid=pid, start=fields[19], state=fields[0], parent=int(fields[1]),
                group=int(fields[2]), session=int(fields[3]), tty=int(fields[4]), foreground=int(fields[5]))


def pids():
    if sys.platform != 'linux':
        return [row['pid'] for row in ps()]
    return [int(p.name) for p in Path('/proc').iterdir() if p.name.isdigit()]


def proc_path(pid, name):
    linux_only()
    return Path('/proc/%d' % pid) / name


def argv_bytes(pid):
    return proc_path(pid, 'cmdline').read_bytes()


def executable_digest(pid):
    return hashlib.sha256(proc_path(pid, 'exe').read_bytes()).hexdigest()


def boot_id():
    linux_only()
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
