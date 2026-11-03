# Observed admission and standing authority

This document owns the measurement admission and authorization binding contract.
Measurement eligibility is separate from native relay acceptance and installed activation.

## Measurement admission

An unlisted model can pass automatic measurement admission with `known_model=false` and `evidence=observed`.
`known_model` means only that the effective optional model table contains an entry.
The shipped `model-catalog.json` and a home's explicit `models` table provide pressure overrides and a configured window fallback for advisory policy evaluation.
An explicit home table still replaces the shipped table in full.
Neither a catalog entry nor a configured fallback window proves automatic admission.

Automatic measurement admission requires a positive harness-observed window belonging to the current session, owning PID and normalized model, a complete current transcript scan, and a resolved harness policy.
Claude uses the owner-filtered statusLine `context_window_size`; Codex uses `token_count.info.model_context_window`, bound to the scanned rollout session and file identity.
A model change invalidates earlier observations until current evidence arrives; a missing latest Codex window or an invalid Claude statusLine observation removes that window evidence.
Observation currency means current session, owner, model and producer evidence, rather than a guessed window or an arbitrary age limit.

Any policy containing a compaction condition, including an OR policy, requires a verified current count and its reader signal.
Claude uses `compact_boundary`; Codex retains the existing verified exec or paginated TUI rollout contract described in the README.
A pure absolute-token policy such as `umbral:400000` requires the observed window and existing policy match, without a verified compaction count.
A pure percentage policy also uses the observed window.
Pressure evaluation can still report an OR branch matching when counting is unverified; that alone cannot bypass automatic admission.

| Stable refusal code | Meaning |
| --- | --- |
| `observed_window_missing` | No positive observed window; a catalog/configured value cannot replace it. |
| `observed_window_stale` | Observation does not match current session, owner, model, source or rollout identity. |
| `compaction_counting_unverified` | A counting policy lacks a verified, consistent count and signal. |
| `measurement_incomplete` | Transcript scan is incomplete or missing. |
| `policy_unresolved` | No policy resolves for this harness. |
| `unsupported_harness` | No supported measurement source exists. |

Session reports, `auto status` and notices show the window, source and counting signal used.
Passing this measurement check grants no consent or transfer authority.
Current-owner identity, same-harness runtime, hooks, supported versions, safe-point attestations, limits, throttles, kill generation, durable standing authority and audit still apply through the existing transfer engine.
The pre-retirement check uses the latest session record, rather than only the frozen preparation record.
No new native acceptance is claimed for a model merely because its measurement evidence passes.

## Authorization binding

`lib/fm_context_relay/safety_manifest.py` is the single explicit inventory and protocol owner.
Its `PROTOCOL_VERSION` integer must be bumped for every automatic transfer behavior change.
The authority stores a canonical hash of that version, the executable manifest contents and the existing Firstmate revision binding, with component hashes for diagnosis.
Every extension Python module and command wrapper remains bound; new unclassified executable files fail closed and fail the coverage regression.
Pure catalog JSON, README/docs, tests, examples, compatibility data and other extension modules' data are excluded.
Executable settings code remains bound because imported Python code can affect transfer behavior.

The Firstmate side is deliberately unchanged: its Git HEAD and the seven previously bound startup, lock, release and ownership primitives remain part of the fingerprint.
An unrelated committed Firstmate change therefore still invalidates authority.
This broader core binding covers transitive behavior that a small direct-file list would miss; narrowing it requires a separate core safety API design.
Removing the core revision here would weaken the previous guarantee.

