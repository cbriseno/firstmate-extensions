"""fm-context-relay: show, validate and change the context relay settings.

Exit status: 0 success, 1 invalid settings or refused change, 2 usage or
environment error, 3 Codex hooks prepared but activation not verified.
"""

import argparse
import glob
import json
import os
import sys

from . import VERSION
from . import common
from . import config
from . import install
from . import measure
from . import pane_close
from . import session
from . import assisted
from . import automatic
from . import thresholds
from . import activation
from . import intent

SET_HELP = """Atomically change a reviewed config/context-relay.json value and record date,
reason and approval in data/context-relay/config-changes.jsonl.
Agents must obtain explicit operator approval before using set; include that
approval in --approved. Measurement is evidence, never authorization.
Keys: max_session_hours, default_trigger, compactions_before_relay,
precompact_checkpoint, unknown_model, relay_mode, relay_policy,
models.<model>.<trigger|request_tokens|stop_new_work_tokens|window_tokens|compactions_before_relay>.
Token values accept 250000, 250k or 1.5M; --unset removes a key.
relay_policy is an atomic per-harness object. Pressure does not replace policy.
Use auto authorize for automatic relay; set cannot authorize it."""


def looks_like_home(path):
    return bool(path) and os.path.isfile(os.path.join(path, "AGENTS.md")) and os.path.isfile(os.path.join(path, "bin", "fm-lock.sh"))


def resolve_home(explicit):
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get("FM_HOME")
    if env:
        return os.path.abspath(env)
    cwd = os.getcwd()
    if looks_like_home(cwd):
        return cwd
    return None


def _sessions(paths, limit):
    files = glob.glob(os.path.join(paths.sessions, "*.json"))
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    out = []
    for path in files[:limit]:
        record = common.read_json(path)
        if isinstance(record, dict) and record.get("schema") == session.SCHEMA:
            out.append(record)
    return out


def _session_view(record, settings):
    request = record.get("request") or {}
    policy = session.relay_policy(settings, record.get("harness"))
    relay = record.get("relay") or {}
    if relay.get("policy") != policy or (policy and not relay):
        relay = {"policy": policy, "pending": False, "status": "awaiting_evaluation" if policy else "disabled"}
    return {
        "harness": record.get("harness"),
        "session_id": record.get("session_id"),
        "updated_at": record.get("updated_at"),
        "model": record.get("model"),
        "models_seen": record.get("models_seen"),
        "resident": record.get("resident"),
        "window_tokens_observed": record.get("window_tokens_observed"),
        "compactions": record.get("compactions"),
        "effective": record.get("effective"),
        "relay": relay,
        "request": {"level": request.get("level", 0), "opened_at": request.get("opened_at"), "reasons": request.get("reasons", [])[-3:]},
        "delivered": record.get("delivered"),
        "pending": [p.get("key") for p in record.get("pending") or []],
        "checkpoint": record.get("checkpoint"),
    }


def _model_view(settings, name):
    eff = settings.for_model(name)
    return {key: eff[key] for key in ("model", "known", "harness", "trigger", "compactions_before_relay", "request_tokens", "stop_new_work_tokens", "window_tokens", "sources")}


