# Firstmate Extensions 0.1.0

Optional extensions for [Firstmate](https://github.com/cbriseno/firstmate): context
relay and scoped Spec Kit planning. Cloning this repository activates nothing.
Firstmate remains usable without these extensions.

Context relay measures context pressure, warns, supports assisted handover, and
can relay automatically **only after explicit authorization**. Automatic relay is
**OFF by default** (`relay_mode=warn`), with a durable authorization record and an
immediate kill switch. [Observed measurement admission](context-relay/SAFETY.md)
does not require a model catalog entry and does not establish new native acceptance.
Pure extension data updates preserve authority; executable/protocol changes and
Firstmate revision changes still invalidate it. Planning defaults to `native`; Spec Kit can be selected per
installation, Firstmate home, project, or task. It ships a restricted offline core,
not the complete upstream CLI.

## Requirements and compatibility

| Component | Requirement / tested combination |
| --- | --- |
| OS | Linux is the accepted baseline. On macOS, planning and settings are supported by design but unverified; relay is unavailable because POSIX `ps` cannot verify process identity and terminal input safety. Other platforms are unverified and relay is unavailable. |
| Python | 3.11+; distribution checks use 3.11.2, standard library only. |
| Git | 2.39.5 tested; required for project identity, hooks and clean clones. |
| Bash | 5.2 tested; relay entrypoints also use coreutils (`dirname`, `readlink`). |
| Firstmate | Relay baseline `33fc9463`; requires `bin/fm-lock-release.sh` and `.py`, session-lock and primary-scope libraries. No semantic release is declared by that baseline. |
| Herdr | Relay baseline 0.9.3, protocol 22, Linux. |
| Codex | Relay baseline 0.166.0, TUI paginated rollout; shared daemon mode has no acceptance. |
| Claude Code | Relay baseline 2.1.316, observed 200k window; hooks require 2.1.312+. Production 1M sessions have no acceptance. |
| Spec Kit | Restricted static subset 1.2.4 at `43486672fe826d230d884815be7cc2026469a6ba`. No `uv`, `specify-cli` or runtime downloads. |

The relay combinations above were tested before this distribution export. This
release repeats local regression and clean-clone installation checks; it does not
repeat live model handover or certify native activation on your machine.
[compatibility.json](compatibility.json) separates those evidence levels. Provider
login, folder trust, hook trust and native skill discovery remain the user's steps.

Without Firstmate you can install planning skills and inspect installation-wide
status. Home/project operations require an explicit home configuration. Relay refuses a
missing or incompatible Firstmate home with a diagnostic. Without Herdr, relay launch is unavailable; no
alternate terminal is started. `doctor --require-relay` reports this as a failure.
Installing hooks is preparation, not permission to run them or verified activation.

### Prerequisites on a managed macOS account

Use only tools already approved and present on the machine; this repository
installs no system packages. Keep the checkout, Firstmate home and writable XDG
directories inside your home. macOS provides `/bin/bash` and `ps`; Git and Python
3.11+ are not guaranteed. Apple command-line developer tools may prompt for an
administrator. If an approved tool is missing, ask the image administrator to
provide it; do not install or update it yourself under a managed policy.

| Tool | Required for | If missing |
| --- | --- | --- |
| Python 3.11+ standard library | All extension commands | Commands cannot run. |
| Bash | Relay entrypoints and project verification | Installation-wide planning remains available. |
| Git | Cloning, project identity and relay | Existing checkout and installation-wide planning remain usable. |
| `ps` | Process inspection outside Linux | Affected relay operations refuse when identity is unknown. |
| Compatible Firstmate | Home/project operations and relay | Installation-wide planning and offline measurement remain available. |
| Herdr and a supported provider | Live relay and native hooks | Planning and offline inspection remain available. |
| bubblewrap and `flock` | Optional Linux clean-room checks only | Use the smoke steps below; no native macOS clean-room is claimed. |

No additional Python or Node packages are required. Tool versions listed in the
compatibility table are tested baselines unless explicitly marked as minimums.

## Guided installation

Clone the repository into any directory, including a name containing spaces. Keep
the clone until installation finishes. From its root, one command installs a
private runtime copy, reversible planning skills for both harnesses, and hooks for
the selected harness in a compatible Firstmate home:

```sh
bin/fm-extension install --home "$FIRSTMATE_HOME" --harness codex
bin/fm-extension doctor --home "$FIRSTMATE_HOME" --require-relay
bin/fm-extension planning status --home "$FIRSTMATE_HOME"
```

`FIRSTMATE_HOME` is the path to your compatible Firstmate checkout. Choose
`--harness claude` instead for Claude Code. To install planning skills without a
Firstmate home, omit `--home`. The installer invokes its doctor automatically; the
explicit doctor command above requires all relay prerequisites. Doctor verifies vendor hashes and installed runtime integrity and queries tool version flags; it never starts a terminal session or a model.
Use `--language en|es` to select the installation planning prose language;
English is the default. Set `FM_PLANNING_LANGUAGE` for a per-command override.
Machine protocol keys and diagnostics remain English.

The runtime lives under `$XDG_DATA_HOME/firstmate-extensions/runtime` (default:
the `firstmate-extensions/runtime` subdirectory of the local user data directory). Installed skills contain their own
runtime. Scripts resolve resources from their own locations; no checkout basename
is required. A completed installation survives renaming or removing the clone.
The installer refuses modified runtime/skill files and an unreviewed release or
home switch. Uninstall the previous release before installing a new one. It backs
up existing skills and restores them on removal.

For Codex, review pending handlers in `/hooks` inside the trusted Firstmate home.
Preparation can return code 3 in the lower-level relay CLI; the guided installer
accepts that state and reports activation as unverified. Check one positive primary
hook injection and worker exclusion before considering hooks active. Restart the
harness to refresh native skill discovery. No trust or permissions are auto-accepted.

## Quick start

```sh
# Inspect installation-wide planning; prefer native unless a task needs Spec Kit.
bin/fm-extension planning status
bin/fm-extension planning set --scope installation --mode speckit --language en
bin/fm-extension planning set --scope home --home "$FIRSTMATE_HOME" --mode native
bin/fm-extension planning set --scope project --home "$FIRSTMATE_HOME" \
  --project "$PROJECT" --mode speckit
bin/fm-extension planning status --home "$FIRSTMATE_HOME" --project "$PROJECT"

# Preview first preparation. Review the policy and use the returned digest to apply.
bin/fm-extension speckit prepare --home "$FIRSTMATE_HOME" --project "$PROJECT" --dry-run
# After applying with the returned preview digest and your authorized delivery mode:
bin/fm-extension speckit verify --home "$FIRSTMATE_HOME" --project "$PROJECT"

# Inspect relay; these commands start no relay.
bin/fm-extension relay status --home "$FIRSTMATE_HOME"
context-relay/bin/fm-context-relay --home "$FIRSTMATE_HOME" show --json

# Immediate automatic-relay kill switch, reverting to warning mode:
context-relay/bin/fm-context-relay --home "$FIRSTMATE_HOME" auto off --mode warn
```

The assisted and automatic protocols require safe points, native identities,
receipts and explicit authorization. See [Context relay](context-relay/README.md)
before enabling either mode. Notifications are a generic, user-configurable
command interface; the default sends nothing. These extensions never grant merge,
publication, workflow, account or project-write authority.

## Uninstall and verification

From the source clone or installed runtime, run:

```sh
bin/fm-extension uninstall
```

This removes owned hooks, restores backed-up skills and removes the managed runtime.
Private preferences, relay history, project preparation receipts and authored specs
remain. Spec Kit rollback is separate from disabling planning or uninstalling; see
[Planning](planning/README.md). Modified owned files stop removal for inspection.

Run the local fixture suites with a compatible Firstmate source available:

```sh
FIRSTMATE_ROOT="$FIRSTMATE_HOME" python3 -B tests/run.py
```

Tests use synthetic provider data and temporary homes; no live lifecycle drivers,
credentials or model calls are included. [Clean-room checks](clean-room/README.md)
exercise a real fresh clone, installation, status, scopes, doctor, uninstall and a
second clone with a different name and spaces. The pair scenario
`extension-with-firstmate.json` requires a separately supplied Firstmate bare repo.
These checks do not establish native trust, model quality or installed activation.

## macOS check

From the existing checkout, run one command:

```sh
bin/fm-extension doctor
```

The doctor prints required and optional tools, their versions when available,
missing tools, and the resulting limitations. It queries version flags but does
not start sessions or model inference. Share its output as the environment
inventory. This does not run unit tests or establish native acceptance. macOS
acceptance has not been recorded. POSIX `ps` does not supply every identity,
working-directory, executable or terminal-input proof required by relay safety
checks; assisted and automatic relay refuse with code `process_evidence_unavailable`. A possible future approach is the macOS-provided `lsof`; it requires acceptance on a real Mac before relay can be enabled.

## License and attribution

This repository is MIT licensed; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
Spec Kit's original MIT license, copyright GitHub, Inc., is retained verbatim in
[vendor/spec-kit/1.2.4/LICENSE](vendor/spec-kit/1.2.4/LICENSE). The pinned manifest
records upstream paths, hashes and adaptations. Vendor verification checks all
shipped bytes, the license hash, helper hashes and aggregate pin before preparation.
See [CHANGELOG.md](CHANGELOG.md) for release history.
