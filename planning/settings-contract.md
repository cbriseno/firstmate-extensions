# Extension settings contract v1

The JSON-only entry point is `bin/fm-extension settings`.
Planning commands remain compatible; UI clients should use this contract for revision checks and receipts.
All commands require `--home HOME`; `--project ROOT` and `--task-id ID` select additional identities.
Installation state follows `XDG_CONFIG_HOME` (otherwise the user's config directory).

## Reads

`settings status --home HOME --project ROOT --task-id ID` returns `schema: "fm-extension-settings.v1"`, `ok`, `revision`, `identity`, and `planning`.
`identity` contains installation, canonical home, canonical project, and task ID (null when unselected).
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
  "planning": {"scopes": {
    "installation": {"identity": "CONFIG_ROOT/fm-planning", "mode": "native"},
    "home": {"identity": "HOME", "mode": "inherit"},
    "project": {"identity": "ROOT", "project_id": null, "mode": "inherit", "installation_mode": "inherit"},
    "task": {"identity": {"home": "HOME", "project": "ROOT", "task_id": "TASK"}, "mode": "native"}
  }, "resolution": {"mode": "native", "effective": "native", "origin": "task", "availability": "not-required"}}
}
```

`resolution` is null without a selected project; otherwise it is the existing planning resolver object (mode, effective mode, origin, layers, availability and frozen brief declarations).

## Writes

Every `settings set` and `settings undo` requires `--request-id ID --expected-revision REV`.
Use the opaque revision from status with the same home/project/task selectors.
Request IDs are installation-wide owner-supplied strings, unique per intended operation.
An identical retry returns the original receipt without another write, even if the revision has since changed.
Reusing an ID with different arguments rejects with `request_id_conflict`.

```sh
fm-extension settings set --home HOME --project ROOT --task-id TASK --target planning --scope task --mode speckit --request-id owner-1 --expected-revision REV
fm-extension settings set --home HOME --target planning --scope installation --mode native --request-id owner-2 --expected-revision REV
fm-extension settings undo --home HOME --undo-token TOKEN --request-id owner-7 --expected-revision REV
```

Planning set accepts all four scopes and `native|speckit|inherit`; task scope requires project and task ID, project scope requires project.
Task preferences bind the task ID to its canonical project within the selected home and apply when resolving with `planning resolve --task-id ID`.
They never rewrite a frozen brief.

## Receipts and failures

Receipts contain `schema: "fm-extension-settings-receipt.v1"`, `request_id`, `state: "applied"|"rejected"`, `code`, `revision`, `previous_revision`, `identity`, and `undo_token` (nullable).
An applied receipt can include the underlying operation's `result`; a rejection includes `message`.
Exit 0 means applied/read success, 1 means a rejected operation or invalid configuration, 2 means invalid command syntax, and 4 means `stale_revision`.
Other stable write codes: `applied`, `invalid_request`, `request_id_conflict`, `invalid_configuration`, `write_failed`, `busy`, `undo_unavailable`, `operation_incomplete`.
Revisions cover selected planning files and a global settings generation; legacy edits also invalidate the revision.
Writes serialize with the planning lock.
Receipts are durable private installation state; a pending operation interrupted before its receipt is finalized rejects retries as `operation_incomplete` rather than repeating the write.
Transient lock/environment refusals happen before the receipt journal is available and can be retried with the same request ID.
Inspect status and reconcile that operation before submitting a new request ID.
Undo tokens are offered for planning writes, restore the prior bytes, and require the same identity and the immediately resulting revision.
They are single-use.

```json
{"schema":"fm-extension-settings-receipt.v1","request_id":"owner-1","state":"applied","code":"applied","revision":"NEW_REV","previous_revision":"REV","identity":{"installation":"CONFIG_ROOT/fm-planning","home":"HOME","project":"ROOT","task_id":"TASK"},"undo_token":"TOKEN","result":{"scope":"task","mode":"native"}}
```

On rejection, `state` becomes `rejected`, `code` identifies the refusal, `message` explains it, `undo_token` is null, and `revision` is the current revision (null when location/configuration/lock failure prevents obtaining it).
`previous_revision` echoes the owner's supplied expectation, including on a stale refusal.
