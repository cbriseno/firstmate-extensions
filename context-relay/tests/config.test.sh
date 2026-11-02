#!/usr/bin/env bash
# Settings through the CLI: validate, show and the approval-gated set.
# shellcheck source-path=SCRIPTDIR
. "$(dirname "$0")/lib.sh"
setup_tmp

H=$TMP/home
make_home "$H"
CFG=$H/config/context-relay.json
LOG=$H/data/context-relay/config-changes.jsonl

OUT=$("$CLI" --home "$H" validate --file "$EXT/context-relay.example.json"); RC=$?
assert_eq "$RC" 0 "the shipped example validates"
assert_contains "$OUT" "requires a verified rollout contract" "the example's Codex compaction trigger carries its provenance caveat"

OUT=$("$CLI" --home "$H" validate); RC=$?
assert_eq "$RC" 0 "missing settings file is valid (defaults)"
OUT=$("$CLI" --home "$H" show --json); RC=$?
assert_eq "$RC" 0 "show works without a settings file"
assert_eq "$(jout 'd["status"] + "/" + d["top"]["compactions_before_relay"]["source"]')" "missing/default" "show reports default origin"
assert_eq "$(jout '[m["model"] for m in d["models"]]')" '["claude-opus-5-5", "claude-sonnet-5-5", "gpt-6.1-sol"]' "defaults carry optional model profiles"
assert_eq "$(jout '[(m["request_tokens"], m["stop_new_work_tokens"]) for m in d["models"]]')" '[[250000, 400000], [null, null], [200000, 310000]]' "defaults preserve pressure thresholds without inventing Sonnet thresholds"
assert_eq "$(jout 'd["unknown_model"]["known"], d["unknown_model"]["trigger"], d["unknown_model"]["request_tokens"]')" '[false, "compactions", null]' "unknown models: default trigger, no token thresholds"
assert_eq "$(jout 'd["phone_notifications"]')" false "phase 1 never notifies the phone"

check_invalid() {  # <json> <expected substring> <label>
  printf '%s\n' "$1" > "$TMP/bad.json"
  OUT=$("$CLI" --home "$H" validate --file "$TMP/bad.json"); RC=$?
  assert_eq "$RC" 1 "$3: rejected"
  assert_contains "$OUT" "$2" "$3: message"
}
check_invalid '{"compactions_before_relay": 0}' "integer >= 1" "N < 1"
check_invalid '{"relay_mode": "auto"}' "requires durable authorization" "relay_mode auto without authorization"
printf '%s\n' '{"relay_mode":"assisted"}' > "$TMP/assisted.json"
OUT=$("$CLI" --home "$H" validate --file "$TMP/assisted.json"); RC=$?
assert_eq "$RC" 0 "assisted is valid without standing automatic authority"
check_invalid '{"compaction_before_relay": 2}' "unknown key" "typo in a key"
check_invalid '{"models":{"claude-opus-5-5":{"trigger":"tokens","request_tokens":400000,"stop_new_work_tokens":250000}}}' "must be less" "request >= stop"
check_invalid '{"models":{"claude-opus-5-5":{"trigger":"tokens"}}}' "requires request_tokens" "tokens trigger without request_tokens"
check_invalid '{"models":{"claude-opus-5-5":{"trigger":"tokens","request_tokens":1200000,"window_tokens":1000000}}}' "is unreachable" "threshold beyond the window"
check_invalid '{"models":{"kimi-k3":{"trigger":"compactions"}}}' "verified counting" "compactions trigger on a harness without verified counting"
check_invalid '{"models":{"Claude-Opus-5-5[1m]":{"trigger":"compactions"}}}' "normalized" "non-normalized model id"
check_invalid '{"max_session_hours": 0}' "greater than 0" "max_session_hours 0"
check_invalid '{"precompact_checkpoint": "yes"}' "true or false" "precompact_checkpoint not boolean"
check_invalid '{"unknown_model": "relay"}' "warn_only" "unknown_model other than warn_only"
check_invalid '{"default_trigger": "never"}' "default_trigger" "unknown trigger"
check_invalid '{nope' "invalid JSON" "unparseable file"
check_invalid '{"models": null}' "models must be an object" "models null"
check_invalid '{"max_session_hours": NaN}' "greater than 0" "non-finite max hours"
printf '{"models":{"claude-opus-5-5":{"trigger":"tokens","request_tokens":950000,"window_tokens":1000000}}}\n' > "$TMP/warn.json"
OUT=$("$CLI" --home "$H" validate --file "$TMP/warn.json"); RC=$?
assert_eq "$RC" 0 "near-window threshold is a warning, not an error"
assert_contains "$OUT" "within 10%" "near-window warning names the risk"

