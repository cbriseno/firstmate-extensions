"""Settings for the context relay: schema, validation, effective values.

The settings file is $FM_HOME/config/context-relay.json. A missing file means
the built-in defaults below, which equal the shipped example. A file that
fails validation is never used: the hook keeps the last valid settings it saw
(or the defaults) and tells the orchestrator once per session.
"""

import copy
import json
import math
import os
import re
from pathlib import Path

from . import common

TRIGGERS = ("tokens", "compactions", "first")
RELAY_MODES = ("warn", "assisted", "auto")
UNKNOWN_MODEL_MODES = ("warn_only",)

TOP_KEYS = (
    "max_session_hours",
    "default_trigger",
    "compactions_before_relay",
    "precompact_checkpoint",
    "models",
    "unknown_model",
    "relay_mode",
    "relay_policy",
    "relay_event_command",
    "notification_adapter",
)
MODEL_KEYS = ("trigger", "request_tokens", "stop_new_work_tokens", "window_tokens", "compactions_before_relay")
TOKEN_KEYS = ("request_tokens", "stop_new_work_tokens", "window_tokens")

# Values chosen by the captain on 28/10 and 29/10 (plan section 4.A).
DEFAULTS = {
    "max_session_hours": 24,
    "default_trigger": "compactions",
    "compactions_before_relay": 2,
    "precompact_checkpoint": True,
    "models": json.loads((Path(__file__).resolve().parents[2] / "model-catalog.json").read_text()),
    "unknown_model": "warn_only",
    "relay_mode": "warn",
    # Opt-in per home through the audited CLI; pressure thresholds above
    # remain independent of eligibility for a safe handoff.
    "relay_policy": {},
    "relay_event_command": [],
    "notification_adapter": [],
}

# After a compaction the orchestrator restarts at roughly 61-63k tokens on
# Claude and 31k on Codex with the current AGENTS.md (phase 0B, F2 and F3).
FLOOR_ADVICE_TOKENS = 100000
# Observed automatic compactions start near 95% of the window (phase 0A).
NEAR_WINDOW_RATIO = 0.9


def normalize_model(name):
    """Lower-case a model id and drop a trailing [variant] tag such as [1m]."""
    if not isinstance(name, str):
        return ""
    text = name.strip().lower()
    text = re.sub(r"\[[^\]]*\]$", "", text).strip()
    return text


def model_harness(model):
    name = normalize_model(model)
    if name.startswith("claude-"):
        return "claude"
    if name.startswith("gpt-") or name.startswith("codex-") or re.match(r"^o\d", name):
        return "codex"
    return None


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return (isinstance(value, (int, float))) and not isinstance(value, bool)


