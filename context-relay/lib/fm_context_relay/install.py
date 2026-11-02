"""Installer and uninstaller for the hook entries.

Claude Code: entries go into the home untracked .claude/settings.local.json,
merged into whatever is there. Each entry is one hook group holding one
command whose text ends with MARKER; the uninstaller removes exactly those
commands and the containers that only they filled, and nothing else.

Codex: prepare user hooks only. Trust review belongs to the person in /hooks;
preparation never establishes that the hooks are trusted or active.
"""

import copy
import difflib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

from . import common

MARKER = "#fm-context-relay"
CLAUDE_EVENTS = (
    ("PreToolUse", 15),
    ("SessionStart", 15),
    ("UserPromptSubmit", 10),
    ("PostToolBatch", 10),
    ("Stop", 10),
    ("PreCompact", 60),
    ("PostCompact", 15),
)
CODEX_EVENTS = (
    ("PreToolUse", 15),
    ("SessionStart", 15),
    ("UserPromptSubmit", 10),
    ("PostToolUse", 10),
    ("Stop", 10),
    ("PreCompact", 60),
    ("PostCompact", 15),
)
# Oldest Claude Code whose PostCompact, compact_boundary and PostToolBatch
# contracts phases 0A and 0B measured; 2.1.314 is the live-verified version.
MIN_CLAUDE = (2, 1, 312)
CODEX_PREPARED = 3


def ext_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def hook_bin():
    return os.path.join(ext_root(), "bin", "fm-context-relay-hook")


def example_config():
    return os.path.join(ext_root(), "context-relay.example.json")


def hook_command(home, harness, event):
    executable = os.path.join(ext_root(), "bin", "fm-context-relay-guard") if event == "PreToolUse" else hook_bin()
    return "%s --home %s %s %s %s" % (shlex.quote(executable), shlex.quote(home), harness, event, MARKER)


def is_ours(hook):
    return isinstance(hook, dict) and isinstance(hook.get("command"), str) and hook["command"].rstrip().endswith(MARKER)


