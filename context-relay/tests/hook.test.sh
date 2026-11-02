#!/usr/bin/env bash
# Hook behavior through bin/fm-context-relay-hook with fake harness processes
# and synthetic transcripts: who is measured, what counts, when a warning is
# injected, and that nothing ever loops, blocks or escapes the owning session.
# shellcheck source-path=SCRIPTDIR
. "$(dirname "$0")/lib.sh"
setup_tmp
export FM_CONTEXT_RELAY_THROTTLE_SECONDS=0

TOKCFG='{"models":{"claude-opus-5-5":{"trigger":"tokens","request_tokens":250000,"stop_new_work_tokens":400000,"window_tokens":1000000}}}'

H=$TMP/home
make_home "$H"

# --- ownership and exclusions -------------------------------------------------
T=$TMP/own.jsonl
python3 -I "$FIXTURE" claude-start "$T"
python3 -I "$FIXTURE" claude-usage "$T" msg_a 300000
write_config "$H" "$TOKCFG"

start_foreign_owner "$H" claude
run_hook_as_other "$H" claude PostToolBatch "$(payload PostToolBatch s-other "$T")"
assert_eq "$RC:$OUT" "0:" "non-owner session: exit 0, no output"
assert_no_file "$H/state/context-relay" "non-owner session creates nothing"
for pid in $BG_PIDS; do kill "$pid" 2>/dev/null || true; done
wait 2>/dev/null
BG_PIDS=''

rm -f "$H/state/.lock"
OUT=$(payload PostToolBatch s-nolock "$T" | "$FAKEBIN/claude" "$HOOK" --home "$H" claude PostToolBatch); RC=$?
assert_eq "$RC:$OUT" "0:" "no lock file: exit 0, no output"
assert_no_file "$H/state/context-relay" "no lock file creates nothing"

run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-sub "$T" agent_id='"agent-1"' agent_type='"general-purpose"')"
assert_eq "$RC:$OUT" "0:" "subagent event (agent_id) stays inert"
assert_no_file "$(record_path "$H" claude s-sub)" "subagent event creates no record"

run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-cursor "$T" cursor_version='"2026.1"')"
assert_eq "$RC:$OUT" "0:" "Cursor-delivered payload stays inert"

run_hook "$H" claude PostToolBatch "$(payload Stop s-mismatch "$T")"
assert_eq "$RC:$OUT" "0:" "payload of another event stays inert"

WT_REPO=$TMP/wt-repo
make_home "$WT_REPO"
git -C "$WT_REPO" add AGENTS.md bin >/dev/null
git -C "$WT_REPO" -c user.name=cbriseno -c user.email=4805447+cbriseno@users.noreply.github.com commit -qm init
# Private linked-checkout metadata, without registering or administering a
# worktree in any shared repository or pool.
mkdir -p "$TMP/linked/.git"
printf '%s\n' "$WT_REPO/.git" > "$TMP/linked/.git/commondir"
cp "$WT_REPO/.git/HEAD" "$TMP/linked/.git/HEAD"
cp "$WT_REPO/AGENTS.md" "$TMP/linked/AGENTS.md"
cp -R "$WT_REPO/bin" "$TMP/linked/bin"
mkdir -p "$TMP/linked/state" "$TMP/linked/config"
write_config "$TMP/linked" "$TOKCFG"
run_hook "$TMP/linked" claude PostToolBatch "$(payload PostToolBatch s-wt "$T")"
assert_eq "$RC:$OUT" "0:" "linked worktree (worker or scout) stays inert even holding its lock"
assert_no_file "$TMP/linked/state/context-relay" "linked worktree creates nothing"

for bad in '' 'not json' '[]' '{"session_id":"../../etc","transcript_path":"/x","hook_event_name":"PostToolBatch"}'; do
  run_hook "$H" claude PostToolBatch "$bad"
  assert_eq "$RC:$OUT" "0:" "malformed payload [$bad]: exit 0, no output"
done
OUT=$(printf '{}' | FAKE_LOCK_FILE="$H/state/.lock" "$FAKEBIN/claude" "$HOOK" --home "$H" claude Bogus); RC=$?
assert_eq "$RC:$OUT" "0:" "unknown event: exit 0, no output"