def validate(obj):
    """Return (errors, warnings) as lists of human-readable strings."""
    errors = []
    warnings = []
    if not isinstance(obj, dict):
        return (["file must contain a JSON object"], [])
    for key in obj:
        if key.startswith("_"):
            continue
        if key not in TOP_KEYS:
            errors.append("unknown key: %s" % key)
    if "max_session_hours" in obj:
        value = obj["max_session_hours"]
        if not _is_number(value) or not math.isfinite(value) or value <= 0:
            errors.append("max_session_hours must be a number greater than 0")
    if "default_trigger" in obj and obj["default_trigger"] not in TRIGGERS:
        errors.append("default_trigger must be one of: %s" % ", ".join(TRIGGERS))
    if "compactions_before_relay" in obj:
        value = obj["compactions_before_relay"]
        if not _is_int(value) or value < 1:
            errors.append("compactions_before_relay must be an integer >= 1")
    if "precompact_checkpoint" in obj and not isinstance(obj["precompact_checkpoint"], bool):
        errors.append("precompact_checkpoint must be true or false")
    if "unknown_model" in obj and obj["unknown_model"] not in UNKNOWN_MODEL_MODES:
        errors.append("unknown_model must be: %s" % ", ".join(UNKNOWN_MODEL_MODES))
    command = obj.get("relay_event_command", [])
    if (not isinstance(command, list) or len(command) > 32
            or any(not isinstance(arg, str) or not arg or '\0' in arg or len(arg) > 4096 for arg in command)
            or command and not os.path.isabs(command[0])):
        errors.append('relay_event_command must be [] or argv with an absolute executable, e.g. ["/path/consumer"]')
    if "relay_mode" in obj:
        mode = obj["relay_mode"]
        if mode not in RELAY_MODES:
            errors.append("relay_mode must be warn, assisted or auto (with durable authority)")
    if "notification_adapter" in obj:
        argv = obj["notification_adapter"]
        if (not isinstance(argv, list) or len(argv) > 32
                or any(not isinstance(a, str) or not a or '\0' in a for a in argv)
                or (argv and not os.path.isabs(argv[0]))):
            errors.append('notification_adapter must be [] or argv with absolute executable, without shell')
    policies = obj.get("relay_policy", {})
    if not isinstance(policies, dict):
        errors.append("relay_policy must be an object per harness")
    else:
        for harness, policy in policies.items():
            where = "relay_policy.%s" % harness
            if harness not in ("codex", "claude"):
                errors.append("%s: harness unknown" % where)
            if not isinstance(policy, dict):
                errors.append("%s must be an object" % where)
                continue
            for key in policy:
                if key not in ("compactions", "min_context_percent", "min_context_tokens", "operator"):
                    errors.append("%s: unknown key: %s" % (where, key))
            count = policy.get("compactions")
            if "compactions" in policy and (not _is_int(count) or count < 1):
                errors.append("%s.compactions must be an integer >= 1" % where)
            if not any(k in policy for k in ("compactions", "min_context_tokens", "min_context_percent")):
                errors.append("%s requires compactions or a threshold" % where)
            if "min_context_tokens" in policy:
                tokens = policy["min_context_tokens"]
                if not _is_int(tokens) or tokens < 1:
                    errors.append("%s.min_context_tokens must be a positive integer" % where)
            if "min_context_tokens" in policy and "min_context_percent" in policy:
                errors.append("%s: use tokens or percentage, never both" % where)
            if "operator" in policy:
                if policy["operator"] not in ("and", "or"):
                    errors.append("%s.operator must be and or or" % where)
                if "compactions" not in policy or not any(k in policy for k in ("min_context_tokens", "min_context_percent")):
                    errors.append("%s: connector requires two conditions" % where)
            if "min_context_percent" in policy:
                percent = policy["min_context_percent"]
                if not _is_number(percent) or not math.isfinite(percent) or not 0 < percent <= 100:
                    errors.append("%s.min_context_percent must be greater than 0 and at most 100" % where)
            if harness == "codex" and "compactions" in policy:
                warnings.append("%s: relay requires verified counting; token fallback only reports pressure" % where)
    default_trigger = obj.get("default_trigger", DEFAULTS["default_trigger"])
    models = obj.get("models", DEFAULTS["models"])
    if not isinstance(models, dict):
        errors.append("models must be an object")
        models = None
    for raw_name, entry in sorted((models or {}).items()):
        where = "models.%s" % raw_name
        name = normalize_model(raw_name)
        if not name:
            errors.append("%s: empty model name" % where)
            continue
        if name != raw_name:
            errors.append("%s: use normalized ID %s (lowercase, without [variant])" % (where, name))
        if not isinstance(entry, dict):
            errors.append("%s must be an object" % where)
            continue
        for key in entry:
            if key.startswith("_"):
                continue
            if key not in MODEL_KEYS:
                errors.append("%s: unknown key %s" % (where, key))
        for key in TOKEN_KEYS:
            if key in entry and (not _is_int(entry[key]) or entry[key] <= 0):
                errors.append("%s.%s must be a positive integer" % (where, key))
        if "compactions_before_relay" in entry:
            value = entry["compactions_before_relay"]
            if not _is_int(value) or value < 1:
                errors.append("%s.compactions_before_relay must be an integer >= 1" % where)
        trigger = entry.get("trigger", default_trigger)
        if "trigger" in entry and entry["trigger"] not in TRIGGERS:
            errors.append("%s.trigger must be one of: %s" % (where, ", ".join(TRIGGERS)))
            continue
        request = entry.get("request_tokens")
        stop = entry.get("stop_new_work_tokens")
        window = entry.get("window_tokens")
        if not all(v is None or _is_int(v) for v in (request, stop, window)):
            continue
        if trigger in ("tokens", "first") and request is None:
            errors.append("%s: trigger %s requires request_tokens" % (where, trigger))
        if request is not None and stop is not None and request >= stop:
            errors.append("%s: request_tokens (%d) must be less than stop_new_work_tokens (%d)" % (where, request, stop))
        if request is not None and stop is None:
            warnings.append("%s: without stop_new_work_tokens there is no second token level" % where)
        if window is not None:
            for key, value in (("request_tokens", request), ("stop_new_work_tokens", stop)):
                if value is not None and value >= window:
                    errors.append("%s.%s (%d) is unreachable: window is %d" % (where, key, value, window))
                elif value is not None and value >= NEAR_WINDOW_RATIO * window:
                    warnings.append(
                        "%s.%s (%d) is within 10%% of window (%d): automatic compaction may occur earlier"
                        % (where, key, value, window)
                    )
        if request is not None and request < FLOOR_ADVICE_TOKENS:
            warnings.append(
                "%s.request_tokens (%d) is near the post-compaction floor (~61-63k in Claude, ~31k in Codex)" % (where, request)
            )
        harness = model_harness(name)
        if trigger in ("compactions", "first"):
            if harness is None:
                errors.append("%s: trigger %s requires a harness with verified counting (claude or codex)" % (where, trigger))
            elif harness == "codex":
                warnings.append(
                    "%s: Codex counting requires a verified rollout contract; review provenance with measure (unverified preserves token pressure fallback)" % where
                )
    return (errors, warnings)


