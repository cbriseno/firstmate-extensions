"""Incremental readers for the harness transcripts.

Resident context means the input side of the latest model call (fresh input
plus cache writes plus cache reads), never cumulative usage. Each reader keeps
its cursor in the session record, so a hook reads only bytes appended since the
previous event and a compaction cannot erase what was counted.

Claude Code: assistant rows carry message.usage, repeated across rows of the
same message.id; compactions are the distinct uuids of system rows with
subtype compact_boundary. Codex: event_msg/token_count rows carry
info.last_token_usage.input_tokens; compactions are the distinct window_id
(else window_number) of compacted rows. Neither PreCompact nor turn_id ever
counts a compaction.
"""

import json
import os
import time

def new_scan():
    return {
        "path": None,
        "dev": None,
        "ino": None,
        "offset": 0,
        "line": 0,
        "caught_up": False,
        "started_at": None,
        "last_usage": None,
        "compaction_line": 0,
        "compaction_ids": [],
        "compaction_aliases": [],
        "compaction_meta": [],
        "message_ids": [],
        "zero_line": 0,
        "model": None,
        "window_tokens": None,
        "window_line": None,
        "session_id": None,
        "originator": None,
        "source": None,
        "codex_tui": None,
        "codex_invalid": False,
    }


def scan(state, path, harness, budget_seconds):
    """Advance scan state over new complete lines of path; return state.

    A replaced or truncated file restarts the scan from the beginning. A
    partial last line is left for the next call. When the time budget runs
    out the state says caught_up=False and the next event continues.
    """
    if (not isinstance(state, dict) or state.get("path") != path
            or (harness == "codex" and "codex_tui" not in state)):
        previous = state or {}
        state = new_scan()
        for key in ("message_ids", "compaction_ids", "compaction_meta", "compaction_aliases"):
            state[key] = previous.get(key, [])
        state["path"] = path
    try:
        info = os.stat(path)
    except OSError:
        state["caught_up"] = False
        state["missing"] = True
        return state
    state.pop("missing", None)
    if (state.get("dev"), state.get("ino")) != (info.st_dev, info.st_ino) or info.st_size < state.get("offset", 0):
        fresh = new_scan()
        fresh["path"] = path
        for key in ("message_ids", "compaction_ids", "compaction_meta", "compaction_aliases"):
            fresh[key] = state.get(key, [])
        state = fresh
        state["dev"] = info.st_dev
        state["ino"] = info.st_ino
    handle_row = _claude_row if harness == "claude" else _codex_row
    deadline = time.monotonic() + budget_seconds
    offset = state["offset"]
    line_no = state["line"]
    caught_up = True
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            while True:
                raw = handle.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    caught_up = False
                    break
                offset += len(raw)
                line_no += 1
                handle_row(state, raw, line_no)
                if line_no % 2000 == 0 and time.monotonic() > deadline:
                    caught_up = handle.read(1) == b""
                    break
    except (IOError, OSError):
        caught_up = False
    state["offset"] = offset
    state["line"] = line_no
    state["caught_up"] = caught_up
    return state


def _loads(raw):
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _note_start(state, obj):
    if state.get("started_at") is None and isinstance(obj.get("timestamp"), str):
        state["started_at"] = obj["timestamp"]


def _add_compaction(state, key, line_no, meta, aliases=()):
    known = state["compaction_aliases"]
    duplicate = key in state["compaction_ids"] or any(a in known for a in aliases)
    known.extend(a for a in aliases if a not in known)
    if key and not duplicate:
        state["compaction_line"] = line_no
        state["compaction_ids"].append(key)
        state["compaction_meta"].append(meta)
        del state["compaction_meta"][:-20]

    elif not key:
        # An unidentified boundary invalidates usage but cannot be counted.
        state["compaction_line"] = line_no