# --- tokens trigger: one warning per level, never repeated ----------------------
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-tok "$T")"
assert_eq "$RC" 0 "owner crossing request_tokens: exit 0"
assert_eq "$(jout 'd["hookSpecificOutput"]["hookEventName"]')" PostToolBatch "warning uses PostToolBatch hookSpecificOutput"
assert_contains "$(jout 'd["hookSpecificOutput"]["additionalContext"]')" "Early context warning" "level 1 warning injected"
assert_contains "$OUT" "300.0k" "warning states the resident context"
R=$(record_path "$H" claude s-tok)
assert_eq "$(jget "$R" 'd["request"]["level"]')" 1 "request recorded at level 1"
for i in 1 2 3; do
  run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-tok "$T")"
  assert_eq "$RC:$OUT" "0:" "repeated PostToolBatch $i: no re-injection"
done
run_hook "$H" claude UserPromptSubmit "$(payload UserPromptSubmit s-tok "$T" prompt='"hola"')"
assert_eq "$RC:$OUT" "0:" "UserPromptSubmit after delivery: no re-injection"
python3 -I "$FIXTURE" claude-usage "$T" msg_b 410000
run_hook "$H" claude UserPromptSubmit "$(payload UserPromptSubmit s-tok "$T" prompt='"hola"')"
assert_contains "$(jout 'd["hookSpecificOutput"]["additionalContext"]')" "Do not admit new substantive work" "escalation to level 2 injected once"
assert_eq "$(jout 'd["hookSpecificOutput"]["hookEventName"]')" UserPromptSubmit "warning uses UserPromptSubmit hookSpecificOutput"
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-tok "$T")"
assert_eq "$OUT" "" "level 2 not repeated"
assert_eq "$(jget "$R" 'sorted(d["delivered"])')" '["level:1", "level:2"]' "each level delivered exactly once"

# --- current context, not cumulative: duplicates, sidechains, zeros -------------
T2=$TMP/dup.jsonl
python3 -I "$FIXTURE" claude-start "$T2"
python3 -I "$FIXTURE" claude-usage "$T2" msg_1 300000
python3 -I "$FIXTURE" claude-usage "$T2" msg_1 300000
python3 -I "$FIXTURE" claude-usage "$T2" msg_2 120000
python3 -I "$FIXTURE" claude-usage "$T2" msg_1 300000
python3 -I "$FIXTURE" claude-usage "$T2" msg_side 900000 --sidechain
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-dup "$T2")"
R=$(record_path "$H" claude s-dup)
assert_eq "$(jget "$R" 'd["resident"]["tokens"]')" 120000 "duplicate rows of an older message.id and sidechain rows never replace the latest call"
assert_eq "$OUT" "" "deduplicated context below threshold: no warning"
python3 -I "$FIXTURE" claude-usage "$T2" msg_zero 0
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-dup "$T2")"
assert_eq "$(jget "$R" 'd["resident"]["status"] + "/" + str(d["resident"]["reason"])')" "no_data/zero" "a zero figure is no data, never zero context"
python3 -I "$FIXTURE" claude-usage "$T2" msg_synth 999999 --model '<synthetic>'
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-dup "$T2")"
assert_eq "$(jget "$R" 'd["resident"]["status"]')" "no_data" "synthetic error rows are not model calls"

# --- compaction: stale figure is no data, distinct uuids count ------------------
write_config "$H" '{"models":{"claude-opus-5-5":{"trigger":"first","request_tokens":500000,"stop_new_work_tokens":800000}}}'
T3=$TMP/compact.jsonl
python3 -I "$FIXTURE" claude-start "$T3"
python3 -I "$FIXTURE" claude-usage "$T3" msg_pre 450000
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-cmp "$T3")"
R=$(record_path "$H" claude s-cmp)
assert_eq "$(jget "$R" 'd["resident"]["tokens"]')" 450000 "pre-compaction context measured"
run_hook "$H" claude SessionStart "$(payload SessionStart s-cmp "$T3" source='"compact"')"
assert_eq "$(jget "$R" 'd["resident"]["status"] + "/" + d["resident"]["reason"]')" "no_data/after_compaction" "SessionStart(compact) before the boundary row: stale figure is no data"
assert_eq "$(jget "$R" 'd["compactions"]["count"]')" 0 "SessionStart(compact) alone never counts a compaction"
python3 -I "$FIXTURE" claude-boundary "$T3" uuid-1
run_hook "$H" claude PostCompact "$(payload PostCompact s-cmp "$T3" trigger='"auto"')"
assert_eq "$RC:$OUT" "0:" "PostCompact never prints"
assert_eq "$(jget "$R" 'd["compactions"]["count"]')" 1 "compact_boundary uuid counted"
for i in 1 2 3; do
  run_hook "$H" claude PreCompact "$(payload PreCompact s-cmp "$T3" trigger='"auto"' custom_instructions=null)"
  assert_eq "$RC:$OUT" "0:" "PreCompact $i: exit 0, no output"
