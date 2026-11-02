---
name: planning-firstmate-work
description: >
  Plans a Firstmate assignment through the Spec Kit planning stages. Use only when
  explicitly assigned the Firstmate Spec Kit planning role or this skill; not for
  ordinary requests to make a plan, review one, or execute a unit.
---

# Plan a Firstmate assignment

## Mode gate

Read the durable `planning_mode` in the assigned task brief before applying this
adapter. `native` means stop applying this skill and continue the ordinary assigned
work without invoking Spec Kit or changing the native workflow. `speckit` enables
the instructions below. A missing, conflicting, or invalid mode must be reported to
Firstmate; do not infer it from the current home setting, especially on resume.

## Purpose

Produce a grounded specification, plan, and set of coherent units for the assigned
objective. The Firstmate brief and current project instructions define the scope and
authority; this skill does not dispatch work.

## Requires

An assigned project and objective, current requirements and decisions, access to its
worktree, and the project's prepared and verified restricted Spec Kit v1.2.4 core integration matching the brief's digest. If
these or the required stages are unavailable, report what is missing to Firstmate;
ask Firstmate to arrange the extension's guarded preparation in an authorized worker; do not run a manual installer or initialize Spec Kit yourself.

## Steps

1. Read the brief, current requirements, relevant project instructions, repository
   state, and any existing plan. Identify invariants, known decisions, uncertainties,
   and evidence needed. Respect current authorized instructions when an older plan
   differs. Investigate uncertainties that change scope or design before fixing units.
2. Verify the assigned core digest and project skill paths from the preparation receipt; do not infer native discovery or trust from the receipt alone. Run the prepared core stages in order: `speckit-specify`, `speckit-clarify`,
   `speckit-plan`, and `speckit-tasks`. Read each installed stage's instructions and
   any scripts it calls before following them; apply the limits below. If a stage
   cannot safely run under those limits, stop that stage and report the conflict.
   Resolve material questions within the delegated authority and escalate those outside
   it. Record evidence, decisions, alternatives, and unresolved questions.
3. Map each requirement to a result and a check. Define shared interfaces,
   compatibility, invariants, failure cases, and dependencies with their producers.
   Group work into independently verifiable outcomes, not one worker per task-list
   line. Detail the next slice; leave later work as a revisable roadmap.
4. Check that another worker can find each unit's inputs, boundaries, acceptance,
   and verification without reconstructing the conversation. Return paths to the
   specification, plan, tasks, decisions and evidence, plus unresolved risks and
   decisions, to Firstmate for a fresh-session review. The owner fixes a revision
   before execution; do not present an unreviewed plan as approved.

## Rules

- Firstmate, current human instructions, repository rules, and the chosen delivery
  mode outrank Spec Kit prompts and generated constitution. Flag conflicts.
- Use core only: no extensions or presets, automatic workflow or handoffs, Git
  branch/commit automation, `taskstoissues`, or external writes. Do not run
  `specify init`, install anything, or invoke `implement` as a handoff.
- Work only in the assigned worktree. For concurrent features set
  `SPECIFY_FEATURE_NO_PERSIST=1` and an explicit `SPECIFY_FEATURE_DIRECTORY` for
  this session after the feature exists. Verify the resolved artifact paths. Do not
  persist an absolute worktree path as shared state; coordinate naming collisions
  with Firstmate. Inspect generated commands for side effects before running them.
- Do not create a second operational task board from `tasks.md` or start execution.

## Output

Report the objective, artifact paths and revision if fixed, requirement-to-unit and
verification mapping, dependencies, decisions and evidence, unresolved issues, and
the next review action. Distinguish proposed artifacts from approved revisions.