def _claude_row(state, raw, line_no):
    if state.get("started_at") is None and b'"timestamp"' in raw:
        obj = _loads(raw)
        if obj:
            _note_start(state, obj)
    if b"compact_boundary" in raw:
        obj = _loads(raw)
        if obj and obj.get("type") == "system" and obj.get("subtype") == "compact_boundary":
            meta = obj.get("compactMetadata") if isinstance(obj.get("compactMetadata"), dict) else {}
            _add_compaction(
                state,
                obj.get("uuid") if isinstance(obj.get("uuid"), str) else None,
                line_no,
                {"at": obj.get("timestamp"), "trigger": meta.get("trigger"), "pre_tokens": meta.get("preTokens")},
            )
            return
    if b'"usage"' not in raw or b'"assistant"' not in raw:
        return
    obj = _loads(raw)
    if not obj or obj.get("type") != "assistant" or obj.get("isSidechain") is True:
        return
    message = obj.get("message")
    if not isinstance(message, dict):
        return
    message_id = message.get("id")
    usage = message.get("usage")
    if not isinstance(message_id, str) or not message_id or not isinstance(usage, dict):
        return
    model = message.get("model") if isinstance(message.get("model"), str) else ""
    if model.startswith("<"):
        return  # <synthetic> rows are local error placeholders, not model calls
    tokens = _int(usage.get("input_tokens")) + _int(usage.get("cache_creation_input_tokens")) + _int(usage.get("cache_read_input_tokens"))
    last = state.get("last_usage")
    if last and last.get("id") == message_id:
        if last["line"] > state["compaction_line"]:
            last["tokens"] = tokens
        return
    if message_id in state["message_ids"]:
        return  # a duplicate row of an older message never replaces a newer one
    state["message_ids"].append(message_id)
    state["last_usage"] = {"id": message_id, "tokens": tokens, "model": model, "at": obj.get("timestamp"), "line": line_no}
    if model:
        state["model"] = model


def _codex_row(state, raw, line_no):
    obj = _loads(raw)
    if not obj:
        state["codex_invalid"] = True
        return
    kind = obj.get("type")
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    if kind == "session_meta":
        state["session_id"] = payload.get("id")
        state["originator"] = payload.get("originator")
        state["source"] = payload.get("source")
        window = payload.get("context_window")
        root = window.get("window_id") if isinstance(window, dict) else None
        # This exact producer/protocol is backed by native linked boundaries
        # and independent resident drops. A version or model alone is no proof.
        if (line_no == 1 and payload.get("originator") == "codex-tui"
                and payload.get("source") in ("cli", "vscode")
                and payload.get("cli_version") == "0.166.0"
                and payload.get("history_mode") == "paginated"
                and payload.get("thread_source") == "user"
                and isinstance(payload.get("id"), str) and payload["id"]
                and payload.get("session_id") == payload["id"]
                and isinstance(root, str) and root):
            state["codex_tui"] = {"root": root, "last": root, "windows": {}}
        else:
            state["codex_invalid"] = True
        if state.get("started_at") is None:
            state["started_at"] = payload.get("timestamp") or obj.get("timestamp")
        return
    _note_start(state, obj)
    if kind == "turn_context":
        if isinstance(payload.get("model"), str) and payload["model"]:
            if payload["model"] != state.get("model"):
                state["window_tokens"] = None
            state["model"] = payload["model"]
        return
    if kind == "compacted":
        if _codex_tui_boundary(state, payload):
            # Durable IDs survive rotation and reader upgrades. A boundary
            # first seen in this file pass still invalidates pre-boundary usage.
            state["compaction_line"] = line_no
        key = payload.get("window_id")
        number = _int(payload.get("window_number"))
        aliases = []
        if isinstance(key, str) and key:
            aliases.append("window_id:" + key)
        if number:
            aliases.append("window_number:%d" % number)
        if not isinstance(key, str) or not key:
            key = "window_number:%s" % number if number else None
        _add_compaction(
            state,
            key,
            line_no,
            {"at": obj.get("timestamp"), "window_number": payload.get("window_number")},
            aliases,
        )
        return
    if kind == "event_msg" and payload.get("type") == "token_count":
        info = payload.get("info")
        if not isinstance(info, dict):
            return
        window = info.get("model_context_window")
        state["window_tokens"] = _int(window) or None
        state["window_line"] = line_no if _int(window) else None
        last = info.get("last_token_usage")
        tokens = _int(last.get("input_tokens")) if isinstance(last, dict) else 0
        if tokens <= 0:
            state["zero_line"] = line_no
            return
        state["last_usage"] = {
            "id": "line:%d" % line_no,
            "tokens": tokens,
            "model": state.get("model") or "",
            "at": obj.get("timestamp"),
            "line": line_no,
        }


