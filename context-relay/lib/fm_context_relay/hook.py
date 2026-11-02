"""Hook event handler, reached only after bin/fm-context-relay-hook proved
that this process runs inside the session that owns the home lock.

Contract with the harness: exit status is always 0, stdout is either empty or
one JSON object carrying hookSpecificOutput.additionalContext on an event
whose verified mechanism delivers it to the model without creating a turn,
and nothing is ever printed for Stop, PreCompact or PostCompact. A pending
warning is marked delivered before it is printed, so a repeated event can
never inject it again.
"""

import json
import os
import sys
import time
import traceback

from . import checkpoint
from . import common
from . import config
from . import session
from . import automatic

# Events whose additionalContext reaches the model with its next request.
INJECT_EVENTS = {
    "claude": ("SessionStart", "UserPromptSubmit", "PostToolBatch"),
    "codex": ("SessionStart", "UserPromptSubmit", "PostToolUse"),
}
# Events that only measure. Stop never prints: a Stop hook that returns
# context or blocks makes the conversation continue, which would loop.
MEASURE_EVENTS = ("Stop", "PreCompact", "PostCompact", "StatusLine")
LOCK_TIMEOUT = 3.0
RECORD_RETENTION_DAYS = 30


def throttle_seconds():
    try:
        return max(0, int(os.environ.get("FM_CONTEXT_RELAY_THROTTLE_SECONDS", "30")))
    except ValueError:
        return 30


def _write_gate(paths, key, pending):
    """Arm or clear the per-session gate bin/fm-context-relay-hook reads."""
    gate = os.path.join(paths.state_dir, "throttle", key)
    interval = throttle_seconds()
    if pending or interval <= 0:
        try:
            os.unlink(gate)
        except OSError:
            pass
        return
    common.atomic_write_bytes(gate, ("%d\n" % (int(time.time()) + interval)).encode("ascii"), 0o644)


def scan_budget(event):
    return 3.0 if event in ("SessionStart", "PreCompact", "PostCompact") else 1.5


def main():
    home = os.environ.get("FM_CONTEXT_RELAY_HOME") or ""
    harness = os.environ.get("FM_CONTEXT_RELAY_HARNESS") or ""
    event = os.environ.get("FM_CONTEXT_RELAY_EVENT") or ""
    try:
        raw = sys.stdin.buffer.read()
    except (IOError, OSError):
        raw = b""
    if not home:
        return 0
    paths = common.Paths(home)
    output = None
    try:
        output = handle(paths, harness, event, raw)
    except common.LockTimeout:
        output = None
    except Exception:  # noqa: BLE001 - a hook failure must stay silent and exit 0
        common.log_error(paths, "%s/%s" % (harness, event), traceback.format_exc())
        output = None
    if output:
        try:
            sys.stdout.write(output + "\n")
            sys.stdout.flush()
        except (IOError, OSError):
            pass
    return 0


def _excluded(harness, event, payload):
    """True for events that must stay inert even inside the owning session."""
    if payload.get("agent_id"):
        return True  # a subagent's event; its context is not the orchestrator's
    if harness == "claude" and "cursor_version" in payload:
        return True  # Cursor delivering Claude-shaped settings
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str) and "/.pi/" in transcript:
        return True  # pi-code delivering Claude-shaped settings
    name = payload.get("hook_event_name")
    if isinstance(name, str) and name and name != event:
        return True
    if harness == "codex":
        if payload.get("agent_type"):
            return True
        if config.normalize_model(payload.get("model")).startswith("codex-auto-review"):
            return True
    if event == "Stop" and payload.get("stop_hook_active") is True:
        return True  # a continuation turn: do nothing at all
    return False


