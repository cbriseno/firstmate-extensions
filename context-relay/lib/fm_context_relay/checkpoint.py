"""Operational checkpoint taken from the PreCompact hook.

A checkpoint is a deterministic capture of state, never a curation step: it
stores the session record's metrics, the home git HEAD and the read-only
fleet snapshot from bin/fm-fleet-snapshot.sh --json. The snapshot runs under
its own timeout inside the hook timeout, and any failure is recorded and
swallowed so the hook still exits 0 and never blocks the compaction.
"""

import json
import os
import signal
import subprocess
import time

from . import common

SCHEMA = "fm-context-relay-checkpoint.v1"
KEEP_PER_SESSION = 10
PRUNE_AFTER_DAYS = 30
DEDUPE_SECONDS = 90  # PreCompact can fire twice for one automatic compaction


def snapshot_timeout():
    try:
        value = float(os.environ.get("FM_CONTEXT_RELAY_SNAPSHOT_TIMEOUT", "20"))
    except ValueError:
        value = 20.0
    return max(1.0, min(value, 25.0))


def _run_bounded(argv, cwd, env, timeout):
    """Run argv in its own process group; kill the whole group on timeout."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        return (proc.returncode, out, err, False)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        out, err = proc.communicate()
        return (None, out, err, True)


def _git_head(home):
    try:
        rc, out, _, timed_out = _run_bounded(["git", "-C", home, "rev-parse", "HEAD"], home, dict(os.environ), 5)
    except OSError:
        return None
    if timed_out or rc != 0:
        return None
    return out.decode("utf-8", "replace").strip() or None


def due(record, now):
    info = record.get("checkpoint") or {}
    last = common.parse_iso(info.get("last_attempt_at"))
    count = (record.get("compactions") or {}).get("count", 0)
    return last is None or count != info.get("last_compaction_count") or (now - last).total_seconds() >= DEDUPE_SECONDS


def take(paths, key, record, payload, now):
    """Write one checkpoint file; return (path, error). Never raises."""
    try:
        return _take(paths, key, record, payload, now)
    except Exception as exc:  # noqa: BLE001 - a checkpoint must never fail the hook
        return (None, "%s: %s" % (type(exc).__name__, exc))


def _take(paths, key, record, payload, now):
    started = time.monotonic()
    snapshot = None
    snapshot_error = None
    script = os.path.join(paths.home, "bin", "fm-fleet-snapshot.sh")
    if os.path.isfile(script) and os.access(script, os.X_OK):
        env = dict(os.environ)
        env["FM_HOME"] = paths.home
        limit = snapshot_timeout()
        try:
            rc, out, err, timed_out = _run_bounded(["bash", script, "--json"], paths.home, env, limit)
            if timed_out:
                snapshot_error = "fm-fleet-snapshot.sh exceeded %ss" % int(limit)
            elif rc != 0:
                snapshot_error = "fm-fleet-snapshot.sh exited %s: %s" % (rc, err.decode("utf-8", "replace").strip()[-300:])
            else:
                try:
                    snapshot = json.loads(out.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    snapshot_error = "fm-fleet-snapshot.sh did not return JSON"
        except OSError as exc:
            snapshot_error = "fm-fleet-snapshot.sh could not execute: %s" % exc
    else:
        snapshot_error = "bin/fm-fleet-snapshot.sh missing in home"
    body = {
        "schema": SCHEMA,
        "kind": "precompact",
        "created_at": common.iso(now),
        "harness": record.get("harness"),
        "session_id": record.get("session_id"),
        "trigger": payload.get("trigger"),
        "home": paths.home,
        "home_git_head": _git_head(paths.home),
        "owner_pid": record.get("owner_pid"),
        "session": {
            "model": record.get("model"),
            "resident": record.get("resident"),
            "compactions": record.get("compactions"),
            "request": record.get("request"),
            "effective": record.get("effective"),
            "transcript_path": record.get("transcript_path"),
        },
        "fleet_snapshot": snapshot,
        "fleet_snapshot_error": snapshot_error,
        "fleet_snapshot_sha256": common.sha256_bytes(json.dumps(snapshot, sort_keys=True).encode("utf-8")) if snapshot is not None else None,
        "duration_seconds": round(time.monotonic() - started, 2),
    }
    directory = os.path.join(paths.checkpoints, key)
    name = now.strftime("%Y%m%dT%H%M%S%fZ") + "-precompact.json"
    path = os.path.join(directory, name)
    common.atomic_write_json(path, body, 0o600)
    _prune(paths, directory, now)
    return (path, snapshot_error)


def _prune(paths, directory, now):
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith("-precompact.json"))
        for name in names[:-KEEP_PER_SESSION]:
            os.unlink(os.path.join(directory, name))
    except OSError:
        pass
    cutoff = time.time() - PRUNE_AFTER_DAYS * 86400
    try:
        for entry in os.listdir(paths.checkpoints):
            target = os.path.join(paths.checkpoints, entry)
            if target == directory or not os.path.isdir(target):
                continue
            files = [os.path.join(target, n) for n in os.listdir(target)]
            if files and all(os.path.getmtime(f) < cutoff for f in files):
                for f in files:
                    os.unlink(f)
                os.rmdir(target)
    except OSError:
        pass
