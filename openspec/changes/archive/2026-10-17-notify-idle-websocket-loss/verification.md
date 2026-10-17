## Regression and scope

On base 72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e, a completed Codex response followed by upstream close leaves the downstream open. The new route test fails because the client receives no reconnect notification.

Related work was compared: #1579 covers a silent upstream timeout during work; #1305 concerns already queued work during clean handoff; #2519 concerns multiline HTTP-bridge frames. None provides this notification for a completed, idle ordinary Codex socket. The native burst-buffer repair is independent.

## Verification

Both clean close and transport error notify the Codex client with code 1012. A reconnect with full context has no stale previous_response_id injected. Focused tests cover newer shared anchors, a late request while close waits, uncompleted work, pending work, replay ownership and the public OpenAI-compatible route.

The broader focused suite passes 199 tests: idle-loss route tests, existing Responses WebSockets, idle-loss ownership/race tests and terminal cancellation. The final idle-loss-specific suite passes 9 tests. Two existing send/close race tests also pass after their observer wrapper forwards the new context arguments; their ownership and settlement assertions remain unchanged. The final full local CI rerun passed frontend tests (1658), Python lint/types, Rust tests/build and dependency audit. The backend suite reported 10487 passed, 99 skipped and 1 xfailed, with only the unchanged macOS-specific test_output_under_a_temporary_filesystem_is_refused failure, reproduced on the base commit. Later integration/package stages were not reached locally; GitHub CI is the authoritative complete gate. Ruff and type checks pass; the CI-pinned OpenSpec validator accepts all 67 canonical specs and the scoped change.

## Review and limits

The diff is manually reviewed against the existing terminal ownership and cleanup paths. Independent review service requests are unavailable in this task. No deployment or real-provider claim is made. The fix deliberately requires a reconnect with enough client context; it does not reconstruct lost upstream state.
