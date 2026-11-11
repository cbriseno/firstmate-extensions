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
4. close its own pane once the new harness is working on that instruction; otherwise stay and show the new pane's screen.

Steps 2-4 are `context-relay-lite/relay "$FIRSTMATE_HOME" <claude|codex> <model|-> <effort|->`, run from inside the primary's Herdr pane; `-` keeps the target harness's default.
A relay happens when the captain asks for one, or automatically when the context crosses the threshold: register `context-relay-lite/relay-check "$FIRSTMATE_HOME"` as a Claude or Codex Stop hook, and set `{"threshold_tokens": 300000}` in `config/relay-lite.json`.
When the latest model call reaches the threshold, the hook blocks the end of the turn and tells the primary to relay now; a missing file or `0` turns it off.
In Firstmate, the `/relay` skill handles chat requests such as “releva ahora” and “programa el relevo a 200K”: it runs `/stow` before a requested relay and changes the configured threshold when asked.
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
