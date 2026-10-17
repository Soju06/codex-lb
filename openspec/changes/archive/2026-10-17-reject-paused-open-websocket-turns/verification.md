## Regression proof

The new route-level test fails on the unchanged base: the paused account receives a second response.create and returns response.created instead of the expected rejection. The isolated baseline checkout contained only the new test file.

The focused suite passes 245 tests covering the new route regressions, existing direct WebSocket transport contracts, routing-cache invalidation, terminal cancellation and model-source guards. Tests use isolated SQLite databases and controlled upstream transports; no external provider or deployment is required.

The new tests cover both public routes, fresh frames and previous-response deltas, pause before and during admission, same-socket reactivation, and rejection while another response drains. An account response-create cap of one makes continued admission after rejection prove that the rejected request releases its slot.

## Contract and related work

The base is commit 72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e. Its final direct WebSocket send path does not check the routing-availability cache. The change applies the existing unavailable-account policy to that dispatch boundary without introducing a new policy or per-frame database query.

Related work was compared: #1255 provides the existing invalidation bus; #2115 concerns steering dispatch; #2117 concerns revoked credentials and retry eligibility; #2561 concerns warm HTTP-bridge admission caps. None implements this ordinary direct-socket per-turn check. The changes do not depend on those open PRs.

The scoped OpenSpec delta passes strict validation. The repository-wide spec validation has the same 17 invalid specifications and 71 errors before and after this change, with no added errors.

## Review and limits

The committed production diff and cleanup/dispatch helpers were inspected manually. The independent local review service rejected the review requests without a substantive review, so no independent local approval is claimed. GitHub review and CI are recorded on the PR rather than asserted here.

The required `uv run pre-commit run local-ci --hook-stage manual --all-files` was run. Frontend, lint, typecheck, Rust checks and Rust dependency audit passed. Its unit/simulation stage finished with 10,480 passed, 99 skipped, 1 expected failure and one failure: `test_output_under_a_temporary_filesystem_is_refused`. That same failure was reproduced on the unchanged base on macOS. The gate stopped at that stage, so later integration, packaging, Docker and Helm stages were not completed locally; GitHub CI is the remaining integration gate.
