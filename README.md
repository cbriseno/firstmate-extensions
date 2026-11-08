# Firstmate Extensions 0.1.0

Optional extensions for [Firstmate](https://github.com/cbriseno/firstmate): scoped Spec Kit planning and a minimal Claude context relay. Cloning this repository activates nothing.

## Installation

Python 3.11+, Git and Bash are required. Install the runtime and planning skills with:

```sh
bin/fm-extension install --home "$FIRSTMATE_HOME" --harness claude
bin/fm-extension doctor --home "$FIRSTMATE_HOME"
bin/fm-extension planning status --home "$FIRSTMATE_HOME"
```

The runtime is installed under `$XDG_DATA_HOME/firstmate-extensions/runtime` (or the default local user data directory). Planning skills are installed for both harnesses. Use `bin/fm-extension uninstall` to remove the managed runtime and restore previous skills. See [Planning](planning/README.md) and its [settings contract](planning/settings-contract.md).

## Minimal relay

Relay-lite supports Claude on Linux and macOS (not yet verified on a real Mac) with Herdr and a compatible Firstmate home. Installation copies the relay but does not configure hooks or enable it. To use it, configure Claude Stop and PostToolUse hooks to invoke the installed `context-relay-lite/relay-hook` with your Firstmate home as its argument. The hook requires Firstmate's primary-scope and session-lock libraries. Set `config/relay-lite.json` in that home to `{"enabled": true}` to opt in; remove it or set `enabled` to `false` to stop further relay operations. The default threshold is 400000 resident tokens. Optional `threshold_tokens`, `stow_timeout_seconds`, `startup_timeout_seconds`, and `exit_timeout_seconds` settings are documented in `relay.py`.

For measurement only, supply a Claude hook payload on stdin to `context-relay-lite/relay.py --home "$FIRSTMATE_HOME" --dry-run`.
This does not launch a relay.
The live handover needs the local Herdr session and a matching primary Claude session.
When the threshold is crossed but another live primary owns the home, the hook reports the conflict through Claude's `systemMessage` without replacing that owner.
The same unchanged conflict or pre-launch error is reported once per session, recorded in `state/relay-lite/controller.log`, and rearmed after recovery.
Firstmate's normal startup or Stop auto-arm can recover a dead owner's lock; the relay hook never changes lock ownership and retries automatically once ownership is restored.
See `relay.py --help` for the supported recovery command; never delete the lock or relay ticket manually.
Handover errors still stop the controller without replay and remain in `controller.log` for inspection.
Installation does not establish hook trust or native activation.

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
