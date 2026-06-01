# Change: Clean up superseded beta release PRs

## Why

The beta release sync workflow upserts only the beta PR for the currently discovered release-please train. When the release-please train moves before the beta PR is merged, older automation-created beta PRs stay open and remain mergeable even though they no longer represent the current train.

## What changes

- Identify open automation-managed `release/beta-*` PRs that are not the current beta branch.
- Close superseded beta PRs with an explanatory comment.
- Delete their repository-owned release branches after closing.
- Preserve manually-created, protected, or current beta release PRs.

## Impact

- Reduces stale release PR clutter.
- Prevents accidental publishing of obsolete beta trains.
- Keeps cleanup scoped to PRs created by the beta release automation sentinel text.
