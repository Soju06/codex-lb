# Add beta release channel

## Why

codex-lb currently has one stable release path managed by release-please. Maintainers need a beta channel that is still PR-driven, does not create a long-lived beta branch, and can be promoted to the normal stable release by merging the existing release-please PR.

## What Changes

- Add an automatic workflow that keeps a beta release PR synced from the open release-please PR without requiring manual workflow dispatch.
- Add a PR-merge workflow that publishes merged beta release PRs as GitHub prereleases.
- Teach the release publishing workflow to accept prerelease tags and avoid stable aliases (`latest`, major, major.minor) for prerelease Docker images.
- Keep `.github/release-please-manifest.json` owned by stable release-please; beta PRs do not advance the stable manifest.

## Non-Goals

- No long-lived `beta` branch.
- No private/exclusive artifact access control.
- No replacement of release-please for stable releases.
