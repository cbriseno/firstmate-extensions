# Anonymous extension diagnostics

`fm_diagnostics.py` is the complete standard-library module to vendor into an extension. It writes anonymous JSONL directly; unknown event names and fields are discarded. Use `emit(component, event, **fields)` or a `Logger` when component version and revision are available. `Logger` buffers 32 events and rotates while keeping the current and previous segments within a 20 MiB per-component cap.

Linux stores logs under `${XDG_STATE_HOME:-~/.local/state}/firstmate/diagnostics/<component>`; macOS uses `~/Library/Logs/Firstmate/Diagnostics/<component>`. No administrator access is needed. Windows uses `%LOCALAPPDATA%/Firstmate/Diagnostics`.

Tests may set `FM_DIAGNOSTICS_HOME` to redirect writes to a temporary directory.

Run `bin/fm-diagnostics preview [components...]` for a one-line size, time range, event count and verifier result. `export` writes one zip under `~/Downloads` (or `--downloads DIR`); `path` prints the extension log directory. Only the listed component directories and their two rotated JSONL files are read. The archive is withheld if verification fails.

| Event | Allowed fields |
| --- | --- |
| `state.change` | `from`, `to`, `actor` closed states; `fields_missing` field names; `task` salted identifier |
| `snapshot.read` | task, unresolved-parent and missing-visibility counts |
| `ui.layout-shift` | structural element name and rounded geometry |
| `anon.measure` | word count, rounded length, width ratio, font class and line counts |
| `relay.preflight` | check/result/reason closed enums and component version/revision |
| `relay.attempt` | result/reason closed enums, count and component version/revision |

State/actor values are `queued`, `working`, `paused`, `finished`, `blocked`, `unknown`, `user`, `agent`, `system` and `unknown`. Preflight checks are `model`, `window`, `herdr.session`, `tasks.paused`, `processes`, `authorization`, `config` and `code`; results are `pass`, `fail`, `skip`, `attempt` and `rejected`. Reasons are `ok`, `session_null`, `model_unknown`, `window_unknown`, `tasks_active`, `process_active`, `authorization_missing`, `config_invalid`, `code_changed`, `unsupported`, `unsupported_platform`, `unsupported_harness`, `missing_hooks`, `invalid_hooks`, `invalid_configuration`, `invalid_standing_authorization`, `codex_trust_unverified` and `error`. Unknown values are discarded.

Do not add free text, routes, exception messages, titles or content-bearing fields. Extend the schema and unique-canary tests together.
