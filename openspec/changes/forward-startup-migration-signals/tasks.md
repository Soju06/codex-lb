## 1. Process Regressions

- [x] 1.1 Add an inert executable entrypoint probe that records migration and application argv, PID, environment, signal receipt, and release gates in a per-test temporary directory.
- [x] 1.2 Add actual-process integration tests for both shipped entrypoints covering successful startup, migration failure, standard-image migration bypass, SIGTERM, SIGINT, child reaping, and interrupted-start application suppression.
- [x] 1.3 Run the signal scenarios before production edits and capture deterministic failures caused by missing child signal forwarding.

## 2. Entrypoint Implementation

- [x] 2.1 Replace blocking distroless migration execution with signal-owning child supervision that forwards SIGTERM/SIGINT, reaps once, normalizes exit status, and launches the application only after uninterrupted success.
- [x] 2.2 Route the standard image's migration-enabled shell path through the shared Python supervisor while preserving its migration-disabled direct `app.cli` handoff.
- [x] 2.3 Update the secondary launcher topology test without substituting string assertions for process-level signal coverage.

## 3. Verification

- [x] 3.1 Run the focused entrypoint and launcher-contract tests and confirm every RED scenario is GREEN.
- [x] 3.2 Run shell syntax, Ruff, formatting, type diagnostics, and strict OpenSpec validation for the changed surface.
- [x] 3.3 Exercise SIGTERM against both actual entrypoints with the inert probe, verify signal forwarding/reaping/no app launch, and remove every temporary process and file.
- [x] 3.4 Inspect the final diff and worktree status to confirm no migration SQL, timeout, deployment-manifest, dependency, or unrelated change entered scope.
- [x] 3.5 Add a deterministic regression for a parent signal arriving after child exit observation but before reaping, proving no stale PID is signaled.
- [x] 3.6 Make process-test cleanup own a separate process group and prove cleanup before migration marker publication leaves no child.
- [x] 3.7 Re-run focused verification and independent Sensitive review after the race and cleanup fixes.