done
assert_eq "$(jget "$R" 'd["compactions"]["count"]')" 1 "PreCompact never counts a compaction"
python3 -I "$FIXTURE" claude-usage "$T3" msg_post 62000
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-cmp "$T3")"
assert_eq "$(jget "$R" 'd["resident"]["tokens"]')" 62000 "first call after compaction is the new resident context"
python3 -I "$FIXTURE" claude-boundary "$T3" uuid-2
python3 -I "$FIXTURE" claude-boundary "$T3" uuid-2
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-cmp "$T3")"
assert_eq "$(jget "$R" 'd["compactions"]["count"]')" 2 "a repeated boundary uuid counts once"
assert_contains "$OUT" "2 compactions (limit 2)" "trigger first: compaction limit raises an early warning"
assert_eq "$(jget "$R" 'd["resident"]["status"]')" "no_data" "after the boundary row and before a new call: no data"

# --- missing events: compactions found without any compaction hook --------------
T4=$TMP/missing.jsonl
python3 -I "$FIXTURE" claude-start "$T4"
python3 -I "$FIXTURE" claude-usage "$T4" msg_1 200000
python3 -I "$FIXTURE" claude-boundary "$T4" m-1
python3 -I "$FIXTURE" claude-usage "$T4" msg_2 70000
python3 -I "$FIXTURE" claude-boundary "$T4" m-2
python3 -I "$FIXTURE" claude-usage "$T4" msg_3 65000
rm -f "$H/config/context-relay.json"
run_hook "$H" claude UserPromptSubmit "$(payload UserPromptSubmit s-miss "$T4" prompt='"x"')"
R=$(record_path "$H" claude s-miss)
assert_eq "$(jget "$R" 'd["compactions"]["count"]')" 2 "compactions counted from the transcript when no compaction hook fired"
assert_contains "$OUT" "Early context warning" "default settings (compactions, N=2) raise an early warning"
python3 -I "$FIXTURE" claude-boundary "$T4" m-3
python3 -I "$FIXTURE" claude-usage "$T4" msg_4 64000
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-miss "$T4")"
assert_contains "$OUT" "Do not admit new substantive work" "N+1 compactions: check current pressure before new work"

# --- Stop: never prints, continuation is a no-op, delivery happens elsewhere -----
T5=$TMP/stop.jsonl
python3 -I "$FIXTURE" claude-start "$T5"
python3 -I "$FIXTURE" claude-usage "$T5" msg_1 100000
python3 -I "$FIXTURE" claude-boundary "$T5" st-1
python3 -I "$FIXTURE" claude-boundary "$T5" st-2
python3 -I "$FIXTURE" claude-usage "$T5" msg_2 63000
run_hook "$H" claude Stop "$(payload Stop s-stop "$T5" stop_hook_active=false)"
assert_eq "$RC:$OUT" "0:" "Stop crossing a threshold prints nothing"
R=$(record_path "$H" claude s-stop)
assert_eq "$(jget "$R" '[p["key"] for p in d["pending"]]')" '["level:1"]' "the request is recorded and waits for a safe injection point"
BEFORE=$(cksum < "$R")
for i in 1 2 3 4 5 6 7 8 9 10; do
  run_hook "$H" claude Stop "$(payload Stop s-stop "$T5" stop_hook_active=true)"
  [ "$RC:$OUT" = "0:" ] || fail "Stop continuation $i printed or failed" "$RC:$OUT"
done
assert_eq "$(cksum < "$R")" "$BEFORE" "Stop continuations (stop_hook_active) change nothing"
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-stop "$T5")"
assert_contains "$OUT" "Early context warning" "pending request delivered at the next PostToolBatch"
for i in 1 2 3 4 5; do
  run_hook "$H" claude Stop "$(payload Stop s-stop "$T5" stop_hook_active=false)"
  run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-stop "$T5")"
  [ "$OUT" = "" ] || fail "turn $i after delivery re-injected" "$OUT"
done
assert_eq "$(jget "$R" 'len(d["deliveries"])')" 1 "alternating Stop and PostToolBatch: exactly one delivery"

# --- reminder after a compaction while a request is open ------------------------
python3 -I "$FIXTURE" claude-boundary "$T5" st-3
run_hook "$H" claude SessionStart "$(payload SessionStart s-stop "$T5" source='"compact"')"
assert_contains "$OUT" "Do not admit" "the compaction that escalates injects the level 2 warning at SessionStart"
python3 -I "$FIXTURE" claude-usage "$T5" msg_3 64000
python3 -I "$FIXTURE" claude-boundary "$T5" st-4
run_hook "$H" claude PostCompact "$(payload PostCompact s-stop "$T5" trigger='"auto"')"
run_hook "$H" claude SessionStart "$(payload SessionStart s-stop "$T5" source='"compact"')"
assert_contains "$OUT" "Post-compaction reminder" "a later compaction re-surfaces the open request once"
run_hook "$H" claude SessionStart "$(payload SessionStart s-stop "$T5" source='"compact"')"
assert_eq "$OUT" "" "the same compaction does not remind twice"

