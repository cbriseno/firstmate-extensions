---
name: executing-firstmate-unit
description: >
  Adapts a Firstmate unit brief into execution and revision-linked evidence. Use only
  when explicitly assigned a Firstmate unit with a reviewed plan or this skill; not
  for general implementation, planning, or independent plan review.
---

# Execute a Firstmate unit

## Mode gate

Read the durable `planning_mode` in the assigned task brief before applying this
adapter. `native` means stop applying this skill and continue the ordinary assigned
work without invoking Spec Kit or changing the native workflow. `speckit` enables
the instructions below. A missing, conflicting, or invalid mode must be reported to
Firstmate; do not infer it from the current home setting, especially on resume.

## Purpose

Carry out the assigned unit with the context and delivery authority already in its
brief. This is an adapter for that brief, not a separate workflow or scheduler.

## Steps

1. Read the brief and resolve its repository, fixed plan commit, paths and sections.
   Load the global objective, relevant decisions and invariants, reviewed plan,
   unit scope and exclusions, dependencies, acceptance criteria and IDs, required
   verification, and delivery mode. Verify the references identify accessible
   content and the plan revision is the one assigned. Do not treat a label alone as
   a revision. Check dependency inputs and current repository state.
2. If required context, a dependency, or a material decision is missing or inconsistent,
   report the affected work and evidence to Firstmate. Continue independent authorized
   work where possible. Do not invent a decision or silently substitute another plan.
3. Perform the unit within its scope and existing delivery mode. Apply project and
   Firstmate instructions. Verify the criteria with the assigned checks, and record
   observable evidence and any limits. Do not maintain `tasks.md` as a second board.
4. When new authorized requirements or a revised plan arrive, assess which work and
   criteria they affect. Continue useful work within authority and escalate only the
   affected material decision outside it. Identify the revision received and do not
   silently count evidence from an earlier revision against the new one.

## Rules

The brief, current human instructions, project rules, and selected delivery mode
retain their authority. Do not add a scheduler, review, TDD, or shipping process.
Use no-mistakes only when selected and explicitly authorized under applicable rules;
never infer authorization from this skill. Do not invoke Spec Kit `implement`, its
automatic workflow or handoffs to expand the unit. No installation or initialization
is part of this adapter.

## Output

Return the unit ID and plan repository, commit and paths; commits produced; each
acceptance criterion and its verifiable evidence; checks, results and limitations;
deviations and new decisions; external operations with uncertain results; integration
state and next action. Distinguish completed work from integration or activation.
