# Integrated verification, 2026-09-27

The integrated candidate `5473effc224dccf30e88328dc0793d257b19f064`
contains repaired upstream main
`09a140fa9979a908e60acc97232367e0a08ef32c`. The former migration-topology
blocker is resolved.

## Full local gate

`uv run pre-commit run local-ci --hook-stage manual --all-files --verbose`
passed without filtering on isolated Linux/arm64. The gate and cleanup both
exited 0. Frontend coverage passed 1,643 tests; Python stages passed
10,811 unit, 3,349 integration-core, 364 integration-bridge, 27 end-to-end
and 245 PostgreSQL tests, totaling 14,796. Existing skips, the expected
failure and dependency warnings were not suppressed.

All frontend checks, Python static checks, Rust checks and dependency audit,
SQLite/PostgreSQL migration checks, packaging, Docker build/scanning,
Helm validation and both kind smoke scenarios completed successfully.

## Focused and real-surface evidence

The refreshed focused suite passed 3,523 tests. Eight isolated TCP scenarios
used Uvicorn, the production HTTP routes and a controlled upstream WebSocket:
canonical/backend routes with and without trailing slashes, delayed function
and custom-tool results, invalid IDs, and boolean/string marker owner-loss
controls. These are controlled transport proofs, not live-provider validation.
Scoped static checks and independent candidate inspection found no new
actionable issue from integrating main.

Pinned OpenSpec 1.11.0 strict validation passed for both follow-ups and all
66 main specs. The settled-prefix and fixture-marker requirements and examples
were synchronized before the full gate. The verified follow-ups are archived
as `2026-09-27-validate-settled-async-prefix-pairs` and
`2026-09-27-preserve-async-fixture-markers`.

## Ownership and limits

Disposable test databases, processes, gate/PostgreSQL containers, kind resources
and the generated test image were removed. The two pre-existing Docker tags
were restored. The archival follow-up changes documentation only; runtime code,
tests and configuration remain identical to the full-gate-tested candidate.

Hosted CI and received review feedback are checked separately on the published
head. This receipt does not claim a fresh bot approval, merge or deployment.