def handle(paths, harness, event, raw):
    if harness not in INJECT_EVENTS:
        return None
    if event not in INJECT_EVENTS[harness] and event not in MEASURE_EVENTS:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or _excluded(harness, event, payload):
        return None
    session_id = payload.get("session_id")
    key = common.session_key(harness, session_id)
    if key is None:
        return None
    bound_session = common.read_bytes(os.path.join(paths.home, "state", ".lock-session"))
    if bound_session and bound_session.decode("utf-8", "replace").strip() != session_id:
        return None
    now = common.utcnow()
    window_path = os.path.join(paths.state_dir, 'windows', key + '.json')
    if event == 'StatusLine':
        window = (payload.get('context_window') or {}).get('context_window_size')
        model = payload.get('model') or {}
        if isinstance(window, int) and not isinstance(window, bool) and window > 0 and isinstance(model, dict):
            common.atomic_write_json(window_path, {'model': config.normalize_model(model.get('id')),
                'window_tokens': window, 'at': common.iso(now), 'source': 'claude-statusline'})
        return None
    loaded = config.load(paths, remember=True)
    cli = os.environ.get("FM_CONTEXT_RELAY_CLI") or "fm-context-relay"
    compaction_event = None
    if event == "PostCompact" or (event == "SessionStart" and payload.get("source") == "compact"):
        compaction_event = event
    if event == "SessionStart" and payload.get("source") == "startup":
        _prune_records(paths, now)
    text = None
    run_checkpoint = False
    with common.FileLock(paths.session_lock(key), LOCK_TIMEOUT):
        record = session.load_record(paths, key, harness, session_id, now)
        record["updated_at"] = common.iso(now)
        record["last_event"] = event
        if event == 'UserPromptSubmit' and str(payload.get('prompt', '')).strip() not in ('/exit', '/quit'):
            record['last_user_prompt_nonce'] = os.urandom(16).hex()
        if event == 'Stop':
            record['last_stop_nonce'] = os.urandom(16).hex()
        owner = os.environ.get("FM_CONTEXT_RELAY_OWNER_PID")
        if owner:
            record["owner_pid"] = owner
        transcript = payload.get("transcript_path")
        if isinstance(transcript, str) and transcript:
            record["transcript_path"] = transcript
        payload_model = payload.get("model")
        if isinstance(payload_model, str) and payload_model and not record.get("model"):
            record["model"] = payload_model
            if payload_model not in record["models_seen"]:
                record["models_seen"].append(payload_model)
        new_compactions = session.refresh_measurement(record, harness, scan_budget(event), compaction_event, now)
        if harness == 'claude':
            observed = common.read_json(window_path) or {}
            if observed.get('model') and observed['model'] == config.normalize_model(record.get('model')):
                record['window_tokens_observed'] = observed['window_tokens']
                record['window_observation'] = observed
        session.evaluate(record, loaded, harness, now, cli, new_compactions)
        if event in INJECT_EVENTS[harness]:
            text = session.take_pending(record, event, now)
        if event == "PreCompact" and loaded.settings.value("precompact_checkpoint") and checkpoint.due(record, now):
            record["checkpoint"]["last_attempt_at"] = common.iso(now)
            record["checkpoint"]["last_compaction_count"] = record["compactions"]["count"]
            run_checkpoint = True
        session.save_record(paths, key, record)
        _write_gate(paths, key, bool(record["pending"]))
    if run_checkpoint:
        path, error = checkpoint.take(paths, key, record, payload, now)
        with common.FileLock(paths.session_lock(key), LOCK_TIMEOUT):
            record = session.load_record(paths, key, harness, session_id, now)
            info = record.setdefault("checkpoint", {})
            if path:
                info["last_path"] = path
                info["count"] = int(info.get("count") or 0) + 1
            info["last_error"] = error
            if error:
                common.log_error(paths, "%s/PreCompact checkpoint" % harness, error)
            session.save_record(paths, key, record)
    if event in INJECT_EVENTS[harness]:
        auto_text = automatic.notice(paths, record, now)
        if auto_text:
            text = (text+'\n\n' if text else '')+auto_text
    if not text:
        return None
    return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}, ensure_ascii=False)


def _prune_records(paths, now):
    cutoff = time.time() - RECORD_RETENTION_DAYS * 86400
    try:
        names = os.listdir(paths.sessions)
    except OSError:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        target = os.path.join(paths.sessions, name)
        try:
            if os.path.getmtime(target) < cutoff:
                os.unlink(target)
                lock = target[: -len(".json")] + ".lock"
                if os.path.exists(lock):
                    os.unlink(lock)
        except OSError:
            pass