def _codex_tui_boundary(state, payload):
    """Validate a complete window chain, allowing exact boundary replays.

    Reconstruct this proof on every restart of the file; durable IDs alone
    cannot prove that a rotated/truncated transcript still contains history.
    Nested replacement history and event wrappers never reach this function.
    """
    protocol = state.get("codex_tui")
    if not protocol:
        return
    key = payload.get("window_id")
    number = payload.get("window_number")
    previous = payload.get("previous_window_id")
    root = payload.get("first_window_id")
    signature = [number, previous, root]
    windows = protocol["windows"]
    if (not isinstance(key, str) or not key or key == protocol["root"]
            or not _int(number) or root != protocol["root"]):
        state["codex_invalid"] = True
    elif key in windows:
        if windows[key] != signature:
            state["codex_invalid"] = True
    elif number != len(windows) + 1 or previous != protocol["last"]:
        state["codex_invalid"] = True
    else:
        windows[key] = signature
        protocol["last"] = key
        return True
    return False


def resident(state, signal_usage_id=None):
    """Classify the current resident context; a stale or zero figure is no data."""
    def no_data(reason):
        return {"status": "no_data", "reason": reason, "tokens": None}

    if not isinstance(state, dict) or state.get("missing"):
        return no_data("transcript_missing")
    if not state.get("caught_up"):
        return no_data("catching_up")
    last = state.get("last_usage")
    if not last:
        return no_data("no_call_yet")
    if state.get("compaction_line", 0) > last.get("line", 0):
        return no_data("after_compaction")
    if state.get("zero_line", 0) > last.get("line", 0):
        return no_data("zero")
    if state.get("model") and state["model"] != last.get("model"):
        return no_data("model_changed")
    if signal_usage_id is not None and signal_usage_id == last.get("id"):
        return no_data("after_compaction")
    if not last.get("tokens"):
        return no_data("zero")
    return {
        "status": "ok",
        "reason": None,
        "tokens": last["tokens"],
        "at": last.get("at"),
        "line": last.get("line"),
        "message_id": last.get("id"),
    }


def compaction_counting(harness, state):
    """Whether this session's compaction count is a verified signal."""
    if harness == "claude":
        return ("verified", "compact_boundary")
    if state.get("originator") == "codex_exec" or state.get("source") == "exec":
        return ("verified", "codex exec: compacted")
    protocol = state.get("codex_tui")
    if (protocol and state.get("caught_up") and not state.get("missing")
            and not state.get("codex_invalid")
            and set(protocol["windows"]) == set(state.get("compaction_ids") or [])):
        return ("verified", "codex-tui 0.166.0 %s: paginated/window-chain" % state["source"])
    detail = state.get("originator") or "desconocido"
    return ("unverified", "codex %s" % detail)


def summarize(harness, state, signal_usage_id=None):
    counting, detail = compaction_counting(harness, state)
    return {
        "harness": harness,
        "model": state.get("model"),
        "started_at": state.get("started_at"),
        "resident": resident(state, signal_usage_id),
        "window_tokens": state.get("window_tokens"),
        "compactions": {
            "count": len(state.get("compaction_ids") or []),
            "counting": counting,
            "signal": detail,
            "recent": list(state.get("compaction_meta") or [])[-5:],
        },
        "lines": state.get("line"),
        "caught_up": state.get("caught_up"),
    }
