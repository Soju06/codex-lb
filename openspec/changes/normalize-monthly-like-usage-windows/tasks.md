- [x] Define a bounded monthly-like duration classifier
- [x] Normalize poller and live-ingest windows before persistence
- [x] Promote authoritative observed monthly rows in account summaries
- [x] Align load-balancer short/long window semantics
- [x] Add regression tests for the 43800-minute Team shape and stale-plan protection
- [x] Run focused/full tests, lint, type checks, architecture checks, and strict OpenSpec validation

Verification note: relevant unit and integration suites, Ruff, ty, and
strict/all OpenSpec validation pass. The repository's pre-existing architecture
ratchet still reports `app/modules/proxy/service.py` at 2604 lines against a
2600-line limit; this change does not modify that file.
