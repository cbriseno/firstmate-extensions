"""Post-acceptance Herdr pane finalizer; CLI help is the protocol owner.

No transfer, launcher, lock acquisition, native exit, signal or daemon control.
Native identity is verified locally; Firstmate session-lock owners are read-only.
Linux /proc and the original shell's input accounting guard a successor's
explicit, durable confirmation that the retired terminal is empty.
"""

import array
import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import termios

from . import common, identity as native_identity, process_inspection as processes

HELP = """Post-acceptance finalizer: local Herdr; unknown safety evidence preserves the pane.
This command does not transfer authority, start sessions, signal or acquire locks.
Before retiring, the native outgoing owner runs pane-close prepare with --handoff
and an explicit --herdr-session. It validates authorization.json, native startup,
owner identity, same-tab distinct panels and original terminal, and appends final
successor instructions. It never replaces transfer policy or safe-point approval.
After durable acceptance and verified native exit, the successor inspects the exact
retired panel with pane-close inspect. Only an empty, dedicated original shell can
be confirmed using pane-close ready --observed-screen-sha256 HASH --confirmation TEXT.
The confirmation must be the successor's own observation, bound to the shown screen.
finalize requires unchanged screen, no intervening shell input read, empty input
queue, same terminal/socket/boot, no additional tty/session process and absent old
frontend. Any uncertainty preserves the panel; inspect/confirm again if necessary.
Old receipts alone do not establish current native ownership or process death.
SID, process start time, cwd, own lock, startup, pane and tty must agree currently.
Only the captured pane ID may close. Structured absence in the same backend with
its original shell absent is idempotent success. A moved live shell is preserved.
Herdr has no compare-and-close primitive: exclusive access to the retired terminal
is required through finalization. Revalidation immediately precedes the close.
Lab sessions require --herdr-lab-helper; no default-session fallback or lifecycle
operation is provided here. Exit 0 closed/absent/prepared/ready, 1 preserved or
unconfirmed outcome, 2 usage/environment. Unverifiable close results are never
reported as successful. Old packages lacking preparation remain preserved."""


