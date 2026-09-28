# Scoped verification

## Completeness and correctness

All implementation tasks are complete. The five added requirements map to:

| Requirement | Implementation | Focused coverage |
| --- | --- | --- |
| Queued steering extends one reserved usage reservation | WebSocket steering admission, API-key reservation extend/reduce and refreshed limit reconciliation | API-key service tests; steering policy refresh and policy race integrations |
| Reservation lifecycle uses current ORM accounting values | Locked adjustment reads and post-claim terminal reloads | API-key service tests; PostgreSQL reservation-refresh integrations |
| Steering continuations retain owned WebSocket lifecycles | Steering module and WebSocket request matching/finalization | Protocol, parent-ID, explicit-swap, release, retention and HTTP snapshot tests |
| Steering correlation history retires with its connection | Retired-parent guard, expiry tombstones and drain rotation | Expiry and retirement integrations; retired-parent protocol regressions |
| Steering dispatch follows the owned transport handoff | Python transport observer, native acknowledgment callback and archive delegation | WebSocket dispatch units; real compressed aiohttp and WebSocket dispatch integrations; native egress tests |

The final repair's affected SQLite selection passed 274 tests. It includes real
upstream TCP/WebSocket connections, the actual ASGI route and persisted quota
items/counters. The refreshed-policy matrix covers an absent reservation, an
existing reservation and a previously inapplicable model-filtered limit.
Rejected submissions do not reach upstream; admitted successors settle once.

Cancellation/rejection controls hold the reservation result after commit and
coordinate through explicit events. Cancellation of the downstream sender
before first-reservation attachment previously left a reserved row on
`97a57a108`; the detached-baseline cancellation selection produced one expected
failure and one passing existing-reservation control. The corrected path
finishes attachment before propagating cancellation, and teardown leaves no
reservation, admission or heartbeat orphan. A separate transport barrier lets
the first rejection finish before an already admitted second steer writes,
preserving the second successor's accounting.

## Coherence

The existing retired-parent restriction remains the P1 disposition. No
same-parent steering retry or per-rejection connection rotation was introduced.
Native Responses SSE framing and transport-owned handoff remain upstream-owned.
Refreshed quota items use existing admission limits/budget policy, rather than
introducing a new quota policy or changing terminal actual-usage accounting.

One stale scenario said an anonymous error could not settle an already visible
request, contradicting the adjacent live-request-priority scenario and current
matching behavior. It now describes the actual protected case: an undispatched
explicit replacement with no eligible live request. No implementation behavior
changed for that documentation correction.

## Historical scoped checks

These checks passed with run-owned database configuration established before
any application import:

- Affected API-key/Astra unit and integration selection: 274 passed.
- Repository-wide `ty check` and `ruff check .`.
- Formatting checks on the cancellation implementation and race regression.
- Strict validation of this OpenSpec change.
- Proxy architecture, cancellation safety and timing-seam checks.

The initial archive encountered 34 baseline Responses-spec validation errors;
its additions were synchronized manually and confirmed present exactly once.
That historical limitation is superseded by the refreshed verification below:
pinned OpenSpec 1.11.0 strictly validates all 66 main specifications.

## Refreshed full verification, 2026-09-27

Candidate `c878953a971276bd55b5b5d31def8038e1d1d4be` contains repaired main
`09a140fa9979a908e60acc97232367e0a08ef32c`. The complete
`uv run pre-commit run local-ci --hook-stage manual --all-files --verbose`
passed on Linux/arm64 with dedicated SQLite and PostgreSQL databases.
Frontend: 181 files and 1643 tests passed. Python stages: 10576 unit,
3021 integration-core, 356 bridge/WebSocket, 27 PostgreSQL-core and
257 PostgreSQL integration tests passed. Existing skips and expected failures
remain reported; no gate stage was filtered out.

The gate also passed lint/type checks, migration topology, Rust, packaging,
Docker, Helm lint/template and both kind smoke configurations. The external
database smoke verified a two-member bridge ring and successful Helm tests.
Gate and cleanup both exited zero; disposable containers and kind nodes were
removed, and pre-existing image tags restored.

The refreshed focused route/transport selection passed 83 tests. A separate
four-case real aiohttp/ASGI trace covered accepted and rejected steers with
completed and failed explicit continuations, preserving explicit ownership
through compression and transport drain. The sequential-WebSocket fixture
correction passed three related integrations and retained both account-owner
and upstream-payload assertions.

The independent committed-diff reviews of the hot-path repair and subsequent
reservation-cleanup test alignment remain applicable; the refresh adds main,
spec context and the narrowly reviewed fixture correction. The follow-up is
archived at `../2026-09-27-defer-astra-steering-overhead/`. Its normative delta
already appears exactly once in the owning main spec.

This receipt establishes local verification, not hosted-CI success, fresh
automated review approval, live-provider validation, merge or deployment.
