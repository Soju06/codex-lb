- [x] Define bounded idle stream-lease reclamation semantics for HTTP bridge creation
- [x] Reclaim one eligible idle local bridge when account selection reports `account_stream_cap`
- [x] Preserve active requests, pre-submit reservations, required account continuity, and API-key account scope
- [x] Add regression tests for successful reclaim-and-retry and active-session protection
- [x] Run focused unit tests, lint/type checks, architecture checks, and strict OpenSpec validation

Verification note: the targeted/full bridge suites, Ruff, ty, and strict/all
OpenSpec validation pass. The repository's pre-existing architecture ratchet
still reports `app/modules/proxy/service.py` at 2604 lines against a 2600-line
limit; this change does not modify that file.