class Preserve(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise Preserve(reason)


def load(path):
    require(path.resolve() == path and not path.is_symlink(), "symlink or noncanonical path: " + str(path))
    value = common.read_json(str(path))
    require(isinstance(value, dict), "valid JSON object missing: " + str(path))
    return value


def process(pid):
    require(type(pid) is int and pid > 1, "invalid PID")
    return processes.process(pid)


def identity(pid):
    row = process(pid)
    require(row is not None and row["state"] != "Z", "proceso ausente: %s" % pid)
    result = {key: row[key] for key in ("pid", "start", "session", "tty")}
    executable = processes.proc_path(pid, 'exe').stat()
    result["executable"] = [executable.st_dev, executable.st_ino]
    result["argv_sha256"] = common.sha256_bytes(processes.argv_bytes(pid))
    return result


def input_counters(shell):
    counters = dict(line.split(":", 1) for line in processes.proc_path(shell['pid'], 'io').read_text().splitlines())
    return dict(rchar=int(counters["rchar"]), syscr=int(counters["syscr"]))


class Backend:
    def __init__(self, args):
        self.session = args.herdr_session
        self.helper = args.herdr_lab_helper
        require(os.environ.get("HERDR_ENV") == "1", "caller must be inside Herdr")
        require(self.session and not self.session.startswith("-"), "explicit Herdr session required")
        if self.helper:
            require(os.path.isabs(self.helper) and self.session.startswith("fm-lab-"), "helper requires a named lab")
        else:
            require(not self.session.startswith("fm-lab-"), "lab requires --herdr-lab-helper")

    def call(self, *args, missing=False):
        result = self.invoke(args)
        try:
            obj = json.loads(result.stdout or result.stderr)
        except ValueError:
            raise Preserve("respuesta Herdr ilegible: " + result.stderr[-300:]) from None
        if missing and result.returncode == 1 and obj.get("error", {}).get("code") == "pane_not_found":
            return None
        require(result.returncode == 0 and "error" not in obj, "Herdr refused %s: %s" % (args[0], obj))
        return obj.get("result", obj)

    def invoke(self, args):
        argv = ([self.helper, "run", self.session] if self.helper else ["herdr"])
        argv += list(args)
        if not self.helper:
            argv += ["--session", self.session]
        return subprocess.run(argv, text=True, capture_output=True, timeout=15)

    def screen(self, pane):
        result = self.invoke(["pane", "read", pane, "--source", "visible", "--lines", "200"])
        require(result.returncode == 0 and result.stdout.strip(), "screen capture missing or unreadable")
        return result.stdout

    def incarnation(self):
        server = self.call("status", "--json").get("server", {})
        require(server.get("running") is True and server.get("session") == self.session
                and server.get("compatible") is True, "servidor Herdr no coincide/compatible")
        socket = server.get("socket")
        require(isinstance(socket, str) and os.path.isabs(socket), "socket Herdr unknown")
        info = os.stat(socket)
        require(stat.S_ISSOCK(info.st_mode), "Herdr endpoint is not a socket")
        return dict(session=self.session, socket=socket, device=info.st_dev, inode=info.st_ino,
                    ctime_ns=info.st_ctime_ns, boot=processes.boot_id())

    def pane(self, pane):
        value = self.call("pane", "get", pane, missing=True)
        return value["pane"] if value is not None else None

    def terminal(self, pane):
        value = self.pane(pane)
        require(value is not None, "pane absent during capture")
        require(value.get("pane_id") == pane, "pane identity changed; pane alias moved")
        info = self.call("pane", "process-info", "--pane", pane)["process_info"]
        return dict(pane=pane, terminal=value["terminal_id"], tab=value["tab_id"],
                    workspace=value["workspace_id"], shell=identity(info["shell_pid"]))


def current_owner(home, harness, backend, pane):
    require(not os.environ.get("FM_TASK_ID"), "worker cannot finalize the primary")
    require(harness in ("codex", "claude"), "unsupported harness")
    sid, pid, _, cwd, _, _ = native_identity.native(harness, home, {})
    require(cwd == home, "native session belongs to another home")
    state = home / "state"
    for name in (".lock", ".session-start-complete"):
        target = state / name
        require(target.resolve() == target and target.read_text().strip() == str(pid), "current ownership/startup mismatch: " + name)
    sidecar = state / ".lock-session"
    if sidecar.exists() or harness == "claude":
        require(not sidecar.is_symlink() and sidecar.read_text().strip() == sid, "unrelated lock-session")
    # native() already proved this exact PID in the caller's real ancestry.
    # Use the lock owner PID predicate, rather than re-walking through Python
    # argv: a confirmation containing "codex" would itself match that walk.
    check = subprocess.run(["bash", "-c", '''
. "$1/bin/fm-primary-scope-lib.sh" && . "$1/bin/fm-session-lock-lib.sh" &&
fm_primary_scope_matches "$1" "$1/state" && fm_harness_pid_alive "$2"
''', "_", str(home), str(pid)], capture_output=True, timeout=15)
    require(check.returncode == 0, "native scope/lock owner does not corroborate ownership")
    require(os.environ.get("HERDR_PANE_ID") == pane and os.environ.get("HERDR_SESSION") == backend.session,
            "caller is not the expected owner pane/session")
    terminal = backend.terminal(pane)
    native = identity(pid)
    require(native["tty"] != 0 and native["tty"] == terminal["shell"]["tty"]
            and native["session"] == terminal["shell"]["session"], "native owner does not belong to terminal")
    return dict(sid=sid, process=native, pane=pane)


def empty_shell(backend, captured):
    require(backend.terminal(captured["pane"]) == captured, "pane/terminal/shell identity changed")
    shell = captured["shell"]
    row = process(shell["pid"])
    require(row and row["state"] == "S", "shell is not waiting for input")
    info = backend.call("pane", "process-info", "--pane", captured["pane"])["process_info"]
    require(info.get("foreground_process_group_id") == row["group"]
            and [p["pid"] for p in info.get("foreground_processes", [])] == [shell["pid"]], "panel ocupado en primer plano")
    for pid in processes.pids():
        if pid <= 1:
            continue
        other = process(pid)
        if other and other["state"] != "Z" and (other["session"] == shell["session"] or other["tty"] == shell["tty"]):
            require(other["pid"] == shell["pid"], "another process in pane session/tty: %s" % other["pid"])
    # Opening the slave without reading leaves terminal contents untouched.
    fd = os.open(processes.proc_path(shell["pid"], "fd/0"), os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
    try:
        # TIOCGPGRP requires the caller's controlling tty on Linux; /proc stat
        # exposes the original tty's foreground group across successor sessions.
        require(os.isatty(fd) and row["foreground"] == row["group"], "ambiguous shell tty/group")
        require(not termios.tcgetattr(fd)[3] & termios.ICANON, "canonical queue cannot prove partial input absence")
        pending = array.array("i", [0])
        fcntl.ioctl(fd, termios.FIONREAD, pending, True)
        require(pending[0] == 0, "pending tty input")
    finally:
        os.close(fd)
    return input_counters(shell)


def receipts(directory, auth, owner):
    ready = load(directory / "successor-ready.json")
    accepted = load(directory / "successor-accepted.json")
    retired = load(directory / "outgoing-retired.json")
    require((ready.get("sid"), ready.get("pid"), ready.get("process_start"), ready.get("pane")) ==
            (owner["sid"], owner["process"]["pid"], owner["process"]["start"], auth["new_pane"]), "stale or mismatched durable receipt")
    require((accepted.get("old_sid"), accepted.get("old_frontend_pid"), accepted.get("old_pane"), accepted.get("successor_pid"),
             accepted.get("successor_sid"), accepted.get("successor_start")) ==
            (auth["old_sid"], auth["old_frontend_pid"], auth["old_pane"], owner["process"]["pid"],
             owner["sid"], owner["process"]["start"]), "durable acceptance mismatch")
    require((retired.get("sid"), retired.get("role"), retired.get("successor_pane")) ==
            (auth["old_sid"], "read-only retired primary", auth["new_pane"]), "safe outgoing retirement missing")
    old = process(auth["old_frontend_pid"])
    require(old is None, "frontend anterior vivo, zombie o PID reutilizado; preservar")
    return {name: common.sha256_bytes(common.dump_json(value)) for name, value in
            (("ready", ready), ("accepted", accepted), ("retired", retired))}


def run(args, paths):
    try:
        return execute(args, paths)
    except (Preserve, OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status="preserved", reason=str(exc)), ensure_ascii=False))
        return 1