def cmd_show(args, paths):
    loaded = config.load(paths)
    settings = loaded.settings
    view = {
        "schema": "fm-context-relay-settings.v1",
        "home": paths.home,
        "config_path": paths.config,
        "status": loaded.status,
        "using": loaded.using,
        "errors": loaded.errors,
        "warnings": loaded.warnings,
        "top": {key: dict(zip(("value", "source"), settings.top(key))) for key in config.TOP_KEYS if key != "models"},
        "models": [_model_view(settings, name) for name in sorted(settings.models())],
        "unknown_model": _model_view(settings, ""),
        "sessions": [_session_view(r, settings) for r in _sessions(paths, args.sessions)],
        "phone_notifications": False,
        "automatic": automatic.view(paths),
        "thresholds": loaded.thresholds,
    }
    if args.json:
        print(json.dumps(view, indent=2, ensure_ascii=False))
        return 0
    status = {"ok": "valid", "missing": "missing (defaults)", "invalid": "INVALID"}[loaded.status]
    print("Configuration: %s (%s)" % (paths.config, status))
    for error in loaded.errors:
        print("  error: %s" % error)
    if loaded.status == "invalid":
        print("  using: %s" % ("last valid values" if loaded.using == "last-valid" else "valores default"))
    for warning in loaded.warnings:
        print("  warning: %s" % warning)
    for key in config.TOP_KEYS:
        if key == "models":
            continue
        value, source = settings.top(key)
        print("%s: %s [%s]" % (key, json.dumps(value), source))
    print("Models:")
    for item in view["models"] + [view["unknown_model"]]:
        name = item["model"] or "(model without settings: unknown_model=%s)" % settings.value("unknown_model")
        src = item["sources"]
        print("  %s: trigger=%s [%s], compaction_pressure=%s [%s], request=%s, stop_new_work=%s, window=%s" % (
            name, item["trigger"], src["trigger"], item["compactions_before_relay"], src["compactions_before_relay"],
            common.format_tokens(item["request_tokens"]), common.format_tokens(item["stop_new_work_tokens"]),
            common.format_tokens(item["window_tokens"])))
    print("Recent sessions:")
    if not view["sessions"]:
        print("  (none recorded)")
    for item in view["sessions"]:
        resident = item["resident"] or {}
        if resident.get("status") == "ok":
            context = "%s (%s)" % (common.format_tokens(resident.get("tokens")), resident.get("at"))
        else:
            context = "no data (%s)" % resident.get("reason")
        comp = item["compactions"] or {}
        eff = item["effective"] or {}
        request = item["request"]
        line = "  %s %s · model %s · context %s · compactions %s (%s) · trigger %s" % (
            item["harness"], (item["session_id"] or "")[:8], item["model"] or "unknown", context,
            comp.get("count", 0), comp.get("counting") or "?", eff.get("effective_trigger") or "?")
        if item["window_tokens_observed"]:
            line += " · observed window %s" % common.format_tokens(item["window_tokens_observed"])
        print(line)
        if request["level"]:
            print("    historical warning level %d (%s) since %s" % (request["level"], session.LEVEL_LABELS[request["level"]], request["opened_at"]))
        relay = item["relay"] or {}
        if relay.get("policy"):
            print("    relay policy: %s; safe point pending=%s; count=%s; window=%s (%s)" % (
                relay.get("status"), relay.get("pending"), relay.get("counting"),
                common.format_tokens(relay.get("window_tokens")), relay.get("window_source")))
        elif settings.value("relay_policy").get(item["harness"]):
            print("    relay policy configured: evaluation pending at next hook; historical warnings do not establish eligibility")
        if item["pending"]:
            print("    avisos pendientes: %s" % ", ".join(item["pending"]))
    print("Mode %s: hooks measure and warn; assisted relay requires confirmation and explicit commands (relay --help)." % settings.value("relay_mode"))
    return 0


def cmd_validate(args, paths):
    path = args.file or paths.config
    data = common.read_bytes(path)
    if data is None:
        if args.json:
            print(json.dumps({"path": path, "valid": not bool(args.file), "errors": ["file missing"] if args.file else [], "warnings": [], "using": "defaults"}))
        else:
            print("%s does not exist%s" % (path, "" if args.file else ": using default values"))
        return 1 if args.file else 0
    obj, parse_error = config.parse_bytes(data)
    errors, warnings = ([parse_error], []) if parse_error else config.validate(obj)
    if not errors and obj.get('relay_mode') == 'auto':
        try:
            automatic.read_authority(paths)
        except pane_close.Preserve as exc:
            errors.append('auto requires durable authorization: '+str(exc))
    if not errors and not args.file:
        settings = config.Settings(obj, "file")
        for record in _sessions(paths, 20):
            observed = record.get("window_tokens_observed")
            model = record.get("model")
            if not observed or not model:
                continue
            eff = settings.for_model(model)
            if eff["known"]:
                if eff["window_tokens"] and eff["window_tokens"] != observed:
                    warnings.append("models.%s.window_tokens is %d but session %s observed %d" % (
                        eff["model"], eff["window_tokens"], record.get("session_id", "")[:8], observed))
                for field in ("request_tokens", "stop_new_work_tokens"):
                    if eff[field] and eff[field] >= observed:
                        errors.append("models.%s.%s (%d) cannot be reached: observed window is %d" % (
                            eff["model"], field, eff[field], observed))
    if args.json:
        print(json.dumps({"path": path, "valid": not errors, "errors": errors, "warnings": warnings}, indent=2, ensure_ascii=False))
    else:
        print("%s: %s" % (path, "valid" if not errors else "INVALID"))
        for error in errors:
            print("  error: %s" % error)
        for warning in warnings:
            print("  warning: %s" % warning)
    return 0 if not errors else 1