class Settings(object):
    """Effective settings with the origin of every value."""

    def __init__(self, obj, origin):
        self.raw = obj if isinstance(obj, dict) else {}
        self.origin = origin  # file, last-valid, defaults

    def top(self, key):
        if key in self.raw:
            return (self.raw[key], "file" if self.origin == "file" else self.origin)
        return (DEFAULTS[key], "default")

    def value(self, key):
        return self.top(key)[0]

    def models(self):
        if "models" in self.raw:
            return self.raw["models"] or {}
        return DEFAULTS["models"]

    def for_model(self, model):
        name = normalize_model(model)
        models = self.models()
        entry = models.get(name) if name else None
        known = entry is not None
        entry = entry or {}
        out = {"model": name, "known": known, "sources": {}}
        model_source = "modelo" if known else None

        def pick(key, fallback_key=None):
            if key in entry:
                out[key] = entry[key]
                out["sources"][key] = model_source
            elif fallback_key is not None:
                value, source = self.top(fallback_key)
                out[key] = value
                out["sources"][key] = source
            else:
                out[key] = None
                out["sources"][key] = None

        pick("trigger", "default_trigger")
        pick("compactions_before_relay", "compactions_before_relay")
        pick("request_tokens")
        pick("stop_new_work_tokens")
        pick("window_tokens")
        out["harness"] = model_harness(name)
        return out


class LoadResult(object):
    def __init__(self, settings, status, errors, warnings, sha, using):
        self.settings = settings
        self.status = status  # ok, missing, invalid
        self.errors = errors
        self.warnings = warnings
        self.sha = sha
        self.using = using  # file, last-valid, defaults


def parse_bytes(data):
    """Return (obj, error) for raw settings bytes."""
    try:
        return (json.loads(data.decode("utf-8")), None)
    except UnicodeDecodeError:
        return (None, "file is not UTF-8")
    except ValueError as exc:
        return (None, "invalid JSON: %s" % exc)


def load(paths, remember=False):
    from . import thresholds
    result = thresholds.overlay(paths, _load(paths, remember), remember)
    if result.settings.value("relay_mode") == "auto":
        from . import automatic, pane_close
        try:
            automatic.read_authority(paths)
        except (pane_close.Preserve, OSError, ValueError) as exc:
            raw = dict(result.settings.raw, relay_mode="warn")
            result.settings = Settings(raw, result.settings.origin)
            result.status = "invalid"
            result.errors.append('auto unauthorized: '+str(exc))
    return result


