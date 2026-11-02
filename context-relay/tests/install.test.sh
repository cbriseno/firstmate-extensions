#!/usr/bin/env bash
# Installer and uninstaller through the public CLI, in disposable homes only.
# shellcheck source-path=SCRIPTDIR
. "$(dirname "$0")/lib.sh"
setup_tmp
H="$TMP/home with spaces"
make_home "$H"
SETTINGS="$H/.claude/settings.local.json"
CFG="$H/config/context-relay.json"
cat > "$SETTINGS" <<'EOF'
{"permissions":{"allow":["Bash(git status:*)"]},"statusLine":{"type":"command","command":"personal-status"},
 "hooks":{"Stop":[{"matcher":"","hooks":[{"type":"command","command":"personal-stop"}]}],"Other":[]}}
EOF
cp "$SETTINGS" "$TMP/before.json"
BEFORE=$(cksum < "$SETTINGS")
OUT=$("$CLI" --home "$H" install --dry-run); RC=$?
assert_eq "$RC" 0 "install dry-run succeeds"
assert_eq "$(cksum < "$SETTINGS")" "$BEFORE" "dry-run preserves settings bytes"
assert_no_file "$CFG" "dry-run does not create configuration"
OUT=$("$CLI" --home "$H" install); RC=$?
assert_eq "$RC" 0 "install succeeds with spaces in home path"
assert_eq "$(jget "$SETTINGS" 'd["permissions"]["allow"]')" '["Bash(git status:*)"]' "install keeps personal permissions"
assert_eq "$(jget "$SETTINGS" 'd["statusLine"]["command"]')" personal-status "install keeps the statusline"
assert_eq "$(jget "$SETTINGS" 'len(d["hooks"]["Stop"])')" 2 "install appends alongside the Stop chain"
assert_eq "$(jget "$SETTINGS" 'len([h for groups in d["hooks"].values() for g in groups for h in g["hooks"] if h["command"].endswith("#fm-context-relay")])')" 7 "exactly seven identifiable hook entries"
assert_eq "$(jget "$CFG" 'd["relay_mode"]')" warn "initial config only enables warn"
assert_contains "$(cat "$H/data/context-relay/config-changes.jsonl")" install-defaults "initial config creation is audited"
# Execute the installed command through a shell, exercising quoting and marker.
CMD=$(jget "$SETTINGS" 'd["hooks"]["PreCompact"][0]["hooks"][0]["command"]')
OUT=$(printf '{}' | bash -c "$CMD"); RC=$?
assert_eq "$RC:$OUT" '0:' "installed hook command is executable and inert without an owner"
BEFORE=$(cksum < "$SETTINGS")
"$CLI" --home "$H" install >/dev/null
assert_eq "$(cksum < "$SETTINGS")" "$BEFORE" "install is idempotent"
printf '{captain-edited but invalid}\n' > "$CFG"
BEFORE_CFG=$(cksum < "$CFG")
"$CLI" --home "$H" install >/dev/null
assert_eq "$(cksum < "$CFG")" "$BEFORE_CFG" "install never overwrites an existing config, even invalid"
OUT=$("$CLI" --home "$H" uninstall --dry-run)
assert_eq "$(cksum < "$SETTINGS")" "$BEFORE" "uninstall dry-run preserves settings"
OUT=$("$CLI" --home "$H" uninstall); RC=$?
assert_eq "$RC" 0 "uninstall succeeds"
if python3 -I -c 'import json,sys;sys.exit(json.load(open(sys.argv[1])) != json.load(open(sys.argv[2])))' "$SETTINGS" "$TMP/before.json"; then
  pass "uninstall restores all pre-existing content"
else
  fail "uninstall changed unrelated settings"
fi
assert_eq "$(cksum < "$CFG")" "$BEFORE_CFG" "uninstall keeps config and history"
BEFORE=$(cksum < "$SETTINGS")
"$CLI" --home "$H" uninstall >/dev/null
assert_eq "$(cksum < "$SETTINGS")" "$BEFORE" "uninstall is idempotent"

# A command added by someone else inside one of our groups must survive.
"$CLI" --home "$H" install >/dev/null
python3 -I -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["hooks"]["PreCompact"][0]["hooks"].append({"type":"command","command":"other-hook"}); json.dump(d,open(p,"w"))' "$SETTINGS"
"$CLI" --home "$H" uninstall >/dev/null
assert_eq "$(jget "$SETTINGS" 'd["hooks"]["PreCompact"][0]["hooks"]')" '[{"type": "command", "command": "other-hook"}]' "uninstall removes only its own command in a mixed group"

