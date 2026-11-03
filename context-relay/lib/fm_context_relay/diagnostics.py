"""Single-file, standard-library anonymized diagnostics for extensions.

Only event-specific structural values are persisted. Unknown fields and free
text are dropped before JSONL is written.
"""
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import atexit
import zipfile
from pathlib import Path

LIMIT = 20 * 1024 * 1024
BATCH = 32
EVENTS = {
    "state.change": {"from": "enum", "to": "enum", "actor": "enum", "fields_missing": "names", "task": "id"},
    "snapshot.read": {"tasks": "count", "parent_unresolved": "count", "visibility_missing": "count"},
    "ui.layout-shift": {"el": "name", "dx": "measure", "dy": "measure", "w_from": "measure", "w_to": "measure", "vw": "measure", "dpr": "measure"},
    "anon.measure": {"words": "count", "length": "length", "width_ratio": "ratio", "font": "enum", "lines_from": "count", "lines_to": "count"},
    "relay.preflight": {"check": "enum", "result": "enum", "reason": "enum", "version": "version", "revision": "revision"},
    "relay.attempt": {"result": "enum", "reason": "enum", "count": "count", "version": "version", "revision": "revision"},
}
ENUMS = {
    "from": {"queued", "working", "paused", "finished", "blocked", "unknown"},
    "to": {"queued", "working", "paused", "finished", "blocked", "unknown"},
    "actor": {"user", "agent", "system", "unknown"},
    "check": {"model", "window", "herdr.session", "tasks.paused", "processes", "authorization", "config", "code"},
    "result": {"pass", "fail", "skip", "attempt", "rejected"},
    "reason": {"ok", "session_null", "model_unknown", "window_unknown", "tasks_active", "process_active", "authorization_missing", "config_invalid", "code_changed", "unsupported", "unsupported_platform", "unsupported_harness", "missing_hooks", "invalid_hooks", "invalid_configuration", "invalid_standing_authorization", "codex_trust_unverified", "error"},
    "font": {"system-ui", "sans-serif", "serif", "monospace", "other"},
}
FIELD_NAMES = {"project", "alias", "visibility", "status", "title", "name", "assignee", "due", "parent", "priority"}
ELEMENT_NAMES = {"column.finished", "column.working", "column.paused", "task.card", "window", "unknown"}

def root(component):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", component):
        raise ValueError("invalid component")
    if os.environ.get("FM_DIAGNOSTICS_HOME"):
        base = Path(os.environ["FM_DIAGNOSTICS_HOME"]).expanduser()
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "Library" / "Logs")) / "Firstmate" / "Diagnostics"
    elif __import__("sys").platform == "darwin":
        base = Path.home() / "Library" / "Logs" / "Firstmate" / "Diagnostics"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "firstmate" / "diagnostics"
    return base / component

