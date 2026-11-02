"""Read-only native session identity, owned by this extension.

Proves the caller through process ancestry and vendor metadata. This module
never registers endpoints, sends messages, writes state or acquires a lock.
"""
import json
import os
from pathlib import Path
import re
import subprocess

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


def slug(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("invalid stable identity")
    return value


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=15, **kwargs).stdout.strip()


def process(pid):
    return {key: run(["ps", "-p", str(pid), "-o", key + "="])
            for key in ("ppid", "comm", "args", "lstart")}


def ancestry():
    pid = os.getpid()
    for _ in range(64):
        if pid <= 1:
            return
        row = process(pid)
        yield pid, row
        pid = int(row["ppid"])


def claude_transcript(sid, payload, worker_hook):
    # A hook payload names its own transcript. An in-session command has no
    # payload path, so it takes the unique <sid>.jsonl in Claude's own project
    # store, as Codex takes its unique session file.
    if worker_hook or "transcript_path" in payload:
        path = Path(payload.get("transcript_path", ""))
    else:
        store = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
        paths = list(store.glob("*/" + sid + ".jsonl")) if store.is_absolute() else []
        if len(paths) != 1:
            raise ValueError("no unique native Claude transcript")
        path = paths[0]
    if not path.is_absolute() or path.stem != sid or not path.is_file():
        raise ValueError("native Claude transcript required")
    return path


def claude_cwd(path, sid):
    # Claude Code writes metadata-only records (last-prompt, mode,
    # permission-mode, file-history-snapshot, ...) before its first
    # conversation record, and only conversation records carry cwd. Skip them
    # in a bounded leading read, but every record that names a session must
    # name this one, including the first cwd-bearing record.
    with path.open() as stream:
        for index, line in enumerate(stream):
            if index >= 256:
                break
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or record.get("sessionId", sid) != sid:
                raise ValueError("native Claude session metadata mismatch")
            if "cwd" in record:
                if "sessionId" not in record:
                    raise ValueError("native Claude session metadata mismatch")
                return record["cwd"]
    raise ValueError("native Claude session metadata has no working directory")


def native_sid(provider, payload, worker_hook=False):
    key = "CODEX_THREAD_ID" if provider == "codex" else "CLAUDE_CODE_SESSION_ID"
    sid = slug(payload.get("session_id", "") if worker_hook else os.environ.get(key, ""))
    if payload.get("session_id", sid) != sid:
        raise ValueError("native hook and tool session disagree")
    return sid


def native(provider, home, payload, worker_hook=False):
    sid = native_sid(provider, payload, worker_hook)
    chain = list(ancestry())
    if provider == "codex":
        matches = [(p, r) for p, r in chain if Path(r["comm"]).name == "codex"]
        if not matches:
            raise ValueError("no native Codex process ancestor")
        pid, runtime = matches[0]
        provider_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        paths = list((provider_home / "sessions").glob("**/*" + sid + ".jsonl"))
        if len(paths) != 1:
            raise ValueError("native Codex session metadata is not unique")
        with paths[0].open() as stream:
            first = json.loads(stream.readline())
        meta = first.get("payload", {})
        if first.get("type") != "session_meta" or meta.get("id") != sid:
            raise ValueError("native Codex session metadata mismatch")
        if meta.get("source") not in ("cli", "vscode"):
            raise ValueError("unowned Codex source")
        cwd = Path(meta["cwd"]).resolve(strict=True)
        shared = "app-server" in runtime["args"].split()
        if shared and worker_hook:
            raise ValueError("shared hook alone cannot establish a new worker's launch ownership")
        if shared and os.environ.get("CODEX_SESSION_ID") != sid:
            raise ValueError("shared Codex runtime lacks matching tool-local session identity")
        transcript = None
    else:
        pid = int(os.environ.get("CLAUDE_PID", "0"))
        runtime = next((r for p, r in chain if p == pid), None)
        if not runtime or "claude" not in Path(runtime["comm"]).parts:
            # Native installer uses a version basename beneath a claude tree.
            if not runtime or "/claude/" not in runtime["args"].split()[0]:
                raise ValueError("no trusted Claude process ancestor")
        path = claude_transcript(sid, payload, worker_hook)
        cwd = Path(claude_cwd(path, sid)).resolve(strict=True)
        shared = False
        transcript = str(path)
    return sid, pid, runtime["lstart"], cwd, shared, transcript