# --- set: requires reason and approval, writes atomically, logs everything -------
OUT=$("$CLI" --home "$H" set compactions_before_relay 3 --reason "prueba" 2>&1); RC=$?
assert_eq "$RC" 2 "set without --approved is refused"
assert_no_file "$CFG" "refused set writes nothing"
OUT=$("$CLI" --home "$H" set compactions_before_relay 3 --approved "user: yes" 2>&1); RC=$?
assert_eq "$RC" 2 "set without --reason is refused"
OUT=$("$CLI" --home "$H" set compactions_before_relay 3 --reason "user prefers 3" --approved "user in chat: yes, set it to 3" --dry-run); RC=$?
assert_eq "$RC" 0 "dry run succeeds"
assert_no_file "$CFG" "dry run writes nothing"
OUT=$("$CLI" --home "$H" set compactions_before_relay 3 --reason "user prefers 3" --approved "user in chat: yes, set it to 3"); RC=$?
assert_eq "$RC" 0 "approved set succeeds"
assert_eq "$(jget "$CFG" 'd["compactions_before_relay"]')" 3 "value written"
assert_eq "$(jget "$CFG" 'sorted(d.keys())')" '["compactions_before_relay"]' "set writes only the changed key"
LAST=$(tail -1 "$LOG")
assert_contains "$LAST" '"kind": "set"' "change logged"
assert_contains "$LAST" '"reason": "user prefers 3"' "log keeps the reason"
assert_contains "$LAST" '"approved": "user in chat: yes, set it to 3"' "log keeps the approval"
assert_contains "$LAST" '"at": "20' "log keeps the date"
assert_contains "$LAST" '"old": null, "new": 3' "log keeps old and new values"
OUT=$("$CLI" --home "$H" set models.gpt-6.1-sol.request_tokens 220k --reason "validado en sesión" --approved "user: ok"); RC=$?
assert_eq "$RC" 0 "dotted model ids and k suffix accepted"
assert_eq "$(jget "$CFG" 'd["models"]["gpt-6.1-sol"]["request_tokens"]')" 220000 "220k stored as 220000"
assert_eq "$(jget "$CFG" 'd["models"]["claude-opus-5-5"]["stop_new_work_tokens"]')" 400000 "adding a model value keeps the default models"
BEFORE=$(cksum < "$CFG")
OUT=$("$CLI" --home "$H" set models.gpt-6.1-sol.request_tokens 400k --reason "x" --approved "user: ok"); RC=$?
assert_eq "$RC" 1 "a change that would make the file invalid is refused"
assert_contains "$OUT" "must be less" "refusal explains why"
assert_eq "$(cksum < "$CFG")" "$BEFORE" "refused change leaves the file untouched"
OUT=$("$CLI" --home "$H" set relay_mode auto --reason "x" --approved "user: ok"); RC=$?
assert_eq "$RC" 1 "relay_mode auto cannot be set in phase 1"
OUT=$("$CLI" --home "$H" set models.gpt-6.1-sol.request_tokens --unset --reason "volver al valor de model" --approved "user: ok"); RC=$?
assert_eq "$RC" 0 "a compactions-only model does not require a token request threshold"
"$CLI" --home "$H" set models.claude-sonnet-5-5.trigger tokens --reason "x" --approved "user: ok" >/dev/null 2>&1; RC=$?
assert_eq "$RC" 1 "a new model with trigger tokens needs request_tokens first"
"$CLI" --home "$H" set models.claude-sonnet-5-5.request_tokens 150k --reason "nuevo model" --approved "user: ok" >/dev/null; RC=$?
assert_eq "$RC" 0 "a new model entry can be added field by field"
"$CLI" --home "$H" set models.claude-sonnet-5-5 --unset --reason "quitar" --approved "user: ok" >/dev/null; RC=$?
assert_eq "$RC" 0 "a model entry can be removed"
assert_eq "$(jget "$CFG" '"claude-sonnet-5-5" in d["models"]')" false "model removed"
assert_eq "$(grep -c '"kind": "set"' "$LOG")" 5 "every applied change logged, refused ones not"
MODE=$(stat -c %a "$CFG" 2>/dev/null || stat -f %Lp "$CFG")
assert_eq "$MODE" 644 "settings file keeps readable permissions"
TEMPS=$(find "$H/config" -maxdepth 1 -name '*.tmp' -print)
assert_eq "$TEMPS" '' "no temp file left behind"

