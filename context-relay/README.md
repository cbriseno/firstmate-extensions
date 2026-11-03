# Context relay

Anonymous diagnostic event fields and export are documented in [Diagnostics](../diagnostics/README.md).

An optional continuity extension for Firstmate and Herdr. It observes supported
native transcripts, records context pressure and requests a safe point. `warn` is
the default; hooks never obtain authority by measuring pressure. `assisted` needs
explicit confirmation tied to the prepared package. `auto` additionally requires
durable operator authorization and a supported runtime profile.

Use the guided installer in the [repository README](../README.md). Resources are
resolved from scripts, and hooks target the installed runtime copy. The source
clone name is irrelevant. The lower-level CLI is `bin/fm-context-relay`; pass
`--home "$FIRSTMATE_HOME"` before the subcommand. `--help` on a subcommand describes
its precise arguments. Configuration and receipts belong to the private home.

## Requirements and evidence

Python 3.11+ and Bash are required for relay; Git and a compatible Firstmate
home are required for identity, and Herdr plus a supported provider are required
for live handover. Git 2.39.5 and Bash 5.2 are tested baselines, not enforced
minimums. Linux is the accepted baseline. macOS planning and settings are supported by
design but unverified; relay is unavailable because POSIX `ps` cannot verify
process identity and terminal input safety. Other platforms are unverified and
relay is unavailable. Assisted and automatic relay refuse with code
`process_evidence_unavailable`; status and the kill switch remain available.
The macOS-provided `lsof` is a possible future approach, but native acceptance
on a real Mac is required before enabling relay. Relay requires Firstmate with supported primary-scope/session-lock
libraries and the explicit `fm-lock-release.sh`/`.py` primitive. The recorded relay
baseline is Firstmate `33fc9463`, Herdr 0.9.3/protocol 22, Codex 0.166.0 or Claude
Code 2.1.316. These are prior live tests, not freshly repeated export tests.

The Codex acceptance uses TUI rollout metadata (`originator=codex-tui`,
`source=cli|vscode`, `history_mode=paginated`, `thread_source=user`) without a shared
daemon. Claude acceptance used an observed 200k window. Shared Codex daemon mode,
Claude production 1M sessions, macOS and Windows have no relay acceptance. Unknown
profiles fail closed. See [compatibility.json](../compatibility.json).

Without Firstmate, commands requiring its home refuse with a diagnostic. Without
Herdr, relay launch refuses; no alternate runtime is started. Planning and offline
transcript measurement remain independent. Doctor checks prerequisites and queries tool version flags without starting
terminal sessions or model inference.

## Hooks, trust and removal

The low-level commands allow previewing and installing hooks separately:

```sh
context-relay/bin/fm-context-relay --home "$FIRSTMATE_HOME" install --harness codex --dry-run
context-relay/bin/fm-context-relay --home "$FIRSTMATE_HOME" install --harness codex
context-relay/bin/fm-context-relay --home "$FIRSTMATE_HOME" uninstall --harness codex
```

Codex hooks are merged into `hooks.json` inside `CODEX_HOME` (by default the
`.codex` directory within your user directory);
`--codex-home` explicitly overrides that location. Code 3 means **prepared,
activation unverified**, not active. In the trusted home, review pending entries
in `/hooks`, checking executable, home, event and timeout. Verify positive primary
injection and worker exclusion. Preparation writes no trust, permissions or
`config.toml`, and launches no Codex session.

Claude requires hooks 2.1.312+; injection was previously measured on 2.1.314 and
relay on 2.1.316. Its entries merge into the home untracked
`.claude/settings.local.json`. Unrelated hooks and permissions are preserved.
Tracked, malformed or redirected settings are rejected. The optional low-level
`install --assisted` has a separate preview, stable runtime and byte-exact backup
requirement; review `install --help`. It changes mode to assisted, never auto, and
never starts a handover. Use `rollback BACKUP` for that installer transaction.

Uninstall removes owned hook commands only. Configuration, session records and
history remain. The guided uninstaller also restores backed-up planning skills.
A skill appearing on disk does not establish native discovery or active trust.

## Measurement and configuration

```sh
context-relay/bin/fm-context-relay --home "$FIRSTMATE_HOME" show --json
context-relay/bin/fm-context-relay --home "$FIRSTMATE_HOME" validate --json
context-relay/bin/fm-context-relay measure --harness codex "$TRANSCRIPT" --json
```

