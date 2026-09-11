# Keep CodeRabbit summaries out of PR bodies

## Why

CodeRabbit's automatic release-note summary edits the PR description after a
push. CI intentionally handles `pull_request.edited` for release validation, so
the edit cancels an in-flight run and starts the full matrix again for the same
head. PR #2171 encountered this repeatedly.

## What Changes

Configure CodeRabbit to put its high-level summary in the walkthrough comment.
Keep summary generation and review enabled. Preserve CI triggers, concurrency,
required check names, and release-evidence validation.

## Impact

- Configuration: `.coderabbit.yaml`.
- Register the vendor-required root configuration in the root-entry allowlist.
- Owning capability: `github-automation`.
- No application behavior, dependencies, operator settings, or UI changes.