printf '{malformed\n' > "$SETTINGS"
BEFORE=$(cksum < "$SETTINGS")
OUT=$("$CLI" --home "$H" install); RC=$?
assert_eq "$RC" 2 "malformed settings cause refusal"
assert_eq "$(cksum < "$SETTINGS")" "$BEFORE" "malformed settings preserved"
rm "$SETTINGS"
ln -s "$TMP/before.json" "$SETTINGS"
OUT=$("$CLI" --home "$H" install); RC=$?
assert_eq "$RC" 2 "settings symlinks refused"
OUT=$("$CLI" --home "$H" uninstall); RC=$?
assert_eq "$RC" 2 "uninstall also refuses settings symlinks"
rm "$SETTINGS"
cp "$TMP/before.json" "$SETTINGS"
git -C "$H" add -f .claude/settings.local.json
OUT=$("$CLI" --home "$H" install); RC=$?
assert_eq "$RC" 2 "tracked settings refused"
OUT=$("$CLI" --home "$H" uninstall); RC=$?
assert_eq "$RC" 2 "uninstall refuses tracked settings"

HC="$TMP/firstmate codex home"
make_home "$HC"
UC="$TMP/codex user"
codex_cli() { CODEX_HOME="$UC" "$CLI" --home "$HC" "$@" --harness codex; }

# A preview gives concrete commands without creating the user source or defaults.
OUT=$(codex_cli install --dry-run); RC=$?
assert_eq "$RC" 0 "Codex preparation preview succeeds"
assert_contains "$OUT" 'hooks.json (proposed)' "Codex preview contains the proposed hook diff"
assert_contains "$OUT" 'command:' "Codex preview shows the exact commands for human review"
assert_no_file "$UC" "Codex preview does not create the user directory"
assert_no_file "$HC/config/context-relay.json" "Codex preview does not create home defaults"

mkdir -p "$UC"
printf 'personal settings and trust sentinel\n' > "$UC/config.toml"
TRUST_BEFORE=$(cksum < "$UC/config.toml")
cat > "$UC/hooks.json" <<'EOF'
{"custom":"keep", "hooks":{"Stop":[{"matcher":"","hooks":[{"type":"command","command":"personal-stop","timeout":7}]}],"Other":[]}}
EOF
cp "$UC/hooks.json" "$TMP/codex-before.json"
OUT=$(codex_cli install); RC=$?
assert_eq "$RC" 3 "Codex prepares hooks but never returns verified activation"
assert_contains "$OUT" 'state: prepared' "Codex reports prepared state"
assert_contains "$OUT" 'activation: UNVERIFIED' "Codex distinguishes activation from preparation"
assert_contains "$OUT" '/hooks' "Codex points to native human review"
assert_contains "$OUT" 'Pending trust' "Codex distinguishes pending trust from verified activation"
assert_eq "$(jget "$UC/hooks.json" 'len([h for groups in d["hooks"].values() for g in groups for h in g["hooks"] if h["command"].endswith("#fm-context-relay")])')" 7 "Codex prepares exactly seven identifiable handlers"
assert_eq "$(jget "$UC/hooks.json" 'd["hooks"]["PostToolUse"][0]["hooks"][0]["timeout"]')" 10 "Codex uses PostToolUse with bounded timeout"
assert_eq "$(jget "$UC/hooks.json" 'd["hooks"]["PreCompact"][0]["hooks"][0]["timeout"]')" 60 "Codex preserves the checkpoint budget"
assert_eq "$(jget "$HC/config/context-relay.json" 'd["relay_mode"]')" warn "Codex preparation only creates warn defaults"
assert_eq "$(cksum < "$UC/config.toml")" "$TRUST_BEFORE" "Codex preparation preserves user settings and trust bytes"
assert_eq "$(jget "$UC/hooks.json" 'd["hooks"]["Stop"][0]["hooks"][0]["command"]')" personal-stop "Codex preserves foreign Stop hook"
CMD=$(jget "$UC/hooks.json" 'd["hooks"]["PreCompact"][0]["hooks"][0]["command"]')
OUT=$(printf '{}' | bash -c "$CMD"); RC=$?
assert_eq "$RC:$OUT" '0:' "prepared Codex command quotes spaces and remains inert without owner"
BEFORE=$(cksum < "$UC/hooks.json")
BEFORE_AUDIT=$(cksum < "$HC/data/context-relay/config-changes.jsonl")
OUT=$(codex_cli install); RC=$?
assert_eq "$RC" 3 "Codex repeat preparation still does not claim activation"
assert_eq "$(cksum < "$UC/hooks.json")" "$BEFORE" "Codex preparation is byte-idempotent"
assert_eq "$(cksum < "$HC/data/context-relay/config-changes.jsonl")" "$BEFORE_AUDIT" "Codex repeat preparation does not add defaults audit"

