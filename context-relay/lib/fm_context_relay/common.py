"""Shared paths, time helpers, atomic writes and file locks.

Everything the extension writes lives in the Firstmate home ignored
directories: config/ for the settings, state/ for per-session records and
data/ for checkpoints and the settings change log.
"""

import datetime
import errno
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class Paths(object):
    """Every file location the extension uses inside one Firstmate home."""

    def __init__(self, home):
        self.home = os.path.abspath(home)
        self.config = os.path.join(self.home, "config", "context-relay.json")
        self.state_dir = os.path.join(self.home, "state", "context-relay")
        self.sessions = os.path.join(self.state_dir, "sessions")
        self.last_valid = os.path.join(self.state_dir, "last-valid-config.json")
        self.config_seen = os.path.join(self.state_dir, "config-seen.json")
        self.config_lock = os.path.join(self.state_dir, "config.lock")
        self.errors = os.path.join(self.state_dir, "hook-errors.log")
        self.data_dir = os.path.join(self.home, "data", "context-relay")
        self.changes_log = os.path.join(self.data_dir, "config-changes.jsonl")
        self.checkpoints = os.path.join(self.data_dir, "checkpoints")

    def session_record(self, key):
        return os.path.join(self.sessions, key + ".json")

    def session_lock(self, key):
        return os.path.join(self.sessions, key + ".lock")


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(dt=None):
    return (dt or utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value):
    """Parse an ISO-8601 UTC timestamp as harness transcripts write it."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python 3.8 fromisoformat accepts only 3 or 6 fractional digits.
    match = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", text)
    if match:
        frac = match.group(2) or ""
        if frac:
            frac = "." + (frac[1:] + "000000")[:6]
        text = match.group(1) + frac + match.group(3)
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except (IOError, OSError):
        return None


def read_json(path):
    data = read_bytes(path)
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def ensure_dir(path, mode=0o755):
    try:
        os.makedirs(path, mode)
    except OSError as exc:
        if exc.errno != errno.EEXIST or not os.path.isdir(path):
            raise


def atomic_write_bytes(path, data, mode=None):
    """Write data to path through a same-directory temp file and rename.

    An existing file keeps its permission bits unless mode is given.
    """
    directory = os.path.dirname(path)
    ensure_dir(directory)
    if mode is None:
        try:
            mode = os.stat(path).st_mode & 0o7777
        except OSError:
            mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def dump_json(obj):
    return (json.dumps(obj, indent=2, sort_keys=False, ensure_ascii=False) + "\n").encode("utf-8")


def atomic_write_json(path, obj, mode=None):
    atomic_write_bytes(path, dump_json(obj), mode)


def atomic_create_bytes(path, data, mode=0o644):
    """Publish a complete new file without ever replacing an existing name."""
    directory = os.path.dirname(path)
    ensure_dir(directory)
    fd, tmp = tempfile.mkstemp(prefix=".context-relay-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        return True
    finally:
        os.unlink(tmp)


def append_line(path, text, mode=0o644):
    """Append one line with a single O_APPEND write."""
    ensure_dir(os.path.dirname(path))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, mode)
    try:
        os.write(fd, (text.rstrip("\n") + "\n").encode("utf-8"))
    finally:
        os.close(fd)


class LockTimeout(Exception):
    pass


class FileLock(object):
    """Exclusive advisory lock on a sidecar file, bounded by a timeout."""

    def __init__(self, path, timeout=3.0):
        self.path = path
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        ensure_dir(os.path.dirname(self.path))
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (IOError, OSError) as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                    os.close(self.fd)
                    self.fd = None
                    raise
            if time.monotonic() >= deadline:
                os.close(self.fd)
                self.fd = None
                raise LockTimeout(self.path)
            time.sleep(0.02)

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None
        return False


def session_key(harness, session_id):
    if harness not in ("claude", "codex"):
        return None
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        return None
    return harness + "-" + session_id


def format_tokens(value):
    if value is None:
        return "no data"
    if value >= 1000000:
        return "%.2fM" % (value / 1000000.0)
    if value >= 1000:
        return "%.1fk" % (value / 1000.0)
    return str(value)


def log_error(paths, where, message):
    """Record a hook failure without ever raising; the log is size-bounded."""
    try:
        ensure_dir(paths.state_dir)
        try:
            if os.path.getsize(paths.errors) > 256 * 1024:
                os.replace(paths.errors, paths.errors + ".1")
        except OSError:
            pass
        append_line(paths.errors, json.dumps({"at": iso(), "where": where, "error": message[-2000:]}))
    except Exception:  # noqa: BLE001 - error logging must never fail the hook
        pass