# A hand edit that breaks the file can be repaired with set (after approval).
python3 -I -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["compactions_before_relay"]=0; json.dump(d,open(p,"w"))' "$CFG"
OUT=$("$CLI" --home "$H" validate); RC=$?
assert_eq "$RC" 1 "a broken hand edit is reported"
OUT=$("$CLI" --home "$H" show)
assert_contains "$OUT" "INVALID" "show flags the invalid file"
OUT=$("$CLI" --home "$H" set compactions_before_relay 2 --reason "corregir 0 inválido" --approved "user: yes, corrígelo"); RC=$?
assert_eq "$RC" 0 "set repairs an invalid value"
OUT=$("$CLI" --home "$H" validate); RC=$?
assert_eq "$RC" 0 "file valid again"
printf '{broken' > "$CFG"
OUT=$("$CLI" --home "$H" set compactions_before_relay 2 --reason "x" --approved "user: ok" 2>&1); RC=$?
assert_eq "$RC" 1 "set refuses to rewrite an unparseable file"
assert_eq "$(cat "$CFG")" "{broken" "unparseable file left for a hand fix"

# --- show reads session records and windows --------------------------------------
printf '{}\n' > "$CFG"
mkdir -p "$H/state/context-relay/sessions"
cat > "$H/state/context-relay/sessions/codex-x1.json" <<'EOF'
{"schema":"fm-context-relay-session.v1","harness":"codex","session_id":"x1","model":"gpt-6.1-sol","window_tokens_observed":400000,
 "resident":{"status":"ok","tokens":123000,"at":"2026-10-29T00:00:00Z"},"compactions":{"count":1,"counting":"unverified"},
 "effective":{"effective_trigger":"tokens"},"request":{"level":1,"opened_at":"2026-10-29T00:00:00Z","reasons":[]},"pending":[],"delivered":["level:1"]}
EOF
OUT=$("$CLI" --home "$H" show)
assert_contains "$OUT" "codex x1" "show lists recent sessions"
assert_contains "$OUT" "context 123.0k" "show prints the resident context"
assert_contains "$OUT" "historical warning level 1" "show labels the recorded warning as historical"
OUT=$("$CLI" --home "$H" validate)
assert_contains "$OUT" "observed 400000" "validate flags a window different from the configured one"

OUT=$(FM_HOME="$H" "$CLI" show --json | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["home"])')
assert_eq "$OUT" "$H" "FM_HOME selects the home"
OUT=$( cd "$H" && "$CLI" show --json | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["home"])')
assert_eq "$OUT" "$H" "the current directory selects the home"
OUT=$( cd "$TMP" && "$CLI" show 2>&1 ); RC=$?
if [ "$(cd "$EXT/../../.." 2>/dev/null && pwd)" != "${FIRSTMATE_ROOT:-}" ]; then
  assert_eq "$RC" 2 "no resolvable home: usage error"