# Reconcile a stale installation, duplicates, and a foreign handler in our group.
python3 -I - "$UC/hooks.json" <<'PY'
import json,shlex,sys
p=sys.argv[1]; d=json.load(open(p)); own=d['hooks']['PreCompact'][0]['hooks'][0]
args=shlex.split(own['command']); args[0]='/retired path/hook'
own['command']=' '.join(shlex.quote(x) for x in args[:-1])+' '+args[-1]; own['timeout']=999
d['hooks']['PreCompact'][0]['hooks'].append({'type':'command','command':'personal-in-mixed-group'})
d['hooks']['PreCompact'][0]['matcher']='manual'
d['hooks']['Stop'].append(d['hooks']['Stop'][-1])
json.dump(d,open(p,'w'))
PY
OUT=$(codex_cli install); RC=$?
assert_eq "$RC" 3 "Codex reconciles stale and duplicate owned handlers"
assert_eq "$(jget "$UC/hooks.json" 'len([h for groups in d["hooks"].values() for g in groups for h in g["hooks"] if h["command"].endswith("#fm-context-relay")])')" 7 "Codex reconciliation deduplicates only its handlers"
assert_eq "$(jget "$UC/hooks.json" 'd["hooks"]["PreCompact"][0]["hooks"]')" '[{"type": "command", "command": "personal-in-mixed-group"}]' "Codex reconciliation preserves foreign mixed-group handler"
assert_eq "$(jget "$UC/hooks.json" 'd["hooks"]["PreCompact"][0]["matcher"]')" manual "Codex reconciliation preserves foreign group matcher"
assert_eq "$(jget "$UC/hooks.json" 'd["hooks"]["PreCompact"][-1]["hooks"][0]["timeout"]')" 60 "Codex reconciliation restores its timeout"
assert_not_contains "$(cat "$UC/hooks.json")" '/retired path/hook' "Codex reconciliation restores stable executable path"
BEFORE=$(cksum < "$UC/hooks.json")
codex_cli install >/dev/null
assert_eq "$(cksum < "$UC/hooks.json")" "$BEFORE" "Codex reconciliation stays byte-idempotent with mixed groups"

printf '{captain-edited but invalid}\n' > "$HC/config/context-relay.json"
BEFORE_CFG=$(cksum < "$HC/config/context-relay.json")
codex_cli install >/dev/null
assert_eq "$(cksum < "$HC/config/context-relay.json")" "$BEFORE_CFG" "Codex preparation preserves even invalid existing home config"
codex_cli uninstall --dry-run >/dev/null
assert_eq "$(cksum < "$UC/hooks.json")" "$BEFORE" "Codex uninstall preview preserves hooks bytes"
OUT=$(codex_cli uninstall); RC=$?
assert_eq "$RC" 0 "Codex uninstall succeeds"
assert_eq "$(jget "$UC/hooks.json" 'd["hooks"]["PreCompact"][0]["hooks"]')" '[{"type": "command", "command": "personal-in-mixed-group"}]' "Codex uninstall keeps foreign hook from mixed group"
assert_eq "$(jget "$UC/hooks.json" 'd["custom"]')" keep "Codex uninstall keeps unrelated top-level fields"
assert_eq "$(cksum < "$HC/config/context-relay.json")" "$BEFORE_CFG" "Codex uninstall preserves home settings"
assert_eq "$(cksum < "$UC/config.toml")" "$TRUST_BEFORE" "Codex uninstall preserves settings and trust bytes"
BEFORE=$(cksum < "$UC/hooks.json")
codex_cli uninstall >/dev/null
assert_eq "$(cksum < "$UC/hooks.json")" "$BEFORE" "Codex uninstall is byte-idempotent"

