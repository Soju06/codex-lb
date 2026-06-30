## Why

The Codex review label synchronizer can keep `🤖 codex: needs work` on a PR
after a maintainer resolves an inline Codex finding. The unresolved-thread check
already knows resolved review threads are no longer blockers, but the timeline
classifier still merges the old inline review comment and treats it as current
needs-work evidence.

## What Changes

- Ignore inline Codex finding comments from resolved or outdated review threads
  when computing current-head review state.
- Keep unresolved inline Codex finding comments as needs-work evidence.
- Add regression coverage for both resolved and unresolved inline findings.

## Impact

- GitHub automation only.
- No application runtime behavior changes.
