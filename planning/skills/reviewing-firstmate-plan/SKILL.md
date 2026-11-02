---
name: reviewing-firstmate-plan
description: >
  Reviews a Firstmate Spec Kit plan against its requirements in a fresh session. Use
  only when explicitly assigned that independent plan review or this skill; not for
  ordinary code review, planning, or executing a unit.
---

# Review a Firstmate plan

## Mode gate

Read the durable `planning_mode` in the assigned task brief before applying this
adapter. `native` means stop applying this skill and continue the ordinary assigned
work without invoking Spec Kit or changing the native workflow. `speckit` enables
the instructions below. A missing, conflicting, or invalid mode must be reported to
Firstmate; do not infer it from the current home setting, especially on resume.

## Purpose

Challenge the proposed plan using its actual source requirements and repository
context, and give Firstmate actionable findings before units are assigned.

## Requires

A fresh session separate from the planner; the objective, current requirements,
decisions and invariants; accessible specification, plan and tasks at the supplied
revision; and the prepared, verified restricted Spec Kit v1.2.4 core analyze stage matching the brief's digest. If freshness,
a source, revision, or the stage cannot be verified, report the gap to Firstmate;
do not claim an independent or complete review. Do not install or initialize it.

## Steps

1. Read the assignment and source requirements independently of the plan, then inspect
   the supplied `spec.md`, `plan.md`, `tasks.md`, decisions, and relevant repository
   evidence at the identified revision. Check that the references resolve to the
   intended content; report missing or changed sources.
2. Read the installed `speckit-analyze` instructions and any scripts it calls before
   applying them. Run the core analysis only within the limits below; report a
   conflict that prevents safe analysis instead of following the conflicting step.
3. Check each item against evidence:
   - Every current requirement has a responsible unit and sufficient verification.
   - Relevant global invariants, integration, compatibility and failure cases are
     included in acceptance.
   - Each dependency has a real input and identified producer or source; there are
     no cycles or unconnected interfaces. Shared contracts are defined and reviewed.
   - The pieces form a verifiable end-to-end result, not merely separate files.
   - Each worker has accessible, current sources, scope, decisions and criteria;
     no unit depends on reconstructing the chat or settling an open architecture.
   - Assumptions are supported; omissions, contradictions, unauthorized scope cuts,
     and invented decisions are identified.
4. Return findings with severity, affected requirement and artifact location,
   evidence, impact, and a concrete correction. Identify checks with insufficient
   evidence. Firstmate coordinates correction and any bounded re-review; do not
   silently fix or approve the plan yourself.

## Rules

Firstmate and repository instructions outrank Spec Kit prompts. Use core only;
no extensions, presets, automatic workflow or handoffs, Git/branch/commit automation,
`taskstoissues`, or external writes. Do not run `specify init`, install anything, or
start implementation. For concurrent features use `SPECIFY_FEATURE_NO_PERSIST=1`
and an explicit `SPECIFY_FEATURE_DIRECTORY`; verify paths and do not persist an
absolute worktree path as shared state. Inspect scripts for side effects before use.

## Output

Identify the plan by repository, commit and paths, the sources reviewed, findings
and checklist results, limitations, and recommended next action. Say explicitly when
no findings were found without claiming checks that could not be performed.
