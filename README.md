# Firstmate Extensions 0.1.0

Optional extensions for [Firstmate](https://github.com/cbriseno/firstmate): scoped Spec Kit planning and a minimal Claude context relay. Cloning this repository activates nothing.

## Installation

Python 3.9+, Git and Bash are required. Install the runtime and planning skills with:

```sh
bin/fm-extension install --home "$FIRSTMATE_HOME" --harness claude
bin/fm-extension doctor --home "$FIRSTMATE_HOME"
bin/fm-extension planning status --home "$FIRSTMATE_HOME"
```

The runtime is installed under `$XDG_DATA_HOME/firstmate-extensions/runtime` (or the default local user data directory). Planning skills are installed for both harnesses. Use `bin/fm-extension uninstall` to remove the managed runtime and restore previous skills. See [Planning](planning/README.md) and its [settings contract](planning/settings-contract.md).

## Minimal relay

The relay replaces a Firstmate primary (Claude or Codex) with a fresh session of the same harness, model and effort, in four steps the primary performs itself:

1. run `/stow`;
2. open a sibling Herdr pane;
3. start the same harness there with the instruction to take the firstmate role;
4. read and answer authorized startup dialogs, then close its own pane only after the successor writes its start mark before waiting for the old lock.

Steps 2-4 are `context-relay-lite/relay "$FIRSTMATE_HOME" <claude|codex> <model|-> <effort|->`, run from inside the primary's Herdr pane; `-` keeps the target harness's default.
If startup needs attention, the command returns the new pane's screen and an exact `--watch` command. Resolve the dialog and run that command to continue with the same successor. A login or a startup still blocked after 60 seconds keeps the outgoing primary open; it must use its configured alert route with the blocking screen. Herdr `working` or `done` alone never permits closing the old pane.
Register `context-relay-lite/relay-check "$FIRSTMATE_HOME"` as a Claude or Codex Stop hook to check the latest model call.
The checker reads `config/relay-lite.json`; the existing `{"threshold_tokens": 250000}` remains effective for both primary harnesses when no per-harness token value is set.
`context-relay-lite/relay-migrate "$FIRSTMATE_HOME"` keeps that scalar as the primary fallback, sets worker values to zero, retains other fields and copies the previous relay-lite file to `relay-lite.json.before-migration`.
It also archives the contents of `config/context-relay.json` and `config/umbrales-relevo` under the informational `legacy` key and leaves those files in place for existing readers.
Compaction predicates, model-specific values and old conditional thresholds cannot be converted into equivalent independent limits automatically, so the archived values do not activate thresholds.
Migration does not install hooks or activate workers.

The configuration contract is:

```json
{"thresholds": {
  "primary": {"codex": {"percent": 0, "tokens": 250000}, "claude": {"percent": 0, "tokens": 250000}},
  "worker":  {"codex": {"percent": 0, "tokens": 0}, "claude": {"percent": 0, "tokens": 0}}
}}
```

Each pair is independent: `0` disables that axis, and reaching either enabled limit triggers the hook.
Missing worker values are disabled; a missing primary token value falls back to the scalar.
These numbers illustrate a migration, not recommended defaults; worker thresholds require a pilot and Claude calibration.
The checker uses the latest input and cache tokens and a window reported for the current model, never a model-name guess.
Codex transcript token-count events include the window when available; if a producer has another actual window measurement it may include `"context_window":{"model":"MODEL_ID","tokens":N}` in the hook payload, which is accepted only when the transcript identifies that same model.
Without a window, percentage is unknown and the token limit still applies; without usage both are unknown.
A model change invalidates an earlier measurement.

For integrations, `relay-check --json "$FIRSTMATE_HOME"` consumes the same hook payload on stdin and returns `relay-lite.check.v1` JSON with `role`, `harness`, `model`, `used_tokens`, `window_tokens`, `percent_used`, `threshold_tokens`, `threshold_percent`, `reached` (the triggered axes), `state` (`reached`, `below`, `unknown`, or `skipped`) and `reason`.
Unknown numeric values are JSON null; a missing window sets `reason` to `window_unavailable` and state to `unknown` unless a token limit was reached.
`FM_TASK_ID` selects worker; otherwise the checker only accepts a primary in its Firstmate home.
Without `--json`, a reached limit exits 2 with an instruction on stderr and all other results exit 0, as required by the Stop hook; `stop_hook_active` is skipped.
The worker instruction requests a safe handoff, and Firstmate owns hook registration, relaunch and notification to the primary.
The primary uses the existing `/relay` skill.
Installation does not configure hooks; Codex asks once to trust a new hook.

## License

MIT; see [LICENSE](LICENSE), [NOTICE](NOTICE), and [Spec Kit's license](vendor/spec-kit/1.2.4/LICENSE).

## Maintaining a local installation

Use one repository with `main` for shared fixes and `local` for private settings and evidence.

1. Check out `main`, make the shared fix, run the affected local checks and commit.
   Keep installation values configurable with empty defaults; retain private values only on `local`.
2. Publish only `main` to the public repository through the existing publication gate.
   Audit the exact destination and ref before publishing.
3. Check out `local`, run `git rebase main` and repeat the affected checks.
   Existing hooks keep their checkout paths and receive the same shared fix.
