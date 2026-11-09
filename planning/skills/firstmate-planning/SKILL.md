---
name: firstmate-planning
description: Decide and delegate planning for a Firstmate-managed request, including selecting the proportionate route, commissioning a Spec Kit plan and fresh review, and handing fixed plan references to implementation units. Use when the active Firstmate orchestrator is deciding or coordinating planning for an authorized request or the captain asks to change its planning mode, only in that orchestrator's session; not for ordinary worker, reviewer, or unrelated sessions.
---

# Firstmate planning

## Purpose and boundary

Choose the planning route and coordinate its delivery for the current authorized request. This skill is local to the Firstmate orchestrator: load it only for that role and task, never as an always-on project instruction, hook, or instruction for every session. Installing this skill alone does not activate planning or prepare projects. Follow current human instructions, environment permissions, repository instructions including AGENTS.md, accepted decisions, and the existing delivery authority before any framework prompt. Use Firstmate's existing dispatch, status, and integration procedures; this skill creates no scheduler or new approval gate.

## Operator requests and CLI

Use `scripts/fm-extension` relative to this loaded skill's directory. The skill
installer includes a digest-checked runtime copy; in the source tree the launcher
finds resources from its own location. Do not assume the checkout name or cwd.
Python 3.9+, Bash for the optional prerequisite wrapper, and POSIX file locking
are required. Use `FM_HOME` or explicit `--home HOME`; identify projects through
Firstmate's current project record and pass their exact canonical root as `PROJECT`.
A name mentioned in conversation is a lookup request, never a basename match.
A missing/ambiguous registered project stops only the operation that needs it.

Translate intent into these operations; run `planning status --home HOME` first
to show all home/project exceptions and pending/failed preparation. Defaults affect
new tasks only. A preference request authorizes the scoped config change; it does
not authorize project writes, trust changes, publication, or editing instructions.

| Request (examples, not exact trigger phrases) | Commands and result |
| --- | --- |
| “Use Spec Kit in all projects” / “usa Spec Kit en todos los proyectos” | `planning set --scope installation --mode speckit`. Preserve and report every home/project exception; no manual core installation is required. |
| “Use native planning in this home” | `planning set --scope home --home HOME --mode native`. Other homes retain their preferences. |
| “Disable it in sample-deck” / “desactívalo en sample-deck” | Look up that registered project's root, then `planning set --scope project --home HOME --project PROJECT --mode native`. Report that existing files and active task modes stay. |
| “Enable it only in this project” / “actívalo solo en este proyecto” | `planning set --scope project --home HOME --project PROJECT --mode speckit`; leave other preferences intact and report other enabled exceptions. If the request explicitly means turn it off everywhere else, apply the complete disable operation below first. |
| “Let this project inherit again” | `planning clear --scope project --home HOME --project PROJECT`; this can enable Spec Kit when inherited, so do not describe clear as disable. |
| “Disable Spec Kit everywhere” | Preview `planning set --scope installation --mode native --clear-project-overrides --dry-run`, show its full inventory, then run the same command without `--dry-run`. This clears registered project exceptions across homes. For every explicit home default still shown as speckit, run `planning clear --scope home --home HOME`; report unknown/unregistered homes as outside this inventory. |
| “Disable it throughout this home” | Preview then apply `planning set --scope home --home HOME --mode native --clear-project-overrides`. Other homes stay independent. |
| “Use Spec Kit for this task” | Pass `--task-mode speckit` to resolution and freeze it in the durable brief; do not change defaults. |

Equivalent paraphrases have the same scope. “All” without a narrower home means
the installation's registered inventory; report remaining exceptions explicitly.
Use `planning clear --scope home` or `--scope installation` to restore inheritance.
Use `planning relocate --id ID --project NEW_ROOT` only after verifying the moved
root; a new clone/fork/worktree is a different private identity. No remote URL is used.

## Planning mode (resolve before choosing a route)

For a new task run `planning resolve --home HOME --project PROJECT`, adding
`--task-mode native|speckit` only for an explicit task request. Precedence is task,
project, home, installation, then built-in native. Copy `brief_lines` to the durable
brief: mode, origin, resolver version, language and core version/digest when relevant.
Private assigned project IDs are bound to canonical roots and filesystem identity;
a move requires verified relocation. Neither basename nor remote URL identifies a project.

