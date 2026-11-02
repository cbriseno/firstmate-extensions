---
name: preparing-assisted-relay
description: >-
  Prepares a safe point and recovers unfinished work during an assisted or durably authorized automatic Firstmate
  primary handoff. Use when a measured relay request is pending or the operator
  explicitly requests a same-harness takeover (including saying "relevo" in chat),
  or recovery of an existing attempt. Automatic handoff requires the runtime's
  recorded standing captain authorization. Not for enabling automatic mode,
  memory curation, or changing context thresholds.
---

# Preparing an assisted relay

## Purpose

Leave a verifiable package that a new primary can use to continue pending work once the operator, or the system under recorded standing authority, confirms the takeover.
The operator instructions take priority; keep the supported lock, startup and terminal safety checks.

## Requires

Use the installed `fm-context-relay` CLI and read `relay --help` for the exact commands and input formats.
The current path supports Linux, local Herdr, and a fresh session of the same Codex or Claude configuration.
`relay start --help` owns the single-entry protocol, including parking an original shell that did not use the wrapper.
Installation and activation are separate operator actions; this skill does not change policy, trust, hooks, or configuration.

## Steps

1. Read the pending request and current ownership using the CLI.
   In assisted mode, a pending policy notice alone does not authorize starting the flow; call `relay start` only after the operator requests the relay or its preview.
   For a measured automatic notice, verify that `auto show` reports current standing authority, then follow the automatic branch in `relay start --help` without asking the captain again.
   For an existing attempt, read its `status` and `events.jsonl` before continuing.
   A missing or ambiguous record is a stop condition, not permission to prepare another attempt.
2. Stop accepting substantive new work and reconcile your own writes or external operations in progress.
   Workers continue independently during assisted preparation.
   Automatic preparation waits for each live worker's durable pause acknowledgement using the exact safe-point key in the notice; never interrupt a critical operation or infer a pause from silence.
   Keep pending captain decisions, uncertain outcomes, consumed notices needing follow-up, and memory proposals as unresolved items.
   Never turn a checkpoint into approval of `/stow` or durable-memory changes.
3. Write the operational notes required by `relay start --help` to `state/context-relay/safe-point.json`.
   Include the exact pending work and its completion check, recent instructions, decisions, dependencies, blockers, notices and references to current briefs and plan reviews.
   Describe the safe point concretely; do not claim an external operation finished unless its result is known.
   Bind the notes to your current native `owner_sid` and `owner_pid`; do not reuse another session's safe point.
4. In assisted mode, run `relay start` from the outgoing owner.
   Present its short summary, pending work, unresolved items, preserved state, new pane, retiring pane, and package hash to the captain.
   Keep the barrier active and wait for the captain's explicit confirmation of that displayed package.
   Saying "relevo" requests this summary; it does not authorize retirement before seeing it.
   If the captain declines, use `relay start --abort`; the owner, context and package are preserved.
5. In assisted mode, after that one confirmation, use the same `relay start` entry with the hash and the captain's literal confirmation as documented by its help.
   In automatic mode, save concrete useful-work and safe-point facts as required by the CLI and run its automatic entry once; the system creates the hash-bound confirmation from standing authority.
   Never invent confirmation text, ignore a guard, or replace an expired or revoked authorization yourself.
   End your turn without further fleet operations so the driver can observe a fresh Stop and send native `/exit`.
   The driver coordinates exit, shell parking, fresh launch and package delivery; the captain need not chain commands.
   Never manually repeat a driver submission, answer startup permission/trust dialogs, resume a retired session, or claim completion from submission alone.
6. As the successor, read the package, the notes, the startup digest and the current durable state before dispatching.
   Compare the observed queue point, status changes, unhandled steering, approvals and uncertain operations with the capture.
   Run `relay accept` with your own native identity and a concrete review of recovery.
   Preserve inherited blockers; receipt text must never manufacture an approval or overwrite an uncertain outcome.
7. Continue the pending work only after durable acceptance and verified ownership.
   Reconcile status written during the gap and let the successor's normal watcher re-ring pending steering.
   Do not dispatch the same work again merely because a transport reply was lost.
   Complete the package's `pane-close` steps after inspecting the retired terminal, without asking for another captain confirmation.
   Your ready confirmation certifies your own terminal inspection; the finalizer owns closing that exact pane.

## Failure handling

Before retirement, a failed preparation, missing confirmation or rejected launch precondition leaves the outgoing owner in control.
Use `relay start --abort` while its supported pre-retirement conditions hold; low-level attempts retain `relay abort` for recovery.
After retirement or an uncertain submission, preserve the attempt and inspect the current owner and receipts.
Never delete a lock, copy the old SID or receipt, repeat startup to reclaim authority, relaunch on timeout, or invent acceptance.
The supported `relay recover` path may create one fresh same-harness successor after proving the home free; automatic mode may use that path once, with a durable failure notice.
It never revives the outgoing session; a live or ambiguous session blocks recovery.
If recovery remains blocked, report the exact attempt, failed check, preserved authority and required operator action.
Do not promise automatic rollback or continuity through exhausted quota.

## Output

Report the package path and hash, confirmation state, actual outgoing retirement result, successor identity and recovery receipt, pending blockers, and terminal-close result.
Keep code completion, native acceptance, integration and installed activation separate.
