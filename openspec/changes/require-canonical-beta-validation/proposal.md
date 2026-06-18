# Require validation for canonical beta PRs

## Problem

The beta publish workflow correctly refuses to create release artifacts when a merged canonical `release/beta-*` PR lacks release-candidate validation evidence. However, the pre-merge `Beta release guard` can pass a canonical beta PR when release-managed metadata is already identical to `main`, because it treats the PR as a no-op diff and skips validation evidence checks.

That leaves a green PR that can be merged, only for publish CD to fail after merge.

## Solution

Treat canonical `release/beta-*` PRs as publish intent whenever the checked-out tree already contains the matching beta version, even if release-managed metadata is unchanged relative to the base branch. Require the same release-candidate evidence before merge that the publish workflow requires after merge.

## Changes

- Update the PR guard to identify canonical beta release PRs before no-op release metadata short-circuiting
- Require validation evidence for canonical beta PRs even when release-managed version files are unchanged against the base branch
- Preserve the existing no-op bypass for non-canonical dependency or maintenance edits on beta-version bases
- Add regression coverage for canonical beta PRs with unchanged metadata and missing evidence

## Out of scope

- Creating or rerunning release tags/artifacts
- Changing the release-candidate validation checklist labels
- Changing stable release behavior
