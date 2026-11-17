# Supervisor relay

The current manual procedure is:

1. At the context threshold, finish the current operation and update the handoff with current responsibilities, pending work, notification policy and the exact command for starting vigilance.
2. Open a sibling Herdr pane in the supervisor directory and start the same harness, model and effort, instructing it to read that handoff and take over supervision.
3. Read the new pane and answer authorized startup dialogs; keep the old supervisor and its vigilance running until the successor is actually watching.
4. The successor starts its vigilance, verifies it is running, and confirms readiness to the outgoing supervisor.
5. The outgoing supervisor verifies the successor vigilance, stops its own vigilance, appends the relay to the handoff and closes only its own pane.

This procedure comes from the supervisor's existing handoff rule; it needs no Firstmate primary lock or session-start command.
Implementation reuses `relay-check` for step 1 and `relay` for steps 2-5.
The command headers own invocation details.

## Enable a supervisor

Register `relay-check --supervisor "$SUPERVISOR_HOME"` as its native Stop hook, using the supervisor cwd as the home.
The hook returns the same measured thresholds and exit semantics as the primary checker, with role `supervisor`.
Configure `thresholds.supervisor.claude` and/or `thresholds.supervisor.codex` in that home's `config/relay-lite.json`; token, percentage and exact-model limits follow the [existing configuration contract](../README.md#minimal-relay).
Missing supervisor limits are disabled, and the primary's scalar fallback never enables them.
`FM_TASK_ID` still selects worker even if `--supervisor` is passed; calls outside the declared home are skipped.
`relay-check --json --supervisor "$SUPERVISOR_HOME"` inspects the same hook payload without requesting a relay.

When the hook requests a relay, follow the manual procedure above, using `relay --supervisor` from the outgoing supervisor's pane with its current harness, model, explicit effort, updated handoff path and actual vigilance PID.
Start vigilance and run `relay --ready` in the host context outside any tool sandbox, so the outgoing supervisor can see the actual host PID.
The successor confirms only after starting its own vigilance; the outgoing command verifies that PID and its process start still match the readiness mark and differ from its own, then verifies its original vigilance PID and process start again before stopping the old vigilance and closing the old pane.
When startup needs attention, answer only authorized dialogs and resume the exact printed `--watch-supervisor` command; never launch another successor to retry.
A login or a startup blocked for 60 seconds keeps the old supervisor alive and uses the notification policy in the handoff.
This is an opt-in extension; installation alone changes neither the supervisor's files nor its hooks or running processes.

Live acceptance with Claude automatic mode passed in an isolated laboratory on 2026-11-17; see the [live results and verification limits](tests/supervisor-live.md). Installed runtime and permanent hook activation are separate checks.

The launch lock records its owner PID and process start in UTC. Waiting is bounded by `RELAY_LOCK_WAIT_SECONDS` (default 10); a timeout prints the owner and the manual removal command, to use only after verifying that owner has exited. Successful relay removes its start mark and launch record. The printed supervisor watch command carries the original vigilance start; retain that argument unchanged when resuming. Readiness wait remains 5 seconds per invocation (`RELAY_WAIT_SECONDS` can extend it); expiration keeps the old supervisor and the same successor.
