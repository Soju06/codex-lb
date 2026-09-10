Validated against base `0f6a31c56` using Python 3.14.6.

- Responses regression suites: 293 passed (normalization, native/public HTTP,
  source forwarding, keepalives, and stream ownership).
- Quota/privacy and ingestion suites: 67 passed; four existing PostgreSQL-only
  transaction/row-lock tests skipped in the isolated SQLite environment.
- Additional transport and projection checks: 20 passed, including unit tests
  also present in the Responses regression run.
- Full repository Ruff lint, formatting, and `ty check`: passed.
- Proxy architecture, cancellation safety, timing seams, and settings-tier
  checks: passed.
- Strict OpenSpec validation: all 65 capability specs and this change passed.

New external-path tests cover HTTP direct and HTTP-to-WebSocket bridge routes,
canonical and trailing-slash HTTP URLs, both WebSocket Responses URLs, real
API-key privacy settings, real two-account aggregation, unknown quota families,
original bridge ingestion attribution, and quota-cache read failures while
normal completion remains deliverable. Unit tests also cover cancellation,
owner visibility, malformed/unknown metadata, and split source SSE frames.

Production settings and deployment have not been changed. With upstream quota
privacy enabled, deployment will suppress account quota events; publishing
pool percentages still requires the existing visibility setting to allow them.
