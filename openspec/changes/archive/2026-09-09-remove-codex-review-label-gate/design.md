## Context

The retiring gate is implemented as one GitHub Actions workflow, one synchronization script, and its dedicated unit test module. Contributor guidance and workflow comments also describe that automation. Branch protection requires stable CI check contexts, not Codex labels, and the simplicity-budget workflow still needs label events for its independent override label.

## Goals / Non-Goals

**Goals:**

- Remove the Codex review label automation as one coherent unit.
- Replace its merge-gate documentation with current-head CodeRabbit evidence.
- Preserve required CI check names and simplicity-budget override behavior.

**Non-Goals:**

- Changing branch-protection rules or required CI jobs.
- Removing the local Codex review harness or optional local review command.
- Replacing the removed `needs rebase` label with another synchronization workflow.

## Decisions

- Delete the workflow, script, and dedicated tests instead of disabling them. This prevents dormant automation from remaining a maintenance surface; retaining a disabled compatibility shim was rejected because there are no protected status checks or consumers to preserve.
- Remove every main-spec requirement whose behavior is implemented by the deleted synchronizer, including apply-time reclassification. The unrelated CI path-filtering and simplicity-budget requirements remain outside the delta.
- Keep `labeled` and `unlabeled` events on the simplicity-budget workflow because they re-evaluate `simplicity-budget-approved`, independent of the deleted label churn.
- Treat GitHub's live `mergeable` API field as triage evidence instead of replacing the removed `needs rebase` label sync.

## Risks / Trade-offs

- [Risk] Stale `needs rebase` labels may remain after automation removal. → Triage must use the live `mergeable` field, which is already the accepted source of truth.
- [Risk] Documentation could imply that optional local Codex review is still mandatory. → State consistently that CodeRabbit is the gate and local Codex review is only encouraged.
