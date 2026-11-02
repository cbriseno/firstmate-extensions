# Optional planning

For UI integrations, use the [versioned settings contract](settings-contract.md).

Planning is independent of the context relay. It uses scoped Firstmate skills and
private configuration, without changing Firstmate's tracked files or dispatcher.
A primary must load and follow `firstmate-planning`; this is semantic routing, not
a deterministic dispatch hook. Verify the durable task brief before claiming use.
Firstmate without this module retains native planning. Installing skills alone does
not enable Spec Kit, grant native trust, start models, or change delivery authority.

## Setup

Requires Python 3.11+ and POSIX file locking. Bash is required for project
verification and the shell wrapper; without it installation-wide status remains
available. Git and a Firstmate home are required for project identity and scoped
home/project operations; without them installation-wide planning remains
available. Herdr and provider tools are optional and unnecessary for planning.
The fixture suite is tested on Linux; native client discovery and other platforms
have not been verified by these fixtures. No third-party Python packages, `uv`,
`specify-cli`, model calls or network access are needed at runtime.

From the distribution root run `planning/bin/install-skills install --dry-run`,
review, then `planning/bin/install-skills install`. This copies four scoped skills
to the selected user's Codex and Claude Code skill folders. The primary skill
includes a self-contained runtime copy, so moving or renaming the source checkout
does not break it. Restart sessions for native catalog discovery; loading/trust
must be checked in the actual harness. The installer backs up replaced skills,
is idempotent, and refuses modified installed files. Use `uninstall` (optionally
`--dry-run`) to restore backups. Never discard the install state while installed.
It does not prepare projects or touch their instructions.

The executable is `bin/fm-extension` in the source distribution, or
`scripts/fm-extension` relative to the loaded primary skill. Both find their
resources from their own location. The checkout's name and cwd are irrelevant.
The examples below use `CLI` for that executable and `HOME`/`PROJECT` for explicit
runtime locations, not literal paths or shell variables to overwrite.

## Scopes and identity

For new tasks, precedence is explicit task brief override, private project override,
home default, installation default, built-in `native`. Project values are
`native|speckit|inherit`; clearing any scope restores inheritance and can therefore
enable Spec Kit. Disabling is an explicit `native` preference. Project files,
authored specs, constitution, decisions and active tasks remain after disabling.

Private state lives under `XDG_CONFIG_HOME/fm-planning` (or the user's normal config
folder) and `FM_HOME/config/planning`. Each uses versioned JSON. The installation
registry assigns random private IDs to canonical project roots plus filesystem
identity. It records registered homes for the full exception inventory. IDs do not
come from basenames or remote URLs; clones, forks and worktrees are separate roots.
A copied project needs its own preparation receipt. A move/rename preserves the ID
only after `planning relocate --id ID --project NEW_ROOT` verifies its identity.
A filesystem move that changes identity requires an explicit new registration and
review; automatic reassociation would risk applying preferences to the wrong clone.

```text
CLI planning status [--home HOME] [--project PROJECT]
CLI planning set --scope installation --mode speckit
CLI planning set --scope home --home HOME --mode native
CLI planning set --scope project --home HOME --project PROJECT --mode speckit
CLI planning clear --scope project --home HOME --project PROJECT
CLI planning clear --scope home --home HOME
CLI planning clear --scope installation
CLI planning set --scope installation --mode native --clear-project-overrides --dry-run
CLI planning set --scope installation --mode native --clear-project-overrides
CLI planning resolve --home HOME --project PROJECT [--task-mode native|speckit]
CLI planning resolve --home HOME --project PROJECT --brief BRIEF [--require-core]
```

`--clear-project-overrides` clears exceptions only within the selected scope: one
home, or every registered home at installation scope. It preserves home defaults.
For a complete installation-wide disable, inspect the dry-run inventory, apply the
native installation default with that flag, then clear each explicit home default
that still selects speckit. Unknown/unregistered homes cannot be enumerated; state
that boundary. Enabling a default preserves and reports explicit exceptions.
Omit `--home` to inspect installation-wide status without a Firstmate home.
Status lists all registered project exceptions with provenance, pending/failed
causes, home exceptions, preparation and selected-project resolution. Exit 1 means
an invalid configuration or failed inventory item needs repair, never native fallback.

The previous `FM_HOME/config/planning-mode` line is read only while the new home
config is absent. The compatibility wrapper `planning/bin/planning-mode
show|native|speckit` remains available; writes create the new home config without
rewriting the legacy file. Clear on the new config masks the legacy value and
restores real inheritance. Private state is not intended for version control.

`resolve` returns `brief_lines`: mode, origin, resolver version, language and core
version/digest for speckit. The primary freezes these in the durable brief before
dispatch; workers and resumes retain them. Later default or pin changes affect
new tasks only. `--require-core` stops a dependent full Spec Kit cycle while core
is pending or failed. The existing proportional matrix still routes mechanical
changes and small bounded bugs directly even in speckit mode.

Messages and generated prose default to English. `--language en|es` on a scoped
set persists language; `FM_PLANNING_LANGUAGE` or per-command `--language` selects
human summaries. JSON protocol keys, diagnostics and upstream templates remain
English; `planning_language` tells the assigned stage how to author its prose.

## Guarded first preparation

A mode preference does not authorize writing a third-party repository. Preparation
runs in the assigned worker checkout under the project's delivery mode. The primary
arranges this automatically when a dependent route needs it; no manual per-project
Spec Kit install is required. Read root AGENTS.md, CLAUDE.md and existing constitution.
They remain restrictions and are never rewritten. Structured `planning_policy:
native|speckit|inherit` declarations are enforced; arbitrary policies require the
worker's review, not a claim that a parser understands every instruction.

