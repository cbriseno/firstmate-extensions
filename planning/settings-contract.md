# Extension settings contract v1

The JSON-only entry point is `bin/fm-extension settings`.
Existing planning and context-relay commands remain compatible; UI clients should use this contract for revision checks and receipts.
All commands require `--home HOME`; `--project ROOT` and `--task-id ID` select additional identities.
Installation state follows `XDG_CONFIG_HOME` (otherwise the user's config directory).

## Reads

`settings status --home HOME --project ROOT --task-id ID` returns `schema: "fm-extension-settings.v1"`, `ok`, `revision`, `identity`, `relay`, and `planning`.
`identity` contains installation, canonical home, canonical project, and task ID (null when unselected).
`relay` contains `mode`, `automatic_enabled`, `thresholds` keyed by `claude` and `codex` (each with `text`, `parsed`, `source`), and configuration errors.
Threshold text uses the existing threshold syntax: `umbral:600K`, `umbral:80%`, `compactaciones:5`, with `o` for OR and whitespace for AND.
`planning.scopes` contains installation, home, project, and task, each with `identity` and `mode` (`native`, `speckit`, or `inherit`; null for an unselected scope).
Project preferences are home-local; an installation project override remains visible as `installation_mode` on the project row.
`planning.resolution` is the existing resolver result; an existing durable brief remains authoritative on resume.
Reads do not create files or locks.

The following JSON fragments define the field shapes; `REV` is an opaque string and optional selectors use JSON null.

```json
{
  "schema": "fm-extension-settings.v1",
  "ok": true,
  "revision": "REV",
  "identity": {"installation": "CONFIG_ROOT/fm-planning", "home": "HOME", "project": "ROOT", "task_id": "TASK"},
  "relay": {"mode": "warn", "automatic_enabled": false, "errors": [], "thresholds": {
    "codex": {"text": "compactaciones:5 o umbral:600K", "parsed": {"compactions": 5, "min_context_tokens": 600000, "operator": "or"}, "source": "umbrales-relevo: codex"},
    "claude": {"text": "umbral:80%", "parsed": {"min_context_percent": 80}, "source": "umbrales-relevo: claude"}
  }},
  "planning": {"scopes": {
    "installation": {"identity": "CONFIG_ROOT/fm-planning", "mode": "native"},
    "home": {"identity": "HOME", "mode": "inherit"},
    "project": {"identity": "ROOT", "project_id": null, "mode": "inherit", "installation_mode": "inherit"},
    "task": {"identity": {"home": "HOME", "project": "ROOT", "task_id": "TASK"}, "mode": "native"}
  }, "resolution": {"mode": "native", "effective": "native", "origin": "task", "availability": "not-required"}}
}
```

`resolution` is null without a selected project; otherwise it is the existing planning resolver object (mode, effective mode, origin, layers, availability and frozen brief declarations).

`settings preflight --home HOME --harness HARNESS [--codex-home DIR]` returns `schema: "fm-extension-settings-preflight.v1"`, `eligible`, `can_authorize`, `reasons`, and `authorization_reasons`.
The relay CLI also exposes the same read as `fm-context-relay --home HOME auto preflight --harness HARNESS [--codex-home DIR]`.
`eligible` requires a current standing authorization and prerequisites; `can_authorize` requires prerequisites only, because the explicit authorize action records a new grant.
Each reason is an object with stable `code` and explanatory `message`.
Codes are `unsupported_platform`, `unsupported_harness`, `missing_hooks`, `invalid_hooks`, `codex_trust_unverified`, `invalid_configuration`, `missing_standing_authorization`, and `invalid_standing_authorization`.
This settings preflight does not prove a session can retire; the existing safe-point and launch checks still run at handoff time.
Codex preparation cannot prove native trust, so preflight conservatively reports `codex_trust_unverified` even when hook entries are present.
Authorize can record an explicit grant independently of operational readiness; no preflight result grants authority or bypasses the existing runtime checks.

## Writes

Every `settings set`, `settings authorize`, `settings off`, and `settings undo` requires `--request-id ID --expected-revision REV`.
Use the opaque revision from status with the same home/project/task selectors.
Request IDs are installation-wide owner-supplied strings, unique per intended operation.
An identical retry returns the original receipt without another write, even if the revision has since changed.
Reusing an ID with different arguments rejects with `request_id_conflict`.

```sh
fm-extension settings set --home HOME --project ROOT --task-id TASK --target planning --scope task --mode speckit --request-id owner-1 --expected-revision REV
fm-extension settings set --home HOME --target planning --scope installation --mode native --request-id owner-2 --expected-revision REV
fm-extension settings set --home HOME --target threshold --harness codex --spec 'umbral:600K o compactaciones:5' --request-id owner-3 --expected-revision REV
fm-extension settings set --home HOME --target relay-mode --mode assisted --captain-words 'Use assisted relay' --reason 'Requested mode' --request-id owner-4 --expected-revision REV
fm-extension settings authorize --home HOME --captain-words 'Enable automatic relay' --reason 'Explicit request' --request-id owner-5 --expected-revision REV
fm-extension settings off --home HOME --captain-words 'Turn automatic relay off' --reason 'Explicit request' --request-id owner-6 --expected-revision REV
fm-extension settings undo --home HOME --undo-token TOKEN --request-id owner-7 --expected-revision REV
```

Planning set accepts all four scopes and `native|speckit|inherit`; task scope requires project and task ID, project scope requires project.
Task preferences bind the task ID to its canonical project within the selected home and apply when resolving with `planning resolve --task-id ID`.
They never rewrite a frozen brief.
Relay-mode set accepts `warn|assisted`; it cannot enable automatic relay.
Only `settings authorize` calls the existing explicit `auto authorize` implementation and stores the captain's words verbatim.
Authorize and off require nonblank `--captain-words` and `--reason`; a mode change that disables automatic relay has the same requirement.
No read, threshold write, planning write, or undo grants standing authorization.
The default remains automatic OFF.

## Receipts and failures

Receipts contain `schema: "fm-extension-settings-receipt.v1"`, `request_id`, `state: "applied"|"rejected"`, `code`, `revision`, `previous_revision`, `identity`, and `undo_token` (nullable).
An applied receipt can include the underlying operation's `result`; a rejection includes `message`.
Exit 0 means applied/read success, 1 means a rejected operation or invalid configuration, 2 means invalid command syntax, and 4 means `stale_revision`.
Other stable write codes: `applied`, `invalid_request`, `request_id_conflict`, `authorization_required`, `invalid_configuration`, `write_failed`, `busy`, `undo_unavailable`, `operation_incomplete`.
Revisions cover selected scoped files, relay authority/kill state, and a global settings generation; legacy edits also invalidate the revision.
Writes serialize with the planning lock and reuse relay's own configuration lock for relay mutations.
Receipts are durable private installation state; a pending operation interrupted before its receipt is finalized rejects retries as `operation_incomplete` rather than repeating authorization.
Transient lock/environment refusals happen before the receipt journal is available and can be retried with the same request ID.
Inspect status and reconcile that operation before submitting a new request ID.
Undo tokens are offered for planning and threshold writes, restore the prior bytes, and require the same identity and the immediately resulting revision.
They are single-use; automatic authorization and revocation deliberately have no undo token.

```json
{"schema":"fm-extension-settings-receipt.v1","request_id":"owner-1","state":"applied","code":"applied","revision":"NEW_REV","previous_revision":"REV","identity":{"installation":"CONFIG_ROOT/fm-planning","home":"HOME","project":"ROOT","task_id":"TASK"},"undo_token":"TOKEN","result":{"scope":"task","mode":"native"}}
```

On rejection, `state` becomes `rejected`, `code` identifies the refusal, `message` explains it, `undo_token` is null, and `revision` is the current revision (null when location/configuration/lock failure prevents obtaining it).
`previous_revision` echoes the owner's supplied expectation, including on a stale refusal.