# --- model change and unknown models ----------------------------------------------
write_config "$H" "$TOKCFG"
T6=$TMP/model.jsonl
python3 -I "$FIXTURE" claude-start "$T6"
python3 -I "$FIXTURE" claude-usage "$T6" msg_1 300000 --model claude-unlisted-model
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-model "$T6")"
assert_contains "$OUT" "claude-unlisted-model has no model pressure overrides" "unknown model: one optional-overrides notice"
assert_not_contains "$OUT" "Early context warning" "unknown model has no token thresholds"
R=$(record_path "$H" claude s-model)
assert_eq "$(jget "$R" 'd["effective"]["known_model"]')" false "unknown model has no catalog entry"
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-model "$T6")"
assert_eq "$OUT" "" "unknown-model notice not repeated"
python3 -I "$FIXTURE" claude-usage "$T6" msg_2 310000 --model claude-opus-5-5
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-model "$T6")"
assert_contains "$OUT" "model claude-opus-5-5" "after the model change the new model's thresholds apply"
assert_eq "$(jget "$R" 'd["models_seen"]')" '["claude-unlisted-model", "claude-opus-5-5"]' "model history kept"
assert_eq "$(jget "$R" 'd["effective"]["request_tokens"]')" 250000 "effective thresholds follow the current model"

# --- invalid settings: last valid values and one notice ---------------------------
write_config "$H" '{"compactions_before_relay": 3}'
T7=$TMP/cfg.jsonl
python3 -I "$FIXTURE" claude-start "$T7"
python3 -I "$FIXTURE" claude-usage "$T7" msg_1 80000
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-cfg "$T7")"
write_config "$H" '{"compactions_before_relay": 0, "relay_mode": "auto"}'
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-cfg "$T7")"
assert_contains "$OUT" "is invalid" "invalid settings: notice injected"
assert_contains "$OUT" "last valid values" "invalid settings: last valid values kept"
R=$(record_path "$H" claude s-cfg)
assert_eq "$(jget "$R" 'd["effective"]["compactions_before_relay"]')" 3 "hook keeps evaluating with the last valid settings"
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-cfg "$T7")"
assert_eq "$OUT" "" "invalid-settings notice not repeated for the same file"
assert_contains "$(cat "$H/data/context-relay/config-changes.jsonl")" '"kind": "edit-detected"' "hand edits are logged"
printf '{bad json' > "$H/config/context-relay.json"
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-cfg "$T7")"
assert_contains "$OUT" "invalid JSON" "unparseable settings: a new notice for the new content"

# --- max_session_hours -------------------------------------------------------------
rm -f "$H/config/context-relay.json"
T8=$TMP/hours.jsonl
OLD=$(python3 -I -c 'import datetime;print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z"))')
python3 -I "$FIXTURE" claude-start "$T8" --ts "$OLD"
python3 -I "$FIXTURE" claude-usage "$T8" msg_1 70000
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-hours "$T8")"
assert_contains "$OUT" "session age 30" "a session older than max_session_hours raises an early warning"

# --- PreCompact checkpoint ---------------------------------------------------------
cat > "$H/bin/fm-fleet-snapshot.sh" <<'EOF'
#!/bin/bash
case "${SNAP_MODE:-ok}" in
  ok) printf '{"schema":"fm-fleet-snapshot.v1","fm_home":"%s"}\n' "$FM_HOME" ;;
  slow) sleep 30; echo '{}' ;;
  fail) echo boom >&2; exit 2 ;;