def execute(args, paths):
    home = Path(paths.home)
    directory = Path(args.handoff)
    require(home.is_absolute() and home.resolve() == home and directory.is_absolute()
            and directory.resolve() == directory, "home/package must be canonical absolute paths")
    auth = load(directory / "authorization.json")
    require(auth.get("home") == str(home) and isinstance(auth.get("authority"), str)
            and auth["authority"].strip(), "authority does not match home")
    require(auth["old_pane"] != auth["new_pane"], "outgoing and successor panes are identical")
    backend = Backend(args)
    prepared_path = directory / "pane-close-prepared.json"
    with common.FileLock(str(directory / ".pane-close.lock")):
        if args.action == "prepare":
            owner = current_owner(home, auth["harness"], backend, auth["old_pane"])
            require(owner["sid"] == auth["old_sid"] and owner["process"]["pid"] == auth["old_frontend_pid"]
                    and owner["process"]["start"] == auth["old_frontend_start"], "primario anterior no coincide")
            old, new = backend.terminal(auth["old_pane"]), backend.terminal(auth["new_pane"])
            require(old["tab"] == new["tab"] == auth["tab"], "tab mismatch")
            require(old["terminal"] != new["terminal"] and old["shell"]["pid"] != new["shell"]["pid"]
                    and old["shell"]["tty"] != new["shell"]["tty"], "successor shares outgoing terminal/shell")
            prepared = dict(version=1, home=str(home), authorization=auth, backend=backend.incarnation(),
                            predecessor=old, successor=new)
            if prepared_path.exists():
                require(load(prepared_path) == prepared, "previous capture differs; preserved")
            else:
                common.atomic_create_bytes(str(prepared_path), common.dump_json(prepared), 0o600)
            prompt = directory / "successor-prompt.txt"
            require(prompt.resolve() == prompt and prompt.is_file(), "original successor-prompt.txt missing")
            template = Path(__file__).resolve().parents[2] / "templates/successor-pane-close.txt"
            cli = Path(__file__).resolve().parents[2] / "bin/fm-context-relay"
            argv = [str(cli), "--home", str(home), "pane-close", "{action}", "--handoff", str(directory),
                    "--herdr-session", backend.session]
            if backend.helper:
                argv += ["--herdr-lab-helper", backend.helper]
            command = " ".join(shlex.quote(a) for a in argv)
            tail = template.read_text().replace("@READY@", command.replace("'{action}'", "ready"))
            tail = tail.replace("@INSPECT@", command.replace("'{action}'", "inspect"))
            tail = tail.replace("@FINALIZE@", command.replace("'{action}'", "finalize"))
            original = prompt.read_text()
            marker = "[fm-context-relay:predecessor-pane-close:v1]"
            require(marker not in original or original.endswith(tail), "previous final step mismatch")
            if marker not in original:
                common.atomic_write_bytes(str(prompt), (original.rstrip() + "\n\n" + tail).encode(), 0o600)
            print(json.dumps(dict(status="prepared", successor_prompt=str(prompt))))
            return 0
        prepared = load(prepared_path)
        require(prepared.get("version") == 1 and prepared.get("authorization") == auth
                and prepared.get("home") == str(home), "stale or insufficient capture/authority")
        require(backend.incarnation() == prepared["backend"], "backend incarnation changed")
        owner = current_owner(home, auth["harness"], backend, auth["new_pane"])
        require(owner["sid"] != auth["old_sid"] and owner["process"]["pid"] != auth["old_frontend_pid"], "successor is not distinct")
        require(backend.terminal(auth["new_pane"]) == prepared["successor"], "successor pane reused")
        proof = receipts(directory, auth, owner)
        pane = backend.pane(auth["old_pane"])
        result_path = directory / "pane-close-result.json"
        if pane is None:
            require(backend.incarnation() == prepared["backend"], "backend changed while checking absence")
            require(process(prepared["predecessor"]["shell"]["pid"]) is None,
                    "panel ausente pero shell original vivo/reutilizado; posible traslado, preservar")
            if not result_path.exists():
                common.atomic_write_json(str(result_path), dict(status="already-closed", at=common.iso(), owner=owner))
            print(json.dumps(dict(status="already-closed", pane=auth["old_pane"])))
            return 0
        current = empty_shell(backend, prepared["predecessor"])
        screen = backend.screen(auth["old_pane"])
        screen_hash = common.sha256_bytes(screen.encode())
        if args.action == "inspect":
            print(json.dumps(dict(status="inspected", pane=auth["old_pane"],
                                  original_shell_verified=True, frontend_absent=True,
                                  other_tty_processes=0, pending_tty_input_bytes=0, screen=screen,
                                  screen_sha256=screen_hash), ensure_ascii=False))
            return 0
        ready_path = directory / "pane-close-ready.json"
        if args.action == "ready":
            require(args.observed_screen_sha256 == screen_hash, "screen changed since inspection; preserve")
            require(isinstance(args.confirmation, str) and 20 <= len(args.confirmation.strip()) <= 1000,
                    "explicit owner confirmation after inspecting empty dedicated shell required")
            ready = dict(version=1, at=common.iso(), owner=owner, receipts=proof, shell_input=current,
                         prepared_sha256=common.sha256_bytes(common.dump_json(prepared)), confirmation=args.confirmation.strip(),
                         screen_sha256=screen_hash)
            # Keep prior confirmations as evidence instead of silently replacing them.
            if ready_path.exists():
                common.append_line(str(directory / "pane-close-ready-history.jsonl"), json.dumps(load(ready_path)))
            common.atomic_write_json(str(ready_path), ready, 0o600)
            print(json.dumps(dict(status="ready", pane=auth["old_pane"])))
            return 0
        ready = load(ready_path)
        require(ready.get("version") == 1 and ready.get("owner") == owner and ready.get("receipts") == proof
                and ready.get("prepared_sha256") == common.sha256_bytes(common.dump_json(prepared))
                and isinstance(ready.get("confirmation"), str) and len(ready["confirmation"]) >= 20,
                "stale or mismatched durable confirmation")
        require(ready.get("shell_input", {}).get("rchar") == current["rchar"], "shell read input since confirmation; inspect again")
        require(ready.get("screen_sha256") == screen_hash, "screen changed since confirmation")
        # No bypass on retry: recheck the current owner, backend, retirement and shell.
        require(current_owner(home, auth["harness"], backend, auth["new_pane"]) == owner
                and backend.incarnation() == prepared["backend"] and receipts(directory, auth, owner) == proof
                and empty_shell(backend, prepared["predecessor"])["rchar"] == current["rchar"]
                and common.sha256_bytes(backend.screen(auth["old_pane"]).encode()) == screen_hash, "state changed before close")
        try:
            backend.call("pane", "close", auth["old_pane"])
            require(backend.pane(auth["old_pane"]) is None, "close sent but absence unconfirmed")
            result = dict(status="closed", at=common.iso(), pane=auth["old_pane"], owner=owner,
                          prepared_sha256=ready["prepared_sha256"])
            common.atomic_write_json(str(result_path), result, 0o600)
        except (Preserve, OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
            print(json.dumps(dict(status="unconfirmed", pane=auth["old_pane"], reason=str(exc)), ensure_ascii=False))
            return 1
        print(json.dumps(result))
        return 0


def add_parser(sub):
    parser = sub.add_parser("pane-close", help="finalize only the retired pane after accepting the role",
                            description=HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("prepare", "inspect", "ready", "finalize"):
        item = actions.add_parser(action, description=HELP,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
        item.add_argument("--handoff", required=True)
        item.add_argument("--herdr-session", required=True)
        item.add_argument("--herdr-lab-helper")
        if action == "ready":
            item.add_argument("--confirmation", required=True)
            item.add_argument("--observed-screen-sha256", required=True)
        item.set_defaults(func=run)