def cmd_set(args, paths):
    if args.key == 'relay_mode' and not args.unset and args.value == 'auto':
        print('refused: use auto authorize with the operator explicit words')
        return 1
    if os.path.islink(paths.config):
        print("error: set does not replace configuration symlinks", file=sys.stderr)
        return 2
    if not args.reason or not args.reason.strip():
        print("error: --reason is required", file=sys.stderr)
        return 2
    if not args.approved or not args.approved.strip():
        print("error: --approved is required: include explicit operator approval", file=sys.stderr)
        return 2
    if args.unset == (args.value is not None):
        print("error: provide a value or --unset, never both", file=sys.stderr)
        return 2
    data = common.read_bytes(paths.config)
    if data is None:
        obj = {}
    else:
        obj, parse_error = config.parse_bytes(data)
        if parse_error:
            print("error: %s: %s; repair manually (set does not rewrite unreadable files)" % (paths.config, parse_error), file=sys.stderr)
            return 1
        if not isinstance(obj, dict):
            print("error: %s does not contain a JSON object" % paths.config, file=sys.stderr)
            return 1
    try:
        parsed = config.parse_key(args.key)
        field = parsed[2] if parsed[0] == "model" else parsed[1]
        value = None if args.unset else config.parse_value(field, args.value)
        new, old = config.apply_change(obj, args.key, value, args.unset)
    except (ValueError, OverflowError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    errors, warnings = config.validate(new)
    if errors:
        print("refused: result would be invalid:")
        for error in errors:
            print("  error: %s" % error)
        return 1
    if new == obj:
        print("unchanged: %s already equals %s" % (args.key, json.dumps(old)))
        return 0
    shown_new = "(quitado)" if args.unset else json.dumps(value, ensure_ascii=False)
    if args.dry_run:
        print("dry run: %s: %s -> %s" % (args.key, json.dumps(old, ensure_ascii=False), shown_new))
        for warning in warnings:
            print("  warning: %s" % warning)
        return 0
    payload = common.dump_json(new)
    with common.FileLock(paths.config_lock, timeout=5.0):
        if common.read_bytes(paths.config) != data:
            print("error: %s changed while reading; run the command again" % paths.config, file=sys.stderr)
            return 1
        after = common.sha256_bytes(payload)
        entry = {
            "at": common.iso(),
            "kind": "set",
            "key": args.key,
            "old": old,
            "new": None if args.unset else value,
            "unset": bool(args.unset),
            "reason": args.reason.strip(),
            "approved": args.approved.strip(),
            "sha256_before": common.sha256_bytes(data) if data is not None else None,
            "sha256_after": after,
            "user": os.environ.get("USER"),
        }
        # Audit before replacement: a failed write can leave an intent, never
        # a configuration change with no reason/approval recorded.
        common.append_line(paths.changes_log, json.dumps(entry, ensure_ascii=False))
        common.atomic_write_bytes(paths.config, payload)
        common.atomic_write_json(paths.config_seen, {"sha256": after, "at": entry["at"]})
        common.atomic_write_json(paths.last_valid, {"sha256": after, "at": entry["at"], "settings": new})
    print("written: %s: %s -> %s" % (args.key, json.dumps(old, ensure_ascii=False), shown_new))
    print("registro: %s" % paths.changes_log)
    for warning in warnings:
        print("  warning: %s" % warning)
    return 0


def cmd_measure(args, paths):
    if not os.path.isfile(args.transcript):
        print("error: does not exist %s" % args.transcript, file=sys.stderr)
        return 2
    state = measure.scan(None, os.path.abspath(args.transcript), args.harness, 600.0)
    summary = measure.summarize(args.harness, state)
    if paths is not None:
        loaded = config.load(paths)
        summary["thresholds"] = _model_view(loaded.settings, summary.get("model") or "")
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0
    res = summary["resident"]
    comp = summary["compactions"]
    print("Transcript: %s (%s lines)" % (args.transcript, summary["lines"]))
    print("Model: %s · inicio %s" % (summary["model"] or "unknown", summary["started_at"]))
    if res["status"] == "ok":
        print("Resident context: %s (llamada %s)" % (common.format_tokens(res["tokens"]), res.get("at")))
    else:
        print("Resident context: no data (%s)" % res["reason"])
    if summary["window_tokens"]:
        print("Ventana reportada: %s" % common.format_tokens(summary["window_tokens"]))
    print("Compactions: %d (%s, signal %s)" % (comp["count"], comp["counting"], comp["signal"]))
    for item in comp["recent"]:
        print("  %s" % json.dumps(item, ensure_ascii=False))
    if "thresholds" in summary:
        t = summary["thresholds"]
        print("Effective thresholds: trigger=%s, compactions=%s, request=%s, stop_new_work=%s%s" % (
            t["trigger"], t["compactions_before_relay"], common.format_tokens(t["request_tokens"]),
            common.format_tokens(t["stop_new_work_tokens"]), "" if t["known"] else " (model without settings)"))
    return 0


def cmd_install(args, paths):
    if args.assisted:
        return activation.run(args, paths)
    if args.backup_dir or args.runtime_root:
        raise RuntimeError('--backup-dir and --runtime-root require --assisted')
    return install.install(paths.home, args.harness, dry_run=args.dry_run, force=args.force, codex_home=args.codex_home)


def cmd_uninstall(args, paths):
    return install.uninstall(paths.home, args.harness, dry_run=args.dry_run, codex_home=args.codex_home)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="fm-context-relay",
        description="Context measurement and explicit assisted relay for Firstmate (default warn).",
    )
    parser.add_argument("--home", help="Firstmate home (default $FM_HOME or the current compatible checkout)")
    parser.add_argument("--version", action="version", version="fm-context-relay " + VERSION)
    sub = parser.add_subparsers(dest="command")
    pane_close.add_parser(sub)
    assisted.add_parser(sub)
    automatic.add_parser(sub)
    thresholds.add_parser(sub)
    intent.add_parser(sub)

    p = sub.add_parser("show", help="effective settings, provenance and measured sessions")
    p.add_argument("--json", action="store_true")
    p.add_argument("--sessions", type=int, default=5, help="number of recent sessions to show")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("validate", help="validate schema and thresholds against context window")
    p.add_argument("--file", help="validate this file instead of the home configuration")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("set", help="change one setting (explicit operator approval required)", description=SET_HELP,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("key")
    p.add_argument("value", nargs="?")
    p.add_argument("--unset", action="store_true")
    p.add_argument("--reason", required=False)
    p.add_argument("--approved", required=False, help="explicit operator approval, with date")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("measure", help="measure a transcript without writing")
    p.add_argument("--harness", choices=("claude", "codex"), required=True)
    p.add_argument("transcript")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_measure)

    p = sub.add_parser("install", help="add owned hook entries to the home")
    p.add_argument("--harness", choices=("claude", "codex"), default="claude")
    p.add_argument("--codex-home", help="Codex user directory (default CODEX_HOME or the Codex configuration directory in your user directory); prepares hooks, never trust")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="allow Claude Code older than the measured minimum")
    p.add_argument("--assisted", action="store_true", help="prepare Claude assisted settings, thresholds and skills; never start relay")
    p.add_argument("--backup-dir", help="new directory for byte-exact backup and rollback manifest")
    p.add_argument("--runtime-root", help="stable context-relay hook runtime; applying requires this exact version")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser('rollback', help='restore an install --assisted backup; refuse later edits')
    p.add_argument('backup_dir')
    p.add_argument('--dry-run', action='store_true')
    p.set_defaults(func=activation.rollback)

    p = sub.add_parser("uninstall", help="remove owned hook entries only")
    p.add_argument("--harness", choices=("claude", "codex"), default="claude")
    p.add_argument("--codex-home", help="Codex user directory (default CODEX_HOME or the Codex configuration directory in your user directory)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_uninstall)
    return parser


def main(argv, sanitized=False):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == 'relay' and args.action == 'start' and not sanitized:
        # Firstmate supports Python-hosted harnesses and classifies their argv.
        # Keep user-provided harness names/quotes out of this interpreter's
        # argv so supported lock release reaches the real native ancestor.
        # exec preserves ancestry; no process identity or authority is forged.
        env = dict(os.environ, FM_CONTEXT_RELAY_ARGUMENTS=json.dumps(argv))
        os.execve(sys.executable, [sys.executable, '-I', os.path.join(install.ext_root(), 'lib/fm_context_relay_main.py'),
                                  'cli-start'], env)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    home = resolve_home(args.home)
    if home is None:
        if args.command == "measure":
            return cmd_measure(args, None)
        print("error: Firstmate home unavailable; use --home <path> or set FM_HOME", file=sys.stderr)
        return 2
    if args.command in ("install", "uninstall") and not os.path.isdir(home):
        print("error: %s does not exist" % home, file=sys.stderr)
        return 2
    try:
        return args.func(args, common.Paths(home))
    except (OSError, RuntimeError, ValueError, common.LockTimeout) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
