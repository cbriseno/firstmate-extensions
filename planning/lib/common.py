"""Private state primitives; no shell, model, network or Firstmate internals."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

SCHEMA = 1
VERSION = '1.0.0'
MODES = ('native', 'speckit')
ROOT = Path(__file__).resolve().parents[2]


class PlanningError(Exception):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def regular(path):
    """Reject symlinks, including existing ancestors, before reading/writing state."""
    for item in (path, *path.parents):
        if item.is_symlink():
            raise PlanningError(f'Symlink is not supported: {item}; use an explicit real location')
    if path.exists() and not path.is_file():
        raise PlanningError(f'Expected a regular file: {path}; repair this location and retry')


def read_json(path, default=None):
    regular(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise PlanningError(f'Invalid or unreadable JSON: {path}: {exc}; repair the file and retry') from exc


def atomic(path, value):
    regular(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.planning-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def locked(directory, name='state.lock'):
    path = directory / name
    regular(path)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a') as stream:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PlanningError('Planning operation busy; retry after the current transaction finishes') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def installation():
    base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    if not base.is_absolute():
        raise PlanningError('XDG_CONFIG_HOME must be absolute; repair the environment and retry')
    return base / 'fm-planning'


def home_root(value):
    if not value:
        raise PlanningError('Missing home; set FM_HOME or pass --home HOME')
    path = Path(value).resolve(strict=True)
    if not path.is_dir() or not (path / 'config').is_dir():
        raise PlanningError('Home requires an existing config directory; pass the intended Firstmate home')
    return path


def home_state(home):
    return home / 'config' / 'planning'