| Manifest entry | Why it is bound |
| --- | --- |
| `fm_context_relay_main.py`, `__init__.py`, `cli.py` | Import/bootstrap and CLI dispatch of privileged operations. |
| `safety_manifest.py` | Inventory, protocol version and fingerprint comparison. |
| `common.py` | Private paths, locking, atomic state and hashing. |
| `identity.py`, `pane_close.py`, `process_inspection.py` | Native identity, ownership and exact retired-pane finalization. |
| `admission.py`, `measure.py`, `session.py` | Observed evidence, verified counting and policy evaluation. |
| `config.py`, `thresholds.py` | Validated effective configuration and harness policy resolution. |
| `hook.py` and hook/statusLine/guard wrappers | Owner-filtered evidence and the active transfer barrier. |
| `automatic.py`, `automatic_preflight.py` | Consent, ledger, limits, kill generation and safe-point/pre-retirement checks. |
| `assisted.py` | Package binding, confirmation, retirement, lock-release invocation and acceptance. |
| `start.py`, `launch_once.py`, shell wrapper | Native exit/startup coordination, generation fencing and recovery. |
| `checkpoint.py`, `events.py`, `relay_notifications.py` | Captured operational evidence, audit and subprocess dispatch during transfer. |
| `activation.py`, `install.py`, `intent.py`, `settings_preflight.py` | Executable settings/import surfaces; retained conservatively rather than treated as pure data. |
| Main command wrapper and successor pane-close template | Interpreter dispatch and instructions consumed by the transfer. |
| Firstmate HEAD | Existing coverage of transitive core behavior. |
| `fm-session-start.sh`, `fm-sessionstart-run.sh` | Existing normal-startup binding. |
| `fm-lock.sh`, `fm-lock-release.sh`, `fm-lock-release.py` | Existing acquisition/retirement primitive binding. |
| `fm-session-lock-lib.sh`, `fm-primary-scope-lib.sh` | Existing ownership and primary-scope binding. |

`auto status` and bounded notices identify `protocol_version`, `extension/<file>`, `firstmate/<primitive>` or `firstmate_revision` when the binding changes.
Missing/unclassified executable files fail closed.
Old fingerprint formats are rejected as `binding_format (legacy authorization)` and are never migrated or reused.
Every new authorization still requires the captain's verbatim words and reason through the existing audited authorizer.
Updating this extension does not authorize or start a relay.

## Automatic safe point

A fleet snapshot must identify this home, contain a valid main inventory and tasks/backlog, and have a generation timestamp no more than 60 seconds old.
Finished tasks retain their existing treatment.
Every other live worker must be paused and have a durable last status line acknowledging the exact current safe-point key at or after the request.
The existing refusal text `worker activo/unknown: esperar pausa segura` and `worker has not acknowledged this safe point` remains applicable.

Only an `unknown` task without that acknowledgement is eligible for a read-only absence probe.
Its current-state observation must be fresh and belong to the snapshot generation.
The probe validates its local endpoint through Firstmate's existing `fm_backend_validate_task_endpoint` and reuses `fm_backend_agent_state`; it never invokes work snapshot, reconciles pauses or takes the work lock.
It allows at most five seconds per task and ten seconds total per safe-point evaluation, terminating the probe process group on timeout.
Only a successful structural `missing` or `dead` verdict proves no agent exists.
A cheap `endpoint.status=absent`, failed presence query or human detail string cannot prove absence.
Errors, timeouts, unreadable, ambiguous, unverified and unexpected results remain unknown and refuse the safe point.
Other nonterminal states retain the existing pause requirement.

The captured metadata hash must match the local metadata before and after the probe, and its validated backend and target must match the fleet row.
Remote routes and changed, missing or symlinked metadata refuse the exception.
The reservation carries `parked_without_agent` into the package's hashed `notes.json`: task id, last captured status line, stable reason `parked_without_agent`, unresolved marker, structural verdict, observation time, backend, target, metadata hash and spawn generation.
The successor inherits these tasks unresolved; absence is neither completion nor permission to discard work.
The pre-retirement check repeats the probe and refuses any newly parked task or changed metadata not inherited in the sealed package.
Protocol version 2 invalidates earlier standing authority through the existing manifest binding; the Firstmate HEAD binding is unchanged.

## Verification

`tests/automatic.test.py` covers positive/negative admission, the executable inventory, component mutations, pure-data stability, legacy refusal and preserved core revision invalidation in disposable homes.
`tests/session.test.py` exercises rollout-derived current window evidence; `tests/thresholds.test.py` exercises the hook/CLI path for listed and unlisted Claude models.
These are isolated synthetic regressions, separate from the README's previously recorded native acceptance evidence.

Synthetic backend regressions in `tests/automatic.test.py` cover parked inheritance, fail-closed probe outcomes, metadata and snapshot currency, budgets and exact pause keys.
