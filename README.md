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

Relay-lite supports Claude on Linux with Herdr and a compatible Firstmate home. Installation copies the relay but does not configure hooks or enable it. To use it, configure Claude Stop and PostToolUse hooks to invoke the installed `context-relay-lite/relay-hook` with your Firstmate home as its argument. The hook requires Firstmate's primary-scope and session-lock libraries. Set `config/relay-lite.json` in that home to `{"enabled": true}` to opt in; remove it or set `enabled` to `false` to stop further relay operations. The default threshold is 400000 resident tokens. Optional `threshold_tokens`, `stow_timeout_seconds`, `startup_timeout_seconds`, and `exit_timeout_seconds` settings are documented in `relay.py`.

For measurement only, supply a Claude hook payload on stdin to `context-relay-lite/relay.py --home "$FIRSTMATE_HOME" --dry-run`. This does not launch a relay. The live handover needs the local Herdr session and a matching primary Claude session. Errors stop the controller and are recorded in the home under `state/relay-lite/controller.log`; inspect them before acting. Installation does not establish hook trust or native activation.

## License

MIT; see [LICENSE](LICENSE), [NOTICE](NOTICE), and [Spec Kit's license](vendor/spec-kit/1.2.4/LICENSE).