def _git(home, *args):
    try:
        proc = subprocess.run(["git", "-C", home] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return (None, "")
    return (proc.returncode, proc.stdout.decode("utf-8", "replace").strip())


def check_home(home):
    """Return problems that make home not a Firstmate primary home."""
    problems = []
    for rel in ("AGENTS.md", "bin/fm-session-lock-lib.sh", "bin/fm-primary-scope-lib.sh", "bin/fm-lock.sh"):
        if not os.path.isfile(os.path.join(home, rel)):
            problems.append("missing %s: not a compatible Firstmate home" % rel)
    # The same plain-checkout test as bin/fm-primary-scope-lib.sh.
    rc, git_dir = _git(home, "rev-parse", "--git-dir")
    rc2, common_dir = _git(home, "rev-parse", "--git-common-dir")
    if rc != 0 or rc2 != 0:
        problems.append("%s is not a git checkout" % home)
    elif git_dir != common_dir and not os.path.isfile(os.path.join(home, ".fm-secondmate-home")):
        problems.append("%s is a linked worktree, not the primary checkout" % home)
    return problems


def claude_version():
    try:
        proc = subprocess.run(["claude", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", proc.stdout.decode("utf-8", "replace"))
    return tuple(int(x) for x in match.groups()) if match else None


def _load_settings(path):
    """Return (obj, raw_bytes, error)."""
    if os.path.islink(path):
        return (None, None, "%s is a symlink; installer never writes through symlinks" % path)
    data = common.read_bytes(path)
    if data is None:
        if os.path.exists(path):
            return (None, None, "cannot read %s" % path)
        return ({}, None, None)
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return (None, data, "%s is not valid JSON (%s); refusing to modify" % (path, exc))
    if not isinstance(obj, dict):
        return (None, data, "%s does not contain a JSON object; refusing to modify" % path)
    return (obj, data, None)


def _strip_ours(obj, owns=is_ours):
    """Remove our commands; return (new_obj, removed). Only containers emptied by
    that removal are dropped."""
    new = copy.deepcopy(obj)
    hooks = new.get("hooks")
    removed = 0
    if not isinstance(hooks, dict):
        return (new, 0)
    for event in list(hooks.keys()):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        touched = False
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(inner, list):
                kept_groups.append(group)
                continue
            kept = [h for h in inner if not owns(h)]
            if len(kept) != len(inner):
                removed += len(inner) - len(kept)
                touched = True
                if not kept:
                    continue
                group = dict(group)
                group["hooks"] = kept
            kept_groups.append(group)
        if touched:
            if kept_groups:
                hooks[event] = kept_groups
            else:
                del hooks[event]
    if removed and not hooks:
        del new["hooks"]
    return (new, removed)


def merge_claude(obj, home):
    """Return (new_obj, error) with exactly one entry of ours per event."""
    hooks = obj.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        return (None, "hooks key is not an object; refusing to modify")
    for event, _ in CLAUDE_EVENTS:
        if hooks and event in hooks and not isinstance(hooks[event], list):
            return (None, "hooks.%s is not a list; refusing to modify" % event)
    new, _ = _strip_ours(obj)
    hooks = new.setdefault("hooks", {})
    for event, timeout in CLAUDE_EVENTS:
        hooks.setdefault(event, []).append(
            {"hooks": [{"type": "command", "command": hook_command(home, "claude", event), "timeout": timeout}]}
        )
    return (new, None)


def _codex_ours(hook, home):
    """The user source is shared: another Firstmate home entries survive."""
    if not is_ours(hook):
        return False
    try:
        args = shlex.split(hook["command"])
    except ValueError:
        return False
    return len(args) == 6 and args[1:4] == ["--home", home, "codex"] and args[5] == MARKER


def merge_codex(obj, home):
    hooks = obj.get("hooks")
    if "hooks" in obj and not isinstance(hooks, dict):
        return (None, "hooks key is not an object; refusing to modify")
    for event, _ in CODEX_EVENTS:
        if hooks and event in hooks and not isinstance(hooks[event], list):
            return (None, "hooks.%s is not a list; refusing to modify" % event)
    desired = {
        event: {"type": "command", "command": hook_command(home, "codex", event), "timeout": timeout}
        for event, timeout in CODEX_EVENTS
    }
    current = []
    for event, groups in (hooks or {}).items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                current.extend((event, group.get("matcher"), h) for h in group["hooks"] if _codex_ours(h, home))
    # Another home later install must not cause ours to move on a no-op.
    # Preserve group positions and bytes when all definitions already fit.
    if (len(current) == len(desired) and {event for event, _, _ in current} == set(desired)
            and all(matcher in (None, "", "*") and h == desired[event] for event, matcher, h in current)):
        return (copy.deepcopy(obj), None)
    new, _ = _strip_ours(obj, lambda hook: _codex_ours(hook, home))
    hooks = new.setdefault("hooks", {})
    for event, handler in desired.items():
        hooks.setdefault(event, []).append({"hooks": [handler]})
    return (new, None)


def _codex_path(codex_home):
    directory = os.path.abspath(os.path.expanduser(codex_home or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")))
    path = os.path.join(directory, "hooks.json")
    _check_codex_path(path)
    return path


def _check_codex_path(path):
    # Check ancestors as well as the leaf: a .codex directory symlink must
    # not redirect installation, removal, or the preview to another home.
    if os.path.realpath(path) != path:
        raise RuntimeError("%s contains a symlink; refusing to modify" % path)
    rc, _ = _git(os.path.dirname(path), "ls-files", "--error-unmatch", "--", os.path.basename(path))
    if rc == 0:
        raise RuntimeError("%s is tracked by git; refusing to modify" % path)


def _codex_review(out):
    out("state: prepared; activation: UNVERIFIED (exit 3)")
    out("Human review: open /hooks in Codex in the trusted home; inspect this home #fm-context-relay entries and trust only the pending handlers you approve.")
    out("Pending trust and verified activation are separate: after review, verify positive injection and worker exclusion.")
    out("Warn and TUI/app-server token fallback remain; no trust is written and no session is started.")


def _install_codex(home, codex_home, dry_run, out):
    path = _codex_path(codex_home)
    obj, before, error = _load_settings(path)
    if error:
        out("error: " + error)
        return 2
    new, error = merge_codex(obj, home)
    if error:
        out("error: " + error)
        return 2
    if dry_run:
        out("dry run: %s %s" % ("would prepare" if new != obj else "would keep", path))
        if new != obj:
            for line in difflib.unified_diff(
                (before or b"").decode("utf-8").splitlines(),
                common.dump_json(new).decode("utf-8").splitlines(),
                fromfile=path, tofile=path + " (proposed)", lineterm="",
            ):
                out(line)
        for event, _ in CODEX_EVENTS:
            out("command: " + hook_command(home, "codex", event))
        config_path = os.path.join(home, "config", "context-relay.json")
        out("dry run: %s %s" % ("would preserve" if os.path.lexists(config_path) else "would create from example", config_path))
        out("A dry run does not prepare files, grant trust or verify activation.")
        return 0
    if new != obj:
        _check_codex_path(path)
        _write_if_unchanged(path, before, new)
        out("prepared: %d context-relay entries for this home in %s" % (len(CODEX_EVENTS), path))
    else:
        out("unchanged: this home entries were already prepared in %s" % path)
    _install_config(home, out)
    _codex_review(out)
    return CODEX_PREPARED


def _uninstall_codex(home, codex_home, dry_run, out):
    path = _codex_path(codex_home)
    if not os.path.lexists(path):
        out("unchanged: %s does not exist" % path)
        return 0
    obj, before, error = _load_settings(path)
    if error:
        out("error: " + error)
        return 2
    new, removed = _strip_ours(obj, lambda hook: _codex_ours(hook, home))
    if dry_run:
        out("dry run: would remove %d entries for this home from %s" % (removed, path))
        return 0
    if removed:
        _check_codex_path(path)
        _write_if_unchanged(path, before, new)
        out("uninstalled: %d entries for this home removed from %s" % (removed, path))
    else:
        out("unchanged: no entries for this home in %s" % path)
    out("Trust, config.toml, settings, requests, checkpoints and history retained.")
    return 0


def _settings_path(home):
    return os.path.join(home, ".claude", "settings.local.json")


def _write_if_unchanged(path, before, obj):
    if os.path.islink(path):
        raise RuntimeError("%s is a symlink; refusing to modify" % path)
    current = common.read_bytes(path)
    if current != before:
        raise RuntimeError("%s changed while reading; run the command again" % path)
    if before is None:
        if not common.atomic_create_bytes(path, common.dump_json(obj)):
            raise RuntimeError("%s appeared during preparation; refusing to modify" % path)
    else:
        common.atomic_write_bytes(path, common.dump_json(obj))


def install(home, harness, dry_run=False, force=False, out=print, codex_home=None):
    home = os.path.abspath(home)
    problems = check_home(home)
    if problems:
        for problem in problems:
            out("error: " + problem)
        return 2
    if harness == "codex":
        return _install_codex(home, codex_home, dry_run, out)
    if codex_home is not None:
        out("error: --codex-home requires --harness codex")
        return 2
    version = claude_version()
    if version is None:
        out("warning: cannot read `claude --version`; entries were verified with Claude Code 2.1.314")
    elif version < MIN_CLAUDE and not force:
        out("error: Claude Code %s is older than measured minimum %s; use --force only after review" % (
            ".".join(map(str, version)), ".".join(map(str, MIN_CLAUDE))))
        return 2
    path = _settings_path(home)
    rel = os.path.relpath(path, home)
    rc, _ = _git(home, "ls-files", "--error-unmatch", "--", rel)
    if rc == 0:
        out("error: %s is tracked by git; installer writes only the untracked local file" % rel)
        return 2
    obj, before, error = _load_settings(path)
    if error:
        out("error: " + error)
        return 2
    new, error = merge_claude(obj, home)
    if error:
        out("error: " + error)
        return 2
    changed = new != obj
    rc, _ = _git(home, "check-ignore", "-q", "--", rel)
    if rc != 0:
        out("aviso: %s is not gitignored in this home; it will appear in git status" % rel)
    config_path = os.path.join(home, "config", "context-relay.json")
    create_config = not os.path.lexists(config_path)
    if dry_run:
        out("dry run: %s %s" % ("would update" if changed else "would keep", path))
        out("dry run: %s" % ("would create %s from example" % config_path if create_config else "would preserve %s" % config_path))
        return 0
    if changed:
        common.ensure_dir(os.path.dirname(path))
        _write_if_unchanged(path, before, new)
        out("installed: %d context-relay entries in %s" % (len(CLAUDE_EVENTS), path))
    else:
        out("unchanged: context-relay entries already present in %s" % path)
    _install_config(home, out)
    out("Claude reloads settings.local.json without a session restart.")
    return 0


def _install_config(home, out):
    config_path = os.path.join(home, "config", "context-relay.json")
    if not os.path.lexists(config_path):
        data = common.read_bytes(example_config())
        paths = common.Paths(home)
        with common.FileLock(paths.config_lock):
            if not os.path.lexists(config_path):
                common.append_line(paths.changes_log, json.dumps({
                    "at": common.iso(), "kind": "install-defaults",
                    "reason": "create the initial example during install",
                    "approved": "installer invoked by operator",
                    "sha256_after": common.sha256_bytes(data),
                }, ensure_ascii=False))
                created = common.atomic_create_bytes(config_path, data)
            else:
                created = False
        out(("created: %s from example" if created else "preserved: %s (appeared during installation)") % config_path)
    else:
        out("preserved: %s (never overwritten)" % config_path)


def uninstall(home, harness, dry_run=False, out=print, codex_home=None):
    home = os.path.abspath(home)
    if harness == "codex":
        return _uninstall_codex(home, codex_home, dry_run, out)
    if codex_home is not None:
        out("error: --codex-home requires --harness codex")
        return 2
    path = _settings_path(home)
    rc, _ = _git(home, "ls-files", "--error-unmatch", "--", os.path.relpath(path, home))
    if rc == 0:
        out("error: settings.local.json is tracked; refusing to modify")
        return 2
    if not os.path.lexists(path):
        out("unchanged: %s does not exist" % path)
        return 0
    obj, before, error = _load_settings(path)
    if error:
        out("error: " + error)
        return 2
    new, removed = _strip_ours(obj)
    status_line = new.get('statusLine')
    if isinstance(status_line, dict) and isinstance(status_line.get('command'), str):
        try:
            args = shlex.split(status_line['command'])
        except ValueError:
            args = []
        if (len(args) == 6 and os.path.basename(args[0]) == 'fm-context-relay-statusline'
                and args[1:4] == ['--home', home, '--forward'] and args[5] == MARKER):
            if args[4]:
                new['statusLine'] = dict(status_line, command=args[4])
            else:
                del new['statusLine']
            removed += 1
    if dry_run:
        out("dry run: would remove %d entries from %s" % (removed, path))
        return 0
    if not removed:
        out("unchanged: no context-relay entries in %s" % path)
        return 0
    _write_if_unchanged(path, before, new)
    out("uninstalled: %d entries removed from %s" % (removed, path))
    out("Retaining config/context-relay.json, state/context-relay and data/context-relay.")
    return 0