esac
EOF
chmod +x "$H/bin/fm-fleet-snapshot.sh"
T9=$TMP/pre.jsonl
python3 -I "$FIXTURE" claude-start "$T9"
python3 -I "$FIXTURE" claude-usage "$T9" msg_1 90000
run_hook "$H" claude PreCompact "$(payload PreCompact s-pre "$T9" trigger='"manual"' custom_instructions=null)"
assert_eq "$RC:$OUT" "0:" "PreCompact checkpoint: exit 0, no output"
CP=$(find "$H/data/context-relay/checkpoints/claude-s-pre" -name '*-precompact.json' -print | head -1)
assert_file "$CP" "checkpoint written"
assert_eq "$(jget "$CP" 'd["schema"] + " " + d["fleet_snapshot"]["schema"]')" "fm-context-relay-checkpoint.v1 fm-fleet-snapshot.v1" "checkpoint carries the read-only fleet snapshot"
assert_eq "$(jget "$CP" 'd["session"]["resident"]["tokens"]')" 90000 "checkpoint carries the session metrics"
run_hook "$H" claude PreCompact "$(payload PreCompact s-pre "$T9" trigger='"auto"' custom_instructions=null)"
assert_eq "$(find "$H/data/context-relay/checkpoints/claude-s-pre" -name '*-precompact.json' | wc -l | tr -d ' ')" 1 "a double PreCompact for one compaction writes one checkpoint"
START=$(date +%s)
OUT=$(payload PreCompact s-slow "$T9" trigger='"auto"' custom_instructions=null | SNAP_MODE=slow FM_CONTEXT_RELAY_SNAPSHOT_TIMEOUT=1 FAKE_LOCK_FILE="$H/state/.lock" "$FAKEBIN/claude" "$HOOK" --home "$H" claude PreCompact); RC=$?
ELAPSED=$(( $(date +%s) - START ))
assert_eq "$RC:$OUT" "0:" "hung snapshot: exit 0, no output"
if [ "$ELAPSED" -le 8 ]; then pass "hung snapshot is killed within its budget (${ELAPSED}s)"; else fail "hung snapshot took ${ELAPSED}s"; fi
CP=$(find "$H/data/context-relay/checkpoints/claude-s-slow" -name '*-precompact.json' -print | head -1)
assert_contains "$(jget "$CP" 'd["fleet_snapshot_error"]')" "exceeded" "timeout recorded in the checkpoint"
if pgrep -f "$H/bin/fm-fleet-snapshot.sh" >/dev/null 2>&1; then fail "snapshot process left running"; else pass "no snapshot process left running"; fi
OUT=$(payload PreCompact s-fail "$T9" trigger='"auto"' custom_instructions=null | SNAP_MODE=fail FAKE_LOCK_FILE="$H/state/.lock" "$FAKEBIN/claude" "$HOOK" --home "$H" claude PreCompact); RC=$?
assert_eq "$RC:$OUT" "0:" "failing snapshot: exit 0 (never 2), no output"
assert_contains "$(jget "$(record_path "$H" claude s-fail)" 'd["checkpoint"]["last_error"]')" "exited 2" "snapshot failure recorded on the session"
write_config "$H" '{"precompact_checkpoint": false}'
run_hook "$H" claude PreCompact "$(payload PreCompact s-off "$T9" trigger='"auto"' custom_instructions=null)"
assert_no_file "$H/data/context-relay/checkpoints/claude-s-off" "precompact_checkpoint false: no checkpoint"
rm -f "$H/config/context-relay.json"

# --- concurrency: parallel events deliver once ------------------------------------
T10=$TMP/par.jsonl
python3 -I "$FIXTURE" claude-start "$T10"
python3 -I "$FIXTURE" claude-boundary "$T10" p-1
python3 -I "$FIXTURE" claude-boundary "$T10" p-2
python3 -I "$FIXTURE" claude-usage "$T10" msg_1 63000
mkdir -p "$TMP/par"
payload PostToolBatch s-par "$T10" > "$TMP/par/payload.json"
# One owning harness runs six hook processes at once, as parallel tool calls do.
# shellcheck disable=SC2016 # This script is evaluated by the child bash.
FAKE_LOCK_FILE="$H/state/.lock" "$FAKEBIN/claude" bash -c \
  'for i in 1 2 3 4 5 6; do "$1" --home "$0" claude PostToolBatch < "$2" > "$3/$i.out" & done; wait' \
  "$H" "$HOOK" "$TMP/par/payload.json" "$TMP/par"
DELIVERED=$(cat "$TMP/par/"*.out | grep -c 'Early context warning' || true)
R=$(record_path "$H" claude s-par)
assert_eq "$(jget "$R" 'len(d["deliveries"])')" 1 "parallel events: the record shows one delivery"
if [ "$DELIVERED" -le 1 ]; then pass "parallel events: at most one injection ($DELIVERED)"; else fail "parallel events injected $DELIVERED times"; fi

