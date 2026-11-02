#!/usr/bin/env bash
# Resident-context edge cases through the read-only measure CLI.
. "$(dirname "$0")/lib.sh"
setup_tmp
H=$TMP/home
make_home "$H"
T=$TMP/claude.jsonl
python3 -I "$FIXTURE" claude-usage "$T" old 450000
python3 -I "$FIXTURE" claude-boundary "$T" boundary-1
python3 -I "$FIXTURE" claude-usage "$T" old 450000
OUT=$("$CLI" --home "$H" measure --harness claude "$T" --json)
assert_eq "$(jout 'd["resident"]["status"]')" no_data "a duplicated usage after a boundary never resurrects old context"
python3 -I "$FIXTURE" claude-usage "$T" new 62000
python3 -I "$FIXTURE" claude-boundary "$T" boundary-1
OUT=$("$CLI" --home "$H" measure --harness claude "$T" --json)
assert_eq "$(jout 'd["resident"]["tokens"], d["compactions"]["count"]')" '[62000, 1]' "a replayed boundary does not invalidate a newer call"
python3 -I -c 'import json,sys; f=open(sys.argv[1],"a"); [f.write(json.dumps({"type":"assistant","message":{"id":"id%d"%i,"model":"claude-opus-5-5","usage":{"input_tokens":70000}}})+"\n") for i in range(210)]' "$T"
python3 -I "$FIXTURE" claude-usage "$T" old 450000
OUT=$("$CLI" --home "$H" measure --harness claude "$T" --json)
assert_eq "$(jout 'd["resident"]["tokens"]')" 70000 "message.id deduplication spans the entire session"
printf '%s\n' '{"type":"assistant","message":{"id":"term","model":"claude-opus-5-5","content":"compact_boundary","usage":{"input_tokens":75000}}}' >> "$T"
OUT=$("$CLI" --home "$H" measure --harness claude "$T" --json)
assert_eq "$(jout 'd["resident"]["tokens"]')" 75000 "mentioning compact_boundary in assistant text does not hide its usage"
printf '{"type":' >> "$T"
OUT=$("$CLI" --home "$H" measure --harness claude "$T" --json)
assert_eq "$(jout 'd["resident"]["reason"]')" catching_up "a partial last row is incomplete data"

C=$TMP/codex.jsonl
python3 -I "$FIXTURE" codex-meta "$C"
python3 -I "$FIXTURE" codex-turn "$C" gpt-6.1-sol
python3 -I "$FIXTURE" codex-tokens "$C" 200000 --total 9999999
python3 -I "$FIXTURE" codex-tokens "$C" 0
OUT=$("$CLI" --home "$H" measure --harness codex "$C" --json)
assert_eq "$(jout 'd["resident"]["status"]')" no_data "Codex zero invalidates old usage even if the compacted event is missing"
python3 -I "$FIXTURE" codex-tokens "$C" 31000
python3 -I "$FIXTURE" codex-turn "$C" gpt-6-luna
OUT=$("$CLI" --home "$H" measure --harness codex "$C" --json)
assert_eq "$(jout 'd["resident"]["reason"]')" model_changed "Codex model switch invalidates usage from the previous model"
python3 -I "$FIXTURE" codex-tokens "$C" 42000
printf '%s\n' '{"type":"compacted","payload":{"window_number":1}}' '{"type":"compacted","payload":{"window_number":1}}' '{"type":"compacted","payload":{"window_number":2}}' >> "$C"
OUT=$("$CLI" --home "$H" measure --harness codex "$C" --json)
assert_eq "$(jout 'd["compactions"]["count"]')" 2 "window_number counts distinct compactions when window_id is absent"
python3 -I "$FIXTURE" codex-compacted "$C" win-2 2
OUT=$("$CLI" --home "$H" measure --harness codex "$C" --json)
assert_eq "$(jout 'd["compactions"]["count"]')" 2 "a replay adding window_id to a known window_number is still one compaction"
OUT=$("$CLI" --home "$H" measure --harness claude "$TMP/absent" 2>&1); RC=$?
assert_eq "$RC" 2 "missing transcripts are reported without inventing usage"
assert_contains "$OUT" 'does not exist' "missing transcript diagnostic names the problem"
assert_no_file "$H/state/context-relay" "measure never creates session state"

# A model switch cannot attribute the old window to the new model.
export FM_CONTEXT_RELAY_THROTTLE_SECONDS=0
python3 -I "$FIXTURE" codex-tokens "$C" 41000 --window 258400
run_hook "$H" codex PostToolUse "$(payload PostToolUse switch-window "$C")"
R=$(record_path "$H" codex switch-window)
assert_eq "$(jget "$R" 'd["window_tokens_observed"]')" 258400 "hook records the observed window for the measured model"
python3 -I "$FIXTURE" codex-turn "$C" gpt-6.1-sol
run_hook "$H" codex PostToolUse "$(payload PostToolUse switch-window "$C")"
assert_eq "$(jget "$R" 'd.get("window_tokens_observed")')" null "model switch clears the old model's window until a new report arrives"
assert_eq "$(jget "$R" 'd["resident"]["status"]')" no_data "model switch does not reuse the previous call's resident context"
finish
