# shellcheck shell=bash
# Shared helpers for the context-relay tests. Source from a *.test.sh file.
#
# The fake homes use the REAL Firstmate session-lock and primary-scope
# libraries, copied from the checkout named by FIRSTMATE_ROOT (default: the
# home that contains this repository at <home>/projects/<repo>), so the
# ownership filter is tested against Firstmate's actual contract. A fake
# harness is a bash script named claude or codex: on Linux its process name is
# that file name, which is what Firstmate's ancestry walk matches.
set -u

TESTS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
EXT=$(cd "$TESTS_DIR/.." && pwd)
export CLI="$EXT/bin/fm-context-relay"
HOOK="$EXT/bin/fm-context-relay-hook"
FIXTURE="$TESTS_DIR/fixture.py"
PASSES=0
FAILS=0
BG_PIDS=''

unset CLAUDE_CODE_SESSION_ID CLAUDE_PID CLAUDE_PROJECT_DIR FM_HOME FM_ROOT_OVERRIDE FM_STATE_OVERRIDE
unset FM_CONTEXT_RELAY_HOME FM_CONTEXT_RELAY_HARNESS FM_CONTEXT_RELAY_EVENT FM_CONTEXT_RELAY_CLI FM_CONTEXT_RELAY_OWNER_PID
export FM_LIVE=0

pass() { PASSES=$((PASSES + 1)); printf 'ok - %s\n' "$1"; }
fail() {
  FAILS=$((FAILS + 1))
  printf 'not ok - %s\n' "$1"
  [ -z "${2:-}" ] || printf '  %s\n' "$2"
}
assert_eq() { if [ "$1" = "$2" ]; then pass "$3"; else fail "$3" "expected [$2] got [$1]"; fi; }
assert_contains() { case "$1" in *"$2"*) pass "$3" ;; *) fail "$3" "missing [$2] in [$1]" ;; esac; }
assert_not_contains() { case "$1" in *"$2"*) fail "$3" "unexpected [$2] in [$1]" ;; *) pass "$3" ;; esac; }
assert_file() { if [ -e "$1" ]; then pass "$2"; else fail "$2" "missing $1"; fi; }
assert_no_file() { if [ -e "$1" ]; then fail "$2" "unexpected $1"; else pass "$2"; fi; }

finish() {
  printf '# %s: %d passed, %d failed\n' "$(basename "$0")" "$PASSES" "$FAILS"
  [ "$FAILS" -eq 0 ]
}

cleanup() {
  local pid
  for pid in $BG_PIDS; do
    kill "$pid" 2>/dev/null || true
  done
  [ -z "${TMP:-}" ] || rm -rf "$TMP"
}

fm_root() {
  local root=${FIRSTMATE_ROOT:-}
  if [ -z "$root" ]; then
    root=$(cd "$EXT/../../.." 2>/dev/null && pwd) || root=''
  fi
  if [ -z "$root" ] || [ ! -f "$root/bin/fm-session-lock-lib.sh" ]; then
    echo "error: set FIRSTMATE_ROOT to a Firstmate checkout (needs bin/fm-session-lock-lib.sh)" >&2
    exit 2
  fi
  printf '%s\n' "$root"
}

setup_tmp() {
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/fm-context-relay-test.XXXXXX")
  trap cleanup EXIT
  FAKEBIN="$TMP/fakebin"
  mkdir -p "$FAKEBIN"
  local name
  for name in claude codex; do
    # A direct /bin/bash shebang keeps the process name equal to the file
    # name; going through /usr/bin/env would rename it to bash.
    cat > "$FAKEBIN/$name" <<'EOF'
#!/bin/bash
# Fake harness: optionally record this pid as the lock owner, then run the
# command as a child so the hook finds this process in its ancestry.
if [ "${1:-}" = --version ]; then echo '2.1.314 (Claude Code fixture)'; exit 0; fi
while [ "${1:-}" = -p ] || [ "${1:-}" = --safe-mode ]; do shift; done
if [ -n "${FAKE_LOCK_FILE:-}" ]; then printf '%s\n' "$$" > "$FAKE_LOCK_FILE"; fi
"$@"
EOF
    chmod +x "$FAKEBIN/$name"
  done
  export PATH="$FAKEBIN:$PATH"
}

# make_home DIR - a minimal Firstmate primary home with the real lock libs.
make_home() {
  local primary_home=$1 root lib
  root=$(fm_root)
  mkdir -p "$primary_home/bin" "$primary_home/state" "$primary_home/config" "$primary_home/.claude"
  git -C "$primary_home" init -q
  printf '# test home\n' > "$primary_home/AGENTS.md"
  for lib in fm-primary-scope-lib.sh fm-session-lock-lib.sh fm-cursor-lib.sh fm-lock.sh; do
    cp "$root/bin/$lib" "$primary_home/bin/$lib"
  done
  printf '.claude/settings.local.json\nstate/\nconfig/\ndata/\n' >> "$primary_home/.git/info/exclude"
}

# write_config HOME JSON
write_config() { printf '%s\n' "$2" > "$1/config/context-relay.json"; }

payload() { python3 -I "$FIXTURE" payload "$@"; }

# run_hook HOME HARNESS EVENT PAYLOAD - run the hook as the lock owner.
# Sets OUT (stdout) and RC.
run_hook() {
  local primary_home=$1 harness=$2 event=$3 body=$4
  OUT=$(printf '%s' "$body" | FAKE_LOCK_FILE="$primary_home/state/.lock" "$FAKEBIN/$harness" "$HOOK" --home "$primary_home" "$harness" "$event")
  export RC=$?
}

# run_hook_as_other HOME HARNESS EVENT PAYLOAD - run inside a harness that
# does not own the lock.
run_hook_as_other() {
  local primary_home=$1 harness=$2 event=$3 body=$4
  OUT=$(printf '%s' "$body" | "$FAKEBIN/$harness" "$HOOK" --home "$primary_home" "$harness" "$event")
  export RC=$?
}

# start_foreign_owner HOME HARNESS - a live harness that holds the lock.
start_foreign_owner() {
  local primary_home=$1 harness=$2 i
  FAKE_LOCK_FILE="$primary_home/state/.lock" "$FAKEBIN/$harness" sleep 120 &
  BG_PIDS="$BG_PIDS $!"
  for ((i = 0; i < 10; i++)); do
    [ -s "$primary_home/state/.lock" ] && return 0
    sleep 0.1
  done
}

record_path() { printf '%s/state/context-relay/sessions/%s-%s.json\n' "$1" "$2" "$3"; }

# jget FILE EXPR - evaluate a Python expression over the JSON in FILE as d.
jget() { python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); r=eval(sys.argv[2]); print(json.dumps(r) if isinstance(r,(dict,list,tuple,bool)) or r is None else r)' "$1" "$2"; }

# jout EXPR - evaluate over the JSON in $OUT (empty output prints EMPTY).
jout() {
  if [ -z "$OUT" ]; then echo EMPTY; return; fi
  printf '%s' "$OUT" | python3 -I -c 'import json,sys; d=json.load(sys.stdin); r=eval(sys.argv[1]); print(json.dumps(r, ensure_ascii=False) if isinstance(r,(dict,list,tuple,bool)) or r is None else r)' "$1"
}