The example configuration is installed only if absent. Unlisted models keep
`known_model=false`, without inferred token limits; their automatic measurement
admission uses current harness-observed evidence, as specified in [SAFETY.md](SAFETY.md). The named profiles in the example are
explicit examples, not forced model preferences. The data-only `model-catalog.json`
contains optional per-model pressure overrides; an explicit home `models` table
still replaces it in full. Observed windows take precedence
over assumptions. Invalid configuration is reported; it must never authorize relay.

Claude pressure uses input plus cache-read/cache-write from the last actual call,
deduplicated by message ID. Codex input includes cache, so cache is not added again.
Compactions use supported boundary IDs/window metadata. Missing, zero, stale or
unsupported measurements remain unknown. Warnings are deduplicated; Stop never
blocks or requests an artificial continuation. Worker/non-owner hooks remain inert.
PreCompact may preserve a durable checkpoint; it grants no handover authority.

`set KEY VALUE --reason TEXT --approved TEXT` changes a reviewed setting atomically
and records the approval. Agents must first obtain the operator explicit approval;
text in a CLI argument is an audit record, not proof of independent authorization.
`set relay_mode auto` is refused: use the authorization protocol below.

The legacy `umbral` threshold interface accepts explicit token, percentage and
compaction policies (including Spanish operator input). For example,
`umbral set 'codex: compactaciones:4'` sets a Codex compaction policy. `umbral show`,
`reset` and `migrate` distinguish file policy from observed context. Percentage
windows must be observed or supported; changing thresholds does not change mode.

## Assisted and automatic handover

`relay start --help` describes the guided assisted protocol. The outgoing primary
must be at a safe point: reconcile owned operations, stop new dispatch, preserve
pending work, decisions, instructions, dependencies, blockers, uncertain operations,
notices and relative references. Preparation hashes the package and evidence.
Confirmation binds to that hash; preparation alone never authorizes retirement.

The outgoing primary invokes supported lock release under its own authority. The
successor starts fresh, acquires its own native identity and lock, and accepts the
package exactly once. Receipt and attempt identities must match. Ambiguous launch,
uncertain external effects, stale evidence or new user input preserve recoverable
state. No SID, lock owner or process identity is forged. Pane finalization requires
successor readiness and proof that the old panel contains only the dedicated empty
parked shell. Insufficient evidence preserves the panel.

Automatic relay starts OFF. After reviewing the installation and compatibility,
the operator can explicitly authorize it:

```sh
context-relay/bin/fm-context-relay --home "$FIRSTMATE_HOME" auto authorize \
  --captain-words 'I authorize automatic context relay for this home' \
  --reason 'Reviewed runtime and safe-point policy'
context-relay/bin/fm-context-relay --home "$FIRSTMATE_HOME" auto status
context-relay/bin/fm-context-relay --home "$FIRSTMATE_HOME" auto off --mode warn
```

The [authorization binding](SAFETY.md#authorization-binding) excludes pure extension
data while retaining executable hashes, a protocol version, and the existing
Firstmate HEAD and primitive hashes. Legacy authority is refused rather than migrated;
each new grant still requires the operator actual words. Status and notices identify
the changed component. Measurement admission is separate from native acceptance.

Automatic safe points inherit confirmed agentless tasks unresolved under the [safe-point contract](SAFETY.md#automatic-safe-point).
Authorization does not relax safe-point, runtime, native ownership, launch-once,
acceptance or panel-close guards. The kill switch revokes the current generation
immediately and cannot be overwritten by an in-flight old grant. It never reclaims
or retires a lock itself. Recovery is explicit; no blind retry after uncertain launch.

Notifications use user-configurable argv commands. Both interfaces default to
empty lists, so nothing is sent. A configured structured adapter must acknowledge
its arm/failure protocol before dependent retirement can proceed. The distribution
includes no personal delivery service, credentials, remote routing or lab drivers.

## Local checks

From the repository root run `FIRSTMATE_ROOT="$FIRSTMATE_HOME" python3 -B tests/run.py`.
The public relay suite uses disposable homes, synthetic transcripts, fake providers
and mocked terminal calls. Tests do not copy login material or accept permissions.
No live lifecycle test is part of the distribution suite. Fresh clean-room checks
validate installation and diagnostics, not new live handover acceptance.