# --- throttle gate -----------------------------------------------------------------
T11=$TMP/gate.jsonl
python3 -I "$FIXTURE" claude-start "$T11"
python3 -I "$FIXTURE" claude-usage "$T11" msg_1 70000
OUT=$(payload PostToolBatch s-gate "$T11" | FM_CONTEXT_RELAY_THROTTLE_SECONDS=60 FAKE_LOCK_FILE="$H/state/.lock" "$FAKEBIN/claude" "$HOOK" --home "$H" claude PostToolBatch)
assert_file "$H/state/context-relay/throttle/claude-s-gate" "a run with nothing pending arms the gate"
python3 -I "$FIXTURE" claude-boundary "$T11" g-1
python3 -I "$FIXTURE" claude-boundary "$T11" g-2
OUT=$(payload PostToolBatch s-gate "$T11" | FM_CONTEXT_RELAY_THROTTLE_SECONDS=60 FAKE_LOCK_FILE="$H/state/.lock" "$FAKEBIN/claude" "$HOOK" --home "$H" claude PostToolBatch)
assert_eq "$OUT" "" "a gated PostToolBatch skips measurement and injects nothing"
OUT=$(payload UserPromptSubmit s-gate "$T11" prompt='"x"' | FM_CONTEXT_RELAY_THROTTLE_SECONDS=60 FAKE_LOCK_FILE="$H/state/.lock" "$FAKEBIN/claude" "$HOOK" --home "$H" claude UserPromptSubmit)
assert_contains "$OUT" "Early context warning" "UserPromptSubmit is never gated"

# --- Codex adapter (implemented, not wired) ---------------------------------------
write_config "$H" '{"models":{"gpt-6-luna":{"trigger":"first","request_tokens":200000,"stop_new_work_tokens":240000,"window_tokens":258400}}}'
C1=$TMP/rollout.jsonl
python3 -I "$FIXTURE" codex-meta "$C1" --originator codex_exec
python3 -I "$FIXTURE" codex-turn "$C1" gpt-6-luna
python3 -I "$FIXTURE" codex-tokens "$C1" 150000 --total 1999900
run_hook "$H" codex PostToolUse "$(payload PostToolUse c-1 "$C1" model='"gpt-6-luna"' turn_id='"t1"')"
R=$(record_path "$H" codex c-1)
assert_eq "$(jget "$R" 'd["resident"]["tokens"]')" 150000 "Codex: last_token_usage is the resident context, total_token_usage ignored"
assert_eq "$(jget "$R" 'd["window_tokens_observed"]')" 258400 "Codex: reported window recorded"
python3 -I "$FIXTURE" codex-compacted "$C1" win-1 1
python3 -I "$FIXTURE" codex-tokens "$C1" 0
run_hook "$H" codex PostCompact "$(payload PostCompact c-1 "$C1" model='"gpt-6-luna"' turn_id='"t1"' trigger='"auto"')"
assert_eq "$(jget "$R" 'd["resident"]["status"]')" "no_data" "Codex: input_tokens 0 after compacted is no data"
python3 -I "$FIXTURE" codex-compacted "$C1" win-1 1
python3 -I "$FIXTURE" codex-compacted "$C1" win-2 2
python3 -I "$FIXTURE" codex-tokens "$C1" 31000
for i in 1 2 3; do
  run_hook "$H" codex PreCompact "$(payload PreCompact c-1 "$C1" model='"gpt-6-luna"' turn_id='"t1"' trigger='"auto"')"
done
run_hook "$H" codex PostToolUse "$(payload PostToolUse c-1 "$C1" model='"gpt-6-luna"' turn_id='"t1"')"
assert_eq "$(jget "$R" 'd["compactions"]["count"]')" 2 "Codex: distinct window_id counted once, turn_id and PreCompact never count"
assert_eq "$(jout 'd["hookSpecificOutput"]["hookEventName"]')" PostToolUse "Codex: warning uses PostToolUse hookSpecificOutput"
assert_contains "$OUT" "2 compactions" "Codex exec: compaction trigger verified"
C2=$TMP/rollout-tui.jsonl
python3 -I "$FIXTURE" codex-meta "$C2" --originator codex-tui
python3 -I "$FIXTURE" codex-turn "$C2" gpt-6-luna
python3 -I "$FIXTURE" codex-compacted "$C2" tw-1 1
python3 -I "$FIXTURE" codex-compacted "$C2" tw-2 2
python3 -I "$FIXTURE" codex-tokens "$C2" 210000
run_hook "$H" codex UserPromptSubmit "$(payload UserPromptSubmit c-2 "$C2" model='"gpt-6-luna"' turn_id='"t1"' prompt='"x"')"
assert_contains "$OUT" "is unverified" "Codex TUI: unverified counting falls back to tokens with a notice"
assert_contains "$OUT" "resident context 210.0k" "Codex TUI: the token trigger still fires"
R=$(record_path "$H" codex c-2)
assert_eq "$(jget "$R" 'd["effective"]["effective_trigger"]')" tokens "Codex TUI: effective trigger is tokens"
run_hook "$H" codex PostToolUse "$(payload PostToolUse c-3 "$C1" model='"codex-auto-review"' turn_id='"t1"')"
assert_eq "$RC:$OUT" "0:" "Codex auto-review stays inert"
assert_no_file "$(record_path "$H" codex c-3)" "Codex auto-review creates no record"
run_hook "$H" codex PostToolUse "$(payload PostToolUse c-4 "$C1" model='"gpt-6-luna"' turn_id='"t1"' agent_type='"worker"')"
assert_no_file "$(record_path "$H" codex c-4)" "Codex subagent stays inert"

