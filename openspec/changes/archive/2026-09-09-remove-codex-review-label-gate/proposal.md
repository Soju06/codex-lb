## Why

The repository is adopting CodeRabbit on its OSS plan as the always-on mechanical reviewer under issue #1756. The auto-posted `@codex review` and `🤖 codex: ok` label gate is therefore redundant and should be retired.

## What Changes

- Replace the documented Codex cloud-review merge gate with a CodeRabbit gate that requires actionable findings to be fixed or explicitly addressed or dismissed in-thread on the merge-target head.
- Remove the Codex review label synchronization workflow, script, and unit tests.
- Remove the associated `needs rebase` label synchronization as accepted collateral; triage uses GitHub's live `mergeable` API field as its source of truth.
- Discard the in-flight `label-sync-rate-limit-fallback` change because it only patches the machinery being removed.
- Keep local `codex review --base origin/main` runs as an encouraged extra tool, not a merge gate.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-automation`: remove the requirements governing the retired Codex review and needs-rebase label synchronization machinery.

## Impact

- GitHub automation no longer auto-posts Codex review requests or maintains Codex review and needs-rebase labels.
- Contributor guidance uses CodeRabbit review evidence for the mechanical-review merge gate.
- Branch-protection status checks and simplicity-budget override label behavior remain unchanged.
