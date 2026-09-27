# Verification after upstream migration repair

The branch integrates upstream `main` at `09a140fa9979a908e60acc97232367e0a08ef32c`, including #2461's migration repair, without rewriting its review history. No additional product or test changes were needed during this refresh.

- Full `uv run pre-commit run local-ci --hook-stage manual --all-files --verbose` passed on isolated Linux/arm64 at `c14e8e1964413b4f119e35f4dd1ff97ed068999c`.
- All frontend checks, Python checks, Rust checks and dependency audit, SQLite/PostgreSQL migrations, packaging, Docker build/scanning, Helm validation, and both kind smoke scenarios completed.
- The full run passed 1,643 frontend tests and 14,315 Python tests. Existing skips and the expected failure were unchanged; no test was filtered or weakened for this refresh.
- The focused policy/history/source/owner/serialization suite passed 309 tests. Ruff, formatting, type checks, LSP error diagnostics, architecture/cancellation/timing checks, migration topology, and the wheel build passed.
- Actual application-route requests under ASGI lifespan with controlled upstream responses verified an allowed Low update returning 200 with one upstream send and a forbidden High update returning 403 with no additional send.
- Pinned OpenSpec 1.11.0 strictly validated the active change and all 66 main specs. Both added requirement blocks were synchronized exactly once into the owning main specs, with stable context and examples.
- The specification-sync and archive follow-ups modify only OpenSpec documents. Product code, tests, and runtime configuration are identical to the full-gate-tested commit.
- Disposable QA databases, processes, containers, kind resources, and test images were cleaned; original shared test-image tags were restored.

This verification does not claim a production deployment or contact with a real upstream provider. The historical multiple-head migration blocker is resolved in the integrated base.