def _load(paths, remember=False):
    """Load the settings file; fall back to the last valid copy if invalid.

    With remember=True (the hook path) a valid file refreshes the last-valid
    cache and any content change not made by `set` is logged as an edit.
    """
    data = common.read_bytes(paths.config)
    if data is None:
        return LoadResult(Settings({}, "defaults"), "missing", [], [], None, "defaults")
    sha = common.sha256_bytes(data)
    obj, parse_error = parse_bytes(data)
    if parse_error:
        errors, warnings = ([parse_error], [])
    else:
        errors, warnings = validate(obj)
    if remember:
        _remember(paths, data, sha, obj, errors)
    if not errors:
        return LoadResult(Settings(obj, "file"), "ok", errors, warnings, sha, "file")
    cached = common.read_json(paths.last_valid)
    if isinstance(cached, dict) and isinstance(cached.get("settings"), dict):
        cached_errors, _ = validate(cached["settings"])
        if not cached_errors:
            return LoadResult(Settings(cached["settings"], "last-valid"), "invalid", errors, warnings, sha, "last-valid")
    return LoadResult(Settings({}, "defaults"), "invalid", errors, warnings, sha, "defaults")


def _remember(paths, data, sha, obj, errors):
    seen = common.read_json(paths.config_seen) or {}
    cached = common.read_json(paths.last_valid) or {}
    if seen.get("sha256") == sha and (errors or cached.get("sha256") == sha):
        return
    try:
        with common.FileLock(paths.config_lock, timeout=1.0):
            seen = common.read_json(paths.config_seen) or {}
            if seen.get("sha256") != sha:
                entry = {
                    "at": common.iso(),
                    "kind": "edit-detected",
                    "sha256": sha,
                    "previous_sha256": seen.get("sha256"),
                    "valid": not errors,
                    "reason": "external edit detected; reason and approval not recorded by CLI",
                }
                if errors:
                    entry["errors"] = errors[:5]
                common.append_line(paths.changes_log, json.dumps(entry, ensure_ascii=False))
                common.atomic_write_json(paths.config_seen, {"sha256": sha, "at": common.iso()})
            if not errors:
                cached = common.read_json(paths.last_valid) or {}
                if cached.get("sha256") != sha:
                    common.atomic_write_json(paths.last_valid, {"sha256": sha, "at": common.iso(), "settings": obj})
    except common.LockTimeout:
        pass


# --- `set` support ------------------------------------------------------------

def parse_key(key):
    """Return ("top", name) or ("model", model, field) for a settings path."""
    if key in TOP_KEYS and key != "models":
        return ("top", key)
    if key.startswith("models."):
        rest = key[len("models."):]
        for field in MODEL_KEYS:
            suffix = "." + field
            if rest.endswith(suffix) and len(rest) > len(suffix):
                return ("model", rest[: -len(suffix)], field)
        if rest:
            return ("model", rest, None)
    raise ValueError("unknown key: %s (use a top-level key or models.<model>.<field>)" % key)


def parse_value(field, text):
    """Parse a CLI value; token fields accept 250000, 250k or 1.5M."""
    if field in TOKEN_KEYS:
        match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([kKmM]?)\s*$", text.replace("_", ""))
        if not match:
            raise ValueError("%s expects a token count, e.g. 250000 or 250k" % field)
        number = float(match.group(1))
        unit = match.group(2).lower()
        number *= 1000 if unit == "k" else 1000000 if unit == "m" else 1
        if number != int(number):
            raise ValueError("%s must be an integer token count" % field)
        return int(number)
    try:
        return json.loads(text)
    except ValueError:
        return text


def apply_change(obj, key, value, unset=False):
    """Return (new_obj, old_value) without touching the input object."""
    new = copy.deepcopy(obj) if isinstance(obj, dict) else {}
    parsed = parse_key(key)
    if parsed[0] == "top":
        name = parsed[1]
        old = new.get(name)
        if unset:
            new.pop(name, None)
        else:
            new[name] = value
        return (new, old)
    _, model, field = parsed
    model_norm = normalize_model(model)
    if "models" not in new:
        new["models"] = copy.deepcopy(DEFAULTS["models"])
    if not isinstance(new["models"], dict):
        raise ValueError("models is not an object; repair manually first")
    models = new["models"]
    if field is None:
        old = models.get(model_norm)
        if not unset:
            raise ValueError("a whole model only supports --unset; use models.%s.<field>" % model_norm)
        models.pop(model_norm, None)
        return (new, old)
    entry = models.get(model_norm)
    if entry is None:
        entry = {}
        models[model_norm] = entry
    if not isinstance(entry, dict):
        raise ValueError("models.%s is not an object; repair manually first" % model_norm)
    old = entry.get(field)
    if unset:
        entry.pop(field, None)
    else:
        entry[field] = value
    return (new, old)