# --- identity binding and headless supervision ----------------------------------
printf 'primary-session\n' > "$H/state/.lock-session"
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch foreign-session "$T")"
assert_eq "$RC:$OUT" '0:' "payload must match the home bound lock session"
assert_no_file "$(record_path "$H" claude foreign-session)" "foreign session identity writes no record"
rm "$H/state/.lock-session"
start_foreign_owner "$H" claude
OUT=$(payload PostToolBatch headless "$T" | "$FAKEBIN/claude" -p --safe-mode "$HOOK" --home "$H" claude PostToolBatch); RC=$?
assert_eq "$RC:$OUT" '0:' "headless engine under another lock owner stays inert"
assert_no_file "$(record_path "$H" claude headless)" "headless engine creates no record"
for pid in $BG_PIDS; do kill "$pid" 2>/dev/null || true; done
wait 2>/dev/null
BG_PIDS=''
run_hook "$H" claude Stop "$(payload Stop never-started "$T" stop_hook_active=true)"
assert_no_file "$(record_path "$H" claude never-started)" "Stop continuation cannot create even a first record"

# A second actual compaction within 90s still gets its own pre-checkpoint.
write_config "$H" '{"precompact_checkpoint":true}'
python3 -I "$FIXTURE" claude-boundary "$T9" actual-1
run_hook "$H" claude PreCompact "$(payload PreCompact s-pre "$T9" trigger='"auto"')"
assert_eq "$(find "$H/data/context-relay/checkpoints/claude-s-pre" -name '*-precompact.json' | wc -l | tr -d ' ')" 2 "each real compaction gets a checkpoint, including closely spaced ones"

# Rotating the transcript must preserve the durable compaction counter/request.
python3 -I "$FIXTURE" claude-boundary "$TMP/rotated.jsonl" p-3
python3 -I "$FIXTURE" claude-usage "$TMP/rotated.jsonl" after-rotation 63000
mv "$TMP/rotated.jsonl" "$T10"
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-par "$T10")"
assert_eq "$(jget "$(record_path "$H" claude s-par)" 'd["compactions"]["count"]')" 3 "transcript rotation preserves distinct compactions outside context"
assert_eq "$(jget "$(record_path "$H" claude s-par)" 'd["request"]["level"]')" 2 "durable request escalates after transcript rotation"
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-par "$TMP/not-there")"
assert_eq "$(jget "$(record_path "$H" claude s-par)" 'd["resident"]["status"],d["request"]["level"],d["compactions"]["count"]')" '["no_data", 2, 3]' "missing transcript never clears the request or invents context"

# Policy eligibility is independent of a previously delivered pressure level.
write_config "$H" '{}'
PC=$TMP/policy-tui.jsonl
python3 -I "$FIXTURE" codex-meta "$PC" --originator codex-tui
python3 -I "$FIXTURE" codex-turn "$PC" gpt-6.1-sol
python3 -I "$FIXTURE" codex-tokens "$PC" 313489 --window 475000
run_hook "$H" codex PostToolUse "$(payload PostToolUse p-old "$PC")"
PR=$(record_path "$H" codex p-old)
assert_eq "$(jget "$PR" 'd["request"]["level"]')" 2 "migration fixture has the actual old level 2"
write_config "$H" '{"relay_policy":{"codex":{"compactions":4},"claude":{"compactions":2,"min_context_percent":80}}}'
run_hook "$H" codex PostToolUse "$(payload PostToolUse p-old "$PC")"
assert_eq "$(jget "$PR" 'd["relay"]["pending"],d["relay"]["status"]')" '[false, "unverified_compactions"]' "old 0/4 TUI level 2 cannot become relay eligibility"
assert_not_contains "$OUT" 'Do not admit new substantive work' "old pressure cannot block reaching the new predicate"
assert_not_contains "$OUT" 'Relay awaiting safe point' "old pressure cannot request a replacement"
assert_contains "$OUT" 'Current relay policy' "natural hook announces the active policy to an existing session"
assert_contains "$OUT" 'do not block admission' "policy announcement overrides the old developer admission guard"
run_hook "$H" codex PostToolUse "$(payload PostToolUse p-old "$PC")"
assert_eq "$OUT" '' "active-policy announcement is not repeated"
assert_eq "$(jget "$PR" 'd["delivered"].count("level:2")')" 1 "policy activation does not reset or reinject the old level 2"

