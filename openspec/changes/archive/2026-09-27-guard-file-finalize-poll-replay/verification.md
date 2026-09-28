# Verification

The actual file-route regression reproduced the review finding before the product edit: an unpinned finalization received `retry` from account A, then a refused connection on its next poll replayed the operation on B and returned 200 instead of the expected 502. The pinned control already failed closed.

The fix records whether an upstream poll has returned during the routed finalization invocation and denies cross-account replay after that point. The direct transport path and first-poll pre-dispatch failover retain their existing behavior.

## Evidence

- Focused route/client/unary suite: 65 passed. The new regression covers both pinned and unpinned late-poll failures, with the existing poll delay set to zero rather than relying on timing.
- Live HTTP checks: late unpinned failure returned 502 with only A called twice; first-poll refusal returned 200 with A then B; a created file was durably pinned to B; pinned late failure returned 502 with only B called twice.
- Scoped Ruff, formatting, ty, and Python LSP diagnostics passed. Pinned OpenSpec 1.11.0 strict validation passed for this change and the owning `responses-api-compat` spec.
- Full `uv run pre-commit run local-ci --hook-stage manual --all-files --verbose` passed on Linux/arm64 at `5a5d9e8405ff973c8f19a4bf5a86e89fdb1bbc3c`, including frontend checks, Python checks, Rust checks and dependency audit, SQLite/PostgreSQL migrations, packaging, Docker build/scanning, Helm validation, and both kind smoke scenarios.
- The full run passed 1,643 frontend tests and 14,023 Python tests. Existing skips and the expected failure were unchanged; no test was filtered or weakened for this fix.
- Disposable HTTP processes, databases, containers, and kind resources were cleaned after verification; original shared test-image tags were restored.

The main spec and context were synchronized before verification. This archive follow-up changes only documentation and does not change the full-gate-tested code, tests, or configuration. Controlled transport failures exercised the real HTTP route; no production or real upstream proxy was contacted.