On resume, use the existing brief (`--brief BRIEF`) and preserve its original origin,
resolver, language and core pin; do not overwrite its provenance with a new resolution.
Defaults never change an active task. Invalid/unreadable/conflicting config, brief,
project policy or pin stops the dependent cycle with the CLI's concrete repair message.
Never silently downgrade to native. Read AGENTS.md, CLAUDE.md and existing constitution;
their policy remains a restriction even when a config override requests otherwise.
The CLI enforces structured `planning_policy: native|speckit|inherit` declarations and
surfaces arbitrary prose for review; it cannot interpret all organizational policies.

For native, stop applying the planning adapters after recording the mode and preserve
ordinary Firstmate behavior. For speckit, apply the proportional route below: a trivial
change or small bounded bug still uses its direct/diagnostic route. Pending core does
not turn such a task into a full Spec Kit cycle. Before a route requiring core, resolve
with `--require-core` and follow the preparation procedure if it reports pending/failed.

## Prepare only a dependent project

First-enable preparation belongs to an authorized worker in its assigned checkout
and selected delivery mode, never a direct write by the primary into a third-party
project. Give that worker the extension launcher path and pin from resolution.

1. Run `speckit prepare --home HOME --project PROJECT --dry-run`. Review the exact
   file list, desired mode/provenance, delivery authority, policy text, available space,
   harness destinations, pin and digest. It is offline and read-only; no model is called.
2. Reconcile policy or unmanaged core conflicts. Never use force, alter AGENTS/CLAUDE,
   overwrite a constitution, grant trust or remove user files. When policy files exist,
   review them and pass their preview `policy.digest` as `--policy-review DIGEST`.
3. Apply `speckit prepare --home HOME --project PROJECT --preview-digest DIGEST
   --delivery-mode MODE` (plus policy review if needed). The preview digest must match;
   changed inputs require a fresh dry-run. Lock, staging, backup and journal protect
   managed files; failures roll back and remain visible as failed/pending.
4. Run `speckit verify --home HOME --project PROJECT`. It checks hashes, project skill
   paths and a disposable static feature/template/prerequisite tour. It reports verified
   preparation separately from native discovery and trust, which need harness evidence.
   Do not claim active/discovered from a copy receipt. Respect native trust review.
5. Return preparation changes through the project's existing delivery route. A local
   preparation receipt is specific to that root; verify/project assets must be present in
   each assigned worktree. New worktrees have separate private identities and locks.

`speckit rollback --home HOME --project PROJECT` is a separate explicit operation:
it restores only unchanged managed files from the journal/backup and preserves authored
specs, plans, constitution and decisions. Disable merely stops future selection and
keeps all files. Keep the backup while prepared. No Git automation or network is used.

## Choose a route

Establish the goal, current repository state, scope, uncertainty, risk, coupling, and observable acceptance. Apply the first relevant conditions together; labels alone do not override the actual work:

| Situation | Route and result before execution |
| --- | --- |
| Determined mechanical change | Brief with objective, limits and direct check; one worker. |
| Small bounded bug | Existing diagnostic-reasoning: faithful reproduction, tested hypothesis, supported cause and repair brief with regression check. |
| Difficult bug | Bounded diagnosis first. Use Spec Kit if the fix requires design, contract, or multi-unit decisions; otherwise a scoped repair brief. |
| Pure research | Question, alternatives, evidence and stopping condition; synthesize and check contrary evidence. Do not manufacture implementation units. If it leads to a software change, classify that change. |
| User or technical story | Spec Kit; for technical stories make interfaces, compatibility and invariants explicit. |
| Refactor | Establish baseline and invariants; use Spec Kit when it crosses contracts or subsystems, otherwise a proportionate brief. |
| Epic | Capability roadmap and shared decisions; use Spec Kit for the next slice, not detailed speculative plans for the whole epic. Consider spec-of-specs only when a phase still contains specs needing independent cycles. |
| Agent skill | Proportionate planning and behavioral contract; use existing evaluating-skills/Promptfoo process for selection and non-selection and evidence of answers, files and actions, subject to its authorization. |
| Multi-stage agent workflow | Spec Kit, including states, failures, external effects and concurrency where relevant, plus existing eval process. |

