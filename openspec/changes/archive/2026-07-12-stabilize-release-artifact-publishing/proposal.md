# Change: Stabilize release artifact publishing

## Problem

A stable GitHub Release can become visible before the release publishing workflow has successfully built and published PyPI, Docker, and Helm artifacts. The `v1.20.0` incident showed a second gap: the release-please stable PR promoted most release-managed files to `1.20.0` while `uv.lock` remained on `1.20.0-beta.3`, causing the Release workflow to fail after the public GitHub Release already existed.

## Solution

- Add a stable release PR guard that rejects release-please stable promotions unless every release-managed version field agrees and advances together
- Add a Release workflow failure cleanup job that withdraws a release-event GitHub Release back to draft when publishing fails, so dashboard/latest-release consumers do not keep advertising a non-installable stable release
- Restore current main release metadata consistency by moving the editable codex-lb `uv.lock` entry back to the current stable version

## Changes

- Stable release PRs from `release-please--branches--main` are guarded separately from beta release PRs
- Release-managed version fields must be consistent before a stable promotion can pass CI
- Failed release-event publishing attempts make the corresponding GitHub Release draft again

## Out of scope

- Rewriting the release channel model
- Automatically cutting the next patch release
- Changing dashboard update semantics beyond preventing failed public releases from remaining latest
