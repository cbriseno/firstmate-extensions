# Decision rule checks

Apply the decision table in `../skills/firstmate-planning/SKILL.md` to each independent case, without assuming facts not supplied. Compare the route and required preconditions with the expected result. This is a documented manual checklist, not evidence that a model or dispatch runtime was evaluated.

| Case | Request and known facts | Expected route and condition |
| --- | --- | --- |
| Mechanical | Rename a fixed constant; scope and direct check are known. | Brief and one worker; no Spec Kit cycle. |
| Small bug | An isolated typo produces a reproducible wrong label; cause is not yet established. | Diagnostic reasoning first; supported cause and regression check before repair brief. |
| Difficult bug | Intermittent failure; the fix might require a shared contract change. | Bounded diagnosis first; choose Spec Kit if diagnosis establishes design, contract or multi-unit decisions. Do not assume the fix. |
| Research | Compare two storage options and recommend one; no implementation requested. | Research question, evidence, contrary evidence and synthesis; no implementation units. |
| Story | Add a user-visible approval flow with acceptance criteria. | Spec Kit cycle and fresh review before units. |
| Technical story | Introduce a new interface with existing clients. | Spec Kit; explicit interface, compatibility and invariants. |
| Local refactor | Rearrange one internal function, preserving known behavior, without crossing contracts or subsystems. | Baseline and invariants, proportionate brief. |
| Cross-cutting refactor | Refactor across two subsystem contracts. | Baseline and invariants, Spec Kit and fresh review. |
| Epic | Deliver three capabilities over several phases. | Roadmap; Spec Kit only for the next slice. |
| Skill | Create an agent skill with a behavioral contract. | Proportionate planning and existing eval process, including selection/non-selection and action evidence within authorization. |
| Workflow | Build a multi-stage agent workflow with external effects. | Spec Kit plus states, failures, effects and existing eval process. |
| Overlap | A multi-stage workflow is also a technical story. | Spec Kit and both sets of constraints. |
| Unknown scope | A request may cross contracts, but evidence is missing. | Investigate the scope before fixing units; do not assume a simple brief is sufficient. |

For each case, mark pass only when the selected route **and** all conditions match. Re-run affected cases after changing the decision table. Separate behavior evaluations require the approved existing evaluation process.

## Scoped operator requests (manual semantic checks)

The following examples check the updated operator path without claiming a model run.
Use the exact project record, never a name match against arbitrary folders.

| Request and known facts | Expected commands and result |
| --- | --- |
| “usa Spec Kit en todos los proyectos”; one native project exception exists | Inspect status; set installation default speckit; report and preserve that native exception. |
| “desactívalo en sample-deck”; its registered root is known | Set that project's home override native; preserve files and active briefs. |
| “actívalo solo en este proyecto”; no other scope change requested | Set project override speckit; report other enabled exceptions without silently clearing them. |
| “desactívalo en todos”; project and home speckit exceptions exist | Show installation dry-run inventory, set native with clear-project-overrides, and clear each remaining speckit home default. |
| “clear this project”; installation default speckit | Clear project override; the new task inherits speckit, rather than disabling it. |
| Resume an existing speckit brief after defaults switch to native | Preserve mode, origin, resolver, language and core digest from that brief. |
| Full Spec Kit story in an unprepared project | Arrange dry-run and authorized worker preparation, verify, then resolve with require-core before dependent planning. |
| Mechanical typo fix with mode speckit and core pending | Direct route still applies; do not require a full core cycle for the typo. |
| Project AGENTS forbids Spec Kit; an override requests speckit | Stop the dependent cycle with the policy conflict; never edit AGENTS or downgrade silently. |
| Ordinary worker asked to fix a bug | Do not adopt primary planning coordination or change defaults. |

These semantic expectations complement the executable CLI fixtures; automatic skill
selection and natural-language tool use have not been evaluated with a model here.