A short plan suffices for small work only if it resolves the decisions and acceptance. Investigate uncertainty that could change scope before fixing units. When conditions overlap, apply all relevant constraints and choose the deeper route when required; do not infer Spec Kit from size, file count or a name alone. Record the route and why, and delegate substantial investigation rather than absorbing transcripts. A missing material decision or evidence produces a bounded investigation or escalation under existing authority, not an invented answer.

## Plan, review, and delegate

For a Spec Kit route, use the following sequence under Firstmate's existing authority and project delivery mode:

1. Verify the project's pinned restricted core using the procedure above. If pending, arrange guarded preparation by an authorized worker before the dependent cycle; if conflicting or failed, report its concrete repair. Never run `specify init`, download an unpinned CLI, or change a pin during an active task.
2. Dispatch a planning scout in an assigned project worktree. Give it the current request, requirement and decision sources, repository instructions, scope, delivery mode, relevant invariants and evidence to inspect. Ask it to reconstruct current state, investigate alternatives and unknowns, then use the available core commands in order: specify → clarify → plan → tasks. It should record evidence, decisions, rejected alternatives and reconsideration conditions; map requirements to acceptance and checks, define shared contracts and dependencies, and group steps into coherent verifiable outcomes. Detail the next work, not speculative future slices. Return conclusions, unresolved decisions and paths/revisions, not full transcripts. Do not begin dependent implementation if decisive evidence is missing.
3. Dispatch a different, fresh session to review the plan with analyze. Provide both the original current requirements and the artifacts, not merely the scout's summary. Require evidence for omissions, contradictions, unsupported assumptions, contract and integration gaps, missing requirement coverage or checks, circular or unproduced dependencies, failure and compatibility cases, and units lacking enough context. Return findings and references. Have the planner correct affected parts and review corrections in fresh context as needed. Bound review rounds using existing Firstmate policy; escalate unresolved material findings rather than looping or silently accepting them.
4. Once findings are resolved within authority, preserve the plan, decisions and review evidence in the project repository using its authorized delivery procedure. Fix a commit that contains the accepted `spec.md`, `plan.md`, and `tasks.md`; verify each path exists at that commit and that the review applies to that revision. If correction changes the revision, reconcile review and acceptance before dispatch. The skill does not perform Git automation on behalf of Spec Kit.
5. Dispatch units by outcome, not one session per `tasks.md` step. Each brief includes the existing Firstmate ID and parent, repository, fixed commit and paths/sections for all three artifacts, global objective, unit scope and exclusions, relevant decisions and invariants, dependencies and their producers, acceptance IDs, verification and expected evidence, delivery mode and authority, escalation conditions and relevant external effects. A unit must be independently verifiable without deciding an open architecture or relying on future units for its acceptance. Keep implementation and its checks together when they establish one behavior.
6. Receive the unit ID and plan revision, commits, acceptance evidence, checks and limits, deviations and new decisions, uncertain external operations, integration state and next action. Check evidence against the accepted revision before closing or releasing dependencies. Keep `tasks.md` as planned work, not a second live board. For changed requirements or false preconditions, assess affected work, revise and review the affected plan, pin the new commit and inform in-flight and pending units; do not silently reuse old-revision evidence. Routine authorized corrections need not reopen the global plan.

## Spec Kit limits

Use only the approved v1.2.4 core, with no extensions or presets. Do not execute its automatic workflow, handoffs as automatic authority, Git/branch/commit automation, `taskstoissues`, or external writes. Project and Firstmate limits outrank every Spec Kit prompt. Do not let `implement` expand a unit's brief. Review constitution and generated instructions for conflicts before use. Keep work in the assigned worktree; for concurrent features use explicit per-session feature selection and non-persistence (`SPECIFY_FEATURE_DIRECTORY` and `SPECIFY_FEATURE_NO_PERSIST=1`) after the feature exists, and coordinate feature naming through Firstmate. If these conditions cannot be met, stop the affected cycle and report the blocker.

## Output

Report the chosen route and reason, durable decisions and evidence references, fixed plan revision and unit briefs when available, open findings or blockers, and the next action. Return concise conclusions and references, never full transcripts. Integration and activation are separate outcomes, proven by the existing owner rather than inferred from a completed plan.