PE=$TMP/policy-exec.jsonl
python3 -I "$FIXTURE" codex-meta "$PE"
python3 -I "$FIXTURE" codex-turn "$PE" gpt-6.1-sol
for number in 1 2 3 4; do python3 -I "$FIXTURE" codex-compacted "$PE" "policy-$number" "$number"; done
python3 -I "$FIXTURE" codex-tokens "$PE" 42000 --window 475000
run_hook "$H" codex Stop "$(payload Stop p-ready "$PE" stop_hook_active=false)"
assert_eq "$OUT" '' "matching policy in Stop remains silent"
assert_eq "$(jget "$(record_path "$H" codex p-ready)" 'd["relay"]["pending"]')" true "four verified compactions persist a safe-point request"
run_hook "$H" codex PostToolUse "$(payload PostToolUse p-ready "$PE")"
assert_contains "$OUT" 'Relay awaiting safe point' "next normal event delivers safe-point policy"
assert_contains "$OUT" 'pause safely' "matching policy preserves safe task completion or pause"
run_hook "$H" codex PostToolUse "$(payload PostToolUse p-ready "$PE")"
assert_eq "$OUT" '' "policy match is delivered once"
run_hook "$H" codex PostToolUse "$(payload PostToolUse p-worker "$PE" agent_type='"worker"')"
assert_no_file "$(record_path "$H" codex p-worker)" "policy does not change worker exclusion"

# The accredited native TUI contract reaches policy through the normal hook.
PN=$TMP/policy-native-tui.jsonl
head -n 2 "$TESTS_DIR/fixtures/codex-tui-paginated.jsonl" > "$PN"
run_hook "$H" codex PostToolUse "$(payload PostToolUse p-native "$PN")"
NR=$(record_path "$H" codex p-native)
assert_eq "$(jget "$NR" 'd["compactions"]["count"],d["compactions"]["counting"],d["relay"]["status"]')" '[0, "verified", "waiting_compactions"]' "native TUI header identifies the accredited zero-count protocol"
assert_contains "$OUT" 'do not block admission' "native 0/4 continues useful work under the existing policy"
assert_not_contains "$OUT" 'is unverified' "accredited TUI does not fall back to a token predicate"
tail -n +3 "$TESTS_DIR/fixtures/codex-tui-paginated.jsonl" >> "$PN"
run_hook "$H" codex Stop "$(payload Stop p-native "$PN" stop_hook_active=false)"
assert_eq "$OUT" '' "native fourth compaction remains silent in Stop"
assert_eq "$(jget "$NR" 'd["compactions"]["count"],d["relay"]["pending"]')" '[4, true]' "native fourth compaction persists safe-point eligibility"
assert_contains "$(jget "$NR" 'd["compactions"]["signal"]')" 'paginated/window-chain' "durable native count retains protocol provenance"
run_hook "$H" codex PostToolUse "$(payload PostToolUse p-native "$PN")"
assert_contains "$OUT" 'pause safely' "native TUI delivers safe completion or pause at a normal injection point"
run_hook "$H" codex PostToolUse "$(payload PostToolUse p-native "$PN")"
assert_eq "$OUT" '' "native TUI policy delivery is not replayed"
assert_eq "$(jget "$NR" 'd["compactions"]["count"]')" 4 "repeated native hooks do not duplicate boundaries"

# --- timing and silence -------------------------------------------------------------
START=$(python3 -I -c 'import time;print(time.time())')
run_hook "$H" claude PostToolBatch "$(payload PostToolBatch s-time "$T2")"
END=$(python3 -I -c 'import time;print(time.time())')
if python3 -I -c 'import sys; d=float(sys.argv[2])-float(sys.argv[1]); print("# owner PostToolBatch took %.2fs" % d); sys.exit(0 if d < 9 else 1)' "$START" "$END"; then
  pass "owner hook finishes inside its 10s timeout"
else
  fail "owner hook too slow"
fi
# The log holds the deliberate snapshot failures above; an exception would
# appear as a traceback.
assert_not_contains "$(cat "$H/state/context-relay/hook-errors.log" 2>/dev/null)" "Traceback" "no handler exception was logged"

finish