class Logger:
    def __init__(self, component, version, revision, directory=None):
        self.component, self.version, self.revision = component, _version(version), _revision(revision)
        self.directory = Path(directory) if directory else root(component)
        self.salt = secrets.token_bytes(32)
        self.buffer = []

    def emit(self, event, **fields):
        schema = EVENTS.get(event)
        if schema is None:
            return False
        clean = {k: v for k, kind in schema.items() if k in fields and (v := _clean(k, kind, fields[k], self.salt)) is not None}
        self.buffer.append({"t": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"), "c": self.component,
                            "e": event, "v": self.version, "r": self.revision, **clean})
        if len(self.buffer) >= BATCH:
            self.flush()
        return True

    def flush(self):
        if not self.buffer:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / "events.jsonl"
        data = "".join(json.dumps(e, separators=(",", ":"), ensure_ascii=False) + "\n" for e in self.buffer).encode()
        if target.exists() and target.stat().st_size + len(data) > LIMIT // 2:
            old = self.directory / "events.1.jsonl"
            old.unlink(missing_ok=True)
            target.replace(old)
            if old.stat().st_size > LIMIT // 2:
                with old.open("rb") as source:
                    source.seek(old.stat().st_size - LIMIT // 2)
                    tail = source.read()
                boundary = tail.find(b"\n")
                old.write_bytes(tail[boundary + 1:] if boundary >= 0 else b"")
        with target.open("ab") as f:
            f.write(data)
        self.buffer.clear()

def _version(x): return x if isinstance(x, str) and re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-+][A-Za-z0-9.-]{1,16})?", x) else "unknown"
def _revision(x): return x if isinstance(x, str) and re.fullmatch(r"[0-9a-f]{7,40}", x) else "unknown"
def _clean(key, kind, value, salt):
    if kind == "enum": return value if isinstance(value, str) and value in ENUMS.get(key, set()) else None
    if kind == "count" and type(value) is int and value >= 0: return value
    if kind == "measure" and type(value) in (int, float) and abs(value) < 1e7: return round(value)
    if kind == "ratio" and type(value) in (int, float) and 0 <= value < 100: return round(value, 2)
    if kind == "length" and type(value) is int and value >= 0: return (value // 16) * 16
    if kind == "id" and isinstance(value, str): return "live-" + hmac.new(salt, value.encode(), hashlib.sha256).hexdigest()[:16]
    if kind == "name" and isinstance(value, str) and value in ELEMENT_NAMES: return value
    if kind == "names" and isinstance(value, (tuple, list)) and len(value) <= 32 and all(isinstance(x, str) and x in FIELD_NAMES for x in value): return list(value)
    if kind == "version": return _version(value)
    if kind == "revision": return _revision(value)
    return None

def export(components, downloads=None, preview=False):
    """Collect only known component event files; remap live ids with fresh salt."""
    salt, records, sources = secrets.token_bytes(32), [], []
    for component in components:
        directory = root(component).resolve()
        for name in ("events.jsonl", "events.1.jsonl"):
            path = directory / name
            if not path.is_file(): continue
            sources.append(path)
            for line in path.read_text(encoding="utf-8").splitlines():
                try: item = json.loads(line)
                except ValueError: continue
                if item.get("c") != component or item.get("e") not in EVENTS: continue
                cleaned = _export_record(item, salt)
                if cleaned is not None: records.append(cleaned)
    size = sum(p.stat().st_size for p in sources)
    stamps = [r.get("t", "") for r in records]
    result = f"{size} bytes, {min(stamps) if stamps else '-'}..{max(stamps) if stamps else '-'}, {len(records)} eventos, verificador: {'ok' if _verify(records) else 'falló'}"
    if preview: return result
    outdir = Path(downloads or Path.home() / "Downloads").expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
    path = outdir / f"diagnostico-{stamp}.zip"
    fd, temp = tempfile.mkstemp(dir=outdir, suffix=".zip"); os.close(fd)
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("events.jsonl", "".join(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n" for r in records))
        if not _verify_zip(temp): raise ValueError("canary verifier failed; export withheld")
        os.link(temp, path)
        os.unlink(temp)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return path

def _export_record(item, salt):
    event = item.get("e")
    if event not in EVENTS or item.get("c") not in ("extensiones", "firstmate", "deck", "day-attendant"):
        return None
    stamp = item.get("t")
    try: datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError): return None
    version, revision = _version(item.get("v")), _revision(item.get("r"))
    if version != item.get("v") or revision != item.get("r"): return None
    result = {"t": stamp, "c": item["c"], "e": event, "v": version, "r": revision}
    for key, kind in EVENTS[event].items():
        if key not in item: continue
        value = item[key]
        if kind == "id":
            if not isinstance(value, str) or not re.fullmatch(r"live-[0-9a-f]{16}", value): return None
            result[key] = "t-" + hmac.new(salt, value.encode(), hashlib.sha256).hexdigest()[:4]
        else:
            clean = _clean(key, kind, value, salt)
            if clean is None: return None
            result[key] = clean
    return result

def _verify(records):
    for r in records:
        if r.get("e") not in EVENTS or r.get("c") not in ("extensiones", "firstmate", "deck", "day-attendant"):
            return False
        if set(r) - ({"t", "c", "e", "v", "r"} | set(EVENTS[r["e"]])): return False
        try: datetime.datetime.fromisoformat(r["t"].replace("Z", "+00:00"))
        except (ValueError, AttributeError): return False
        if _version(r.get("v")) != r.get("v") or _revision(r.get("r")) != r.get("r"): return False
        for key, value in r.items():
            if key in ("t", "c", "e", "v", "r"): continue
            kind = EVENTS[r["e"]][key]
            if kind == "id":
                if not isinstance(value, str) or not re.fullmatch(r"t-[0-9a-f]{4}", value): return False
            elif _clean(key, kind, value, b"") != value: return False
    return True
def _verify_zip(path):
    try:
        with zipfile.ZipFile(path) as z:
            if z.namelist() != ["events.jsonl"]: return False
            return _verify([json.loads(x) for x in z.read("events.jsonl").decode().splitlines() if x])
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return False

def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="fm-diagnostics")
    p.add_argument("action", choices=("export", "preview", "path"))
    p.add_argument("components", nargs="*", default=["extensiones", "firstmate", "deck", "day-attendant"])
    p.add_argument("--downloads")
    a = p.parse_args(argv)
    if a.action == "path": print(root("extensiones")); return 0
    try:
        result = export(a.components, a.downloads, a.action == "preview")
    except (OSError, ValueError) as exc:
        print("fm-diagnostics: export withheld: " + str(exc), file=__import__("sys").stderr)
        return 1
    print(result); return 0

def emit(component, event, **fields):
    """Convenience API: create a logger bound to the extension package metadata."""
    key = (component, fields.pop("_version", "unknown"), fields.pop("_revision", "unknown"))
    logger = _LOGGERS.get(key)
    if logger is None:
        logger = _LOGGERS[key] = Logger(*key)
    return logger.emit(event, **fields)

_LOGGERS = {}
atexit.register(lambda: [logger.flush() for logger in _LOGGERS.values()])