# Multiple Firstmate homes share the Codex source without owning each other.
cp "$TMP/codex-before.json" "$UC/hooks.json"
HC2="$TMP/another primary"
make_home "$HC2"
CODEX_HOME="$UC" "$CLI" --home "$HC2" install --harness codex >/dev/null
cp "$UC/hooks.json" "$TMP/other-home-only.json"
codex_cli install >/dev/null
assert_eq "$(jget "$UC/hooks.json" 'len([h for groups in d["hooks"].values() for g in groups for h in g["hooks"] if h["command"].endswith("#fm-context-relay")])')" 14 "Codex preserves the handlers for another home"
BEFORE=$(cksum < "$UC/hooks.json")
CODEX_HOME="$UC" "$CLI" --home "$HC2" install --harness codex >/dev/null
assert_eq "$(cksum < "$UC/hooks.json")" "$BEFORE" "Codex repeat preparation preserves group order with another home present"
codex_cli uninstall >/dev/null
if python3 -I -c 'import json,sys;sys.exit(json.load(open(sys.argv[1])) != json.load(open(sys.argv[2])))' "$UC/hooks.json" "$TMP/other-home-only.json"; then
  pass "Codex uninstall restores the other home full hooks document"
else
  fail "Codex uninstall changed the other home handlers"
fi
CODEX_HOME="$UC" "$CLI" --home "$HC2" uninstall --harness codex >/dev/null
if python3 -I -c 'import json,sys;sys.exit(json.load(open(sys.argv[1])) != json.load(open(sys.argv[2])))' "$UC/hooks.json" "$TMP/codex-before.json"; then
  pass "Codex final uninstall restores the original hooks document"
else
  fail "Codex final uninstall changed unrelated hooks"
fi

# Invalid source documents and symlink targets never get rewritten.
for BAD in '{malformed' '[]' '{"hooks":null}' '{"hooks":[]}' '{"hooks":{"Stop":{}}}'; do
  printf '%s\n' "$BAD" > "$UC/hooks.json"
  BEFORE=$(cksum < "$UC/hooks.json")
  OUT=$(codex_cli install); RC=$?
  assert_eq "$RC" 2 "Codex refuses invalid source: $BAD"
  assert_eq "$(cksum < "$UC/hooks.json")" "$BEFORE" "Codex leaves invalid source intact"
done
printf '{malformed\n' > "$UC/hooks.json"
OUT=$(codex_cli uninstall); RC=$?
assert_eq "$RC" 2 "Codex uninstall refuses malformed source"
rm -- "$UC/hooks.json"
ln -s "$TMP/codex-before.json" "$UC/hooks.json"
OUT=$(codex_cli install); RC=$?
assert_eq "$RC" 2 "Codex install refuses hooks symlink"
OUT=$(codex_cli uninstall); RC=$?
assert_eq "$RC" 2 "Codex uninstall refuses hooks symlink"
OUT=$(codex_cli install --dry-run); RC=$?
assert_eq "$RC" 2 "Codex preview refuses hooks symlink"
rm -- "$UC/hooks.json"
ALIAS="$TMP/codex-user-alias"
ln -s "$UC" "$ALIAS"
OUT=$(codex_cli install --codex-home "$ALIAS"); RC=$?
assert_eq "$RC" 2 "Codex install refuses a symlink directory"
OUT=$(codex_cli uninstall --codex-home "$ALIAS"); RC=$?
assert_eq "$RC" 2 "Codex uninstall refuses a symlink directory"
assert_no_file "$UC/hooks.json" "Codex never writes through the symlink directory"

cp "$TMP/codex-before.json" "$UC/hooks.json"
git -C "$UC" init -q
git -C "$UC" add hooks.json
BEFORE=$(cksum < "$UC/hooks.json")
OUT=$(codex_cli install); RC=$?
assert_eq "$RC" 2 "Codex refuses versioned user hooks"
OUT=$(codex_cli uninstall); RC=$?
assert_eq "$RC" 2 "Codex uninstall refuses versioned user hooks"
assert_eq "$(cksum < "$UC/hooks.json")" "$BEFORE" "Codex preserves versioned user hooks bytes"
git -C "$UC" rm --cached -q hooks.json
rm -- "$UC/hooks.json"

# Explicit destination overrides an ambient user directory, without native Codex.
OVERRIDE="$TMP/explicit user"
OUT=$(codex_cli install --codex-home "$OVERRIDE"); RC=$?
assert_eq "$RC" 3 "explicit Codex directory is supported"
assert_file "$OVERRIDE/hooks.json" "Codex writes only the explicit source"
assert_no_file "$UC/hooks.json" "explicit destination does not write ambient hooks"
OUT=$(codex_cli uninstall --codex-home "$OVERRIDE"); RC=$?
assert_eq "$RC" 0 "explicit Codex directory supports removal"
OUT=$(CODEX_HOME="$UC" "$CLI" --home "$TMP" install --harness codex); RC=$?
assert_eq "$RC" 2 "Codex preparation refuses a non-primary home"
assert_no_file "$UC/hooks.json" "invalid home causes no user-source writes"
finish
