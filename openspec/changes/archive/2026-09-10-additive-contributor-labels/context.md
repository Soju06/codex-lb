## Context

The existing labeler reads trusted base configuration and adds migration labels without checkout. Issue forms declare bug or enhancement plus triage, but plain API submissions bypass form defaults.

## Goals / Non-Goals

Provide additive classification using existing labels. Review status, needs-info resolution, stale closure, historical backfill, and maintainer approval remain outside this change.

## Decisions

Extend actions/labeler with path mappings and retain `sync-labels: false`. Documentation includes agent guidance and OpenSpec. Python covers Python source and dependency files. Frontend, CI, and Docker mappings identify changed areas without guessing change intent.

Use a pinned github-script step for issues opened. Read the current title through the API, recognize exact conventional prefixes, and add labels through `issues.addLabels`. `bug` and `fix` map to bug, `feat` to enhancement, and `docs` to documentation. Optional scopes and breaking-change markers are accepted. Unknown titles receive triage only. Author text is data, never interpolated code.

The test boundary is event metadata and emitted GitHub label requests. No application imports or database access are needed.

## Risks / Trade-offs

Additive rules retain classifications after later edits or path reversions. Maintainers retain removal authority. Title classification uses explicit prefixes rather than guessing from prose.

GitHub does not trigger another workflow for issues created with GITHUB_TOKEN. Such producers must label their own issues. Existing records need separate maintainer triage. Upstream execution requires the workflows on the default branch.

## Migration Plan

Merge the workflows, then observe a fork PR and newly opened API issue. Revert the workflow changes to stop future classification; existing labels remain intact.