```text
CLI speckit prepare --home HOME --project PROJECT --dry-run
CLI speckit prepare --home HOME --project PROJECT --preview-digest DIGEST --delivery-mode local-only
CLI speckit verify --home HOME --project PROJECT
CLI speckit rollback --home HOME --project PROJECT
```

The dry-run reports exact file additions/keeps and hashes, pin, mode provenance,
policy text/digest, destinations and offline availability. If policy files exist,
read them and add `--policy-review DIGEST` using `policy.digest`. Apply requires
the current `preview_digest`; changed inputs require another dry-run. Delivery mode
is explicit (`local-only`, `direct-PR`, `no-mistakes`) and grants no extra authority.
It never starts a pipeline, Git operation, workflow, external write or trust change.

Preflight verifies vendor/license/helper hashes, available disk space, permissions,
symlinks and unknown/modified integration files. It rejects conflicts without force.
A lock per private root serializes preparation across homes; separate worktree roots
have separate identity and feature selection. A same-filesystem staging directory,
write-ahead journal and byte/mode backups protect managed files. Failure restores
owned files and keeps failed/pending evidence; interrupted journals require explicit
rollback. Re-run is idempotent. Keep the private receipt and backup while prepared.
A config toggle never reports effective speckit unless project verification succeeds.

Only five restricted stages are projected to `.agents/skills` and `.claude/skills`:
specify, clarify, plan, tasks and analyze. Templates, local helpers, integration
metadata and the upstream license go under `.specify`. Existing constitution is
retained. There are no extensions, presets, automatic workflows/handoffs, Git
branch/commit operations, task-to-issue commands, hidden CLI downloads or remote writes.
The helper uses explicit `SPECIFY_FEATURE_DIRECTORY` within the project and
`SPECIFY_FEATURE_NO_PERSIST=1`, never shared feature selection. It creates missing
templates without overwriting authored artifacts; agents supply the stage reasoning.

Verify exercises all stage helpers and prerequisites in a disposable feature and
checks skill files at their documented destinations. `verified` means static
preparation, while `native_discovery: unverified` and `trusted: unknown` require
separate native harness evidence. These fixtures do not establish actual model
reasoning, client discovery, trust, end-to-end dispatch or installed activation.

Rollback is distinct from disable. It restores the pre-preparation bytes/modes of
unchanged owned files and removes only empty owned directories. Modified managed
files stop rollback before any removal; authored artifacts always stay. Never use
rollback to cancel active tasks: their owner must first establish no dependency on
the prepared environment. An interrupted journal is recoverable with the same command.

## Pin and upgrade

The restricted core is derived from the immutable upstream Spec Kit v1.2.4 commit
recorded in [the vendor manifest](../vendor/spec-kit/1.2.4/manifest.json). Upstream is
MIT, copyright GitHub, Inc.; its [license](../vendor/spec-kit/1.2.4/LICENSE) is preserved
verbatim and copied into each preparation. The manifest lists each shipped file,
upstream source/hash and any restriction adaptation. [The integration pin](pin.json)
adds the manifest and helper hashes and an aggregate digest. This is a static subset,
not a bundled CLI, wheel, dependency environment or promise of all upstream features.

An upgrade is a reviewed extension release, never a turn-time download or `latest`.
Add a new version beside the existing vendor; do not replace 1.2.4 bytes. Verify the
new tag-to-commit mapping, license and file digests, review prompt/script/template
diffs for forbidden automation, update compatibility and the version gate/pin, and
run the affected fixtures. Build acquisition needs network only if the exact source
is not cached; record the immutable revision and verify hashes before accepting it.
The shipped runtime is fully offline and has no download fallback.

Project upgrades require a three-way preview (old vendor, current project, new vendor),
explicit conflict handling, journal/backup, successful verification and exact rollback.
The current CLI deliberately rejects a different installed pin; it cannot overwrite
one as an upgrade shortcut. Keep the previous distribution, receipt and environment
for active briefs. Test empty and modified projects plus simultaneous worktrees before
allowing new tasks onto a new pin. Authored specs and project data are never reverted.

## Checks and measurement

The settings contract regressions run with `python3 -B planning/tests/settings.test.py`.

Run `python3 -B planning/tests/scopes.test.py`, `python3 -B
planning/tests/install.test.py` and `python3 -B planning/tests/measure-task.test.py`.
They use disposable fixtures only. The decision matrix has a separate manual checklist
in `tests/decision-checklist.md`; no model behavior evaluation is implied by syntax,
fixture or skill-format checks.

`planning/bin/measure-task --home HOME TASK_ID` reads the durable brief, task metadata,
status and exact recorded Codex session IDs. `--sessions-dir` selects fixtures;
`--manifest JSON` supports external records with explicit `sessions_complete: true`.
It never calls a model. Check session completeness before comparing totals. Missing,
malformed, duplicate or truncated records make affected metrics unavailable. Wall time
includes pauses; active intervals may include tool waits. Tokens use the final
cumulative counter per session, not snapshot sums; cached input and reasoning are
breakdowns, not extra tokens. Context/re-read counts are limited observable heuristics,
not proof of quality, information loss or causation. Compare planning approaches only
under a separate authorized protocol with equal cases, evidence, models, budgets and
independent judging; do not equate token counts with quality or assumed cost.