fi

write_config "$H" '{"compactions_before_relay":3,"models":{"claude-opus-5-5":{"compactions_before_relay":2}}}'
OUT=$("$CLI" --home "$H" show --json)
assert_eq "$(jout 'd["models"][0]["compactions_before_relay"],d["unknown_model"]["compactions_before_relay"]')" '[2, 3]' "per-model compaction limit overrides the home default"
printf '{}\n' > "$CFG"
python3 -I -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["window_tokens_observed"]=258400; json.dump(d,open(p,"w"))' "$H/state/context-relay/sessions/codex-x1.json"
OUT=$("$CLI" --home "$H" validate); RC=$?
assert_eq "$RC" 1 "validate rejects a threshold beyond the observed window"
assert_contains "$OUT" 'observed window is 258400' "observed-window error states its evidence"
rm "$CFG"
ln -s "$TMP/never-created" "$CFG"
OUT=$("$CLI" --home "$H" set compactions_before_relay 3 --reason x --approved 'fixture approval' 2>&1); RC=$?
assert_eq "$RC" 2 "set refuses config symlinks"
assert_no_file "$TMP/never-created" "refused symlink edit does not follow the target"
rm "$CFG"
OUT=$("$CLI" --home "$H" validate --json); RC=$?
assert_eq "$(jout 'd["valid"]')" true "missing default config still produces JSON when requested"
OUT=$("$CLI" --home "$H" validate --file "$TMP/missing.json" --json); RC=$?
assert_eq "$RC" 1 "explicitly missing validation input is an error"
write_config "$H" '{"models":{"gpt-6.1-sol":false}}'
OUT=$("$CLI" --home "$H" validate); RC=$?
assert_eq "$RC" 1 "invalid model entry with observed session data reports validation errors"
assert_not_contains "$OUT" Traceback "invalid observed model is never evaluated as valid settings"

# Confirmed policy is one atomic, audited owner edit, independent of pressure.
PH=$TMP/policy-home
make_home "$PH"
POLICY='{"codex":{"compactions":4},"claude":{"compactions":2,"min_context_percent":80}}'
OUT=$("$CLI" --home "$PH" set relay_policy "$POLICY" --reason 'confirmed policy fixture' --approved 'explicit fixture approval'); RC=$?
assert_eq "$RC" 0 "owner CLI accepts the complete per-harness policy atomically"
assert_eq "$(jget "$PH/config/context-relay.json" 'd["relay_policy"]["codex"]["compactions"],d["relay_policy"]["claude"]["min_context_percent"]')" '[4, 80]' "owner writes both confirmed predicates"
assert_contains "$(tail -1 "$PH/data/context-relay/config-changes.jsonl")" '"approved": "explicit fixture approval"' "policy change preserves owner approval audit"
mkdir -p "$PH/state/context-relay/sessions"
cat > "$PH/state/context-relay/sessions/codex-old.json" <<'EOF'
{"schema":"fm-context-relay-session.v1","harness":"codex","session_id":"old","model":"gpt-6.1-sol","request":{"level":2},"relay":{"policy":{"harness":"codex","compactions":2,"min_context_percent":null},"pending":true}}
EOF
OUT=$("$CLI" --home "$PH" show --json); RC=$?
assert_eq "$(jout 'd["sessions"][0]["request"]["level"],d["sessions"][0]["relay"]["pending"],d["sessions"][0]["relay"]["status"]')" '[2, false, "awaiting_evaluation"]' "old level and stale pending policy never imply current eligibility in show"
OUT=$("$CLI" --home "$PH" set relay_policy '{"codex":{"compactions":0}}' --reason x --approved fixture); RC=$?
assert_eq "$RC" 1 "owner rejects an invalid replacement policy"
assert_eq "$(jget "$PH/config/context-relay.json" 'd["relay_policy"]["codex"]["compactions"]')" 4 "rejected policy leaves approved predicate intact"

finish
