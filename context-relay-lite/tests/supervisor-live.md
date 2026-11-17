# Supervisor relay live acceptance

## Successful acceptance, 2026-11-17

The current `main` implementation (including the bounded launch lock and process-identity fixes) completed a real relay in a disposable named Herdr laboratory, using Claude Code 2.1.326, Opus 5.5 with low effort, in automatic permission mode.
The successor read the handoff, retained its pending laboratory item and local-only notification policy, started host vigilance and successfully ran `relay --ready` with that actual PID.
The original command first retained the outgoing pane and vigilance while the successor started; resuming its exact `--watch-supervisor` command then verified both process identities, stopped only the old vigilance, recorded the relay and closed only the outgoing pane.
Host inspection confirmed the old vigilance absent and the successor vigilance alive; the isolated pane list contained only the successor.
The start mark and launch record were removed after completion.
The laboratory and its vigilance were cleaned up, and `fm-herdr-lab.sh` teardown passed its unchanged-default-session tripwire.

All laboratory Herdr calls, including those made by `relay`, went through `fm-herdr-lab.sh` with an explicit named session; a task-local PATH adapter routed the executable's calls through that helper.
No production supervisor or primary was relayed.
This acceptance does not establish installed runtime or permanent hook activation, nor does it validate Codex permissions.
Private evidence: `data/relevo-supervisor-instalar-20261117/lab-{outgoing,successor,readiness,after,completion,teardown}.log` in the owning Firstmate home.

## Earlier attempts and their limits

Observed on Linux, 2026-11-16, with Herdr 0.9.5/protocol 22 and Claude Code 2.1.325, Opus 5.5 effort low.
A disposable named Herdr session was provisioned, operated and removed through Firstmate's `fm-herdr-lab.sh`; its default-session tripwire passed after teardown.
No real supervisor was restarted or closed.

The live invocation was `relay --supervisor "$LAB_SUPERVISOR_HOME" claude - low "$HANDOFF" "$OLD_VIGILANCE_PID"`.
The successor read the handoff, preserved the pending laboratory item and started and verified its background vigilance.
The outgoing supervisor remained open while the successor's trust dialog was handled.
Claude automatic mode then rejected `relay --ready "$LAB_SUPERVISOR_HOME" "$START_FILE" "$NEW_VIGILANCE_PID"`:

```text
1 action blocked by auto mode
Auto mode: [Undermining Oversight]
```

Confirmation, old-vigilance shutdown and outgoing-pane closure are therefore still pending live acceptance in this mode.
No permission exception or workaround was applied.
Firstmate explicitly deferred this acceptance path and requested the local branch with this limitation retained.
The private task evidence is `data/relevo-supervisor-auto-20261116/claude-auto-denial.log` and `live-4.log` in the owning Firstmate home.

A separate Codex 0.169.0 laboratory attempt exposed a sandbox PID (`3`) that the outgoing host could not verify; it did not complete the relay.
The launch instruction now explicitly requires host-context vigilance and readiness, matching the primary relay's existing host requirement; this correction still needs live acceptance.
Both laboratories were removed and their default-session tripwires passed.
The executable regression `TMPDIR="$TASK_TMPDIR" python3 context-relay-lite/tests/relay.test.py` covers threshold selection, disabled supervisor defaults, retaining the old vigilance without valid confirmation, and closing only after a distinct live successor vigilance.
That deterministic coverage does not establish live acceptance of a harness permission mode or installed activation.
