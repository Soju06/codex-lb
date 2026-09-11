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

## Local checks and remaining integration gate

These checks passed with run-owned database configuration established before
any application import:

- Affected API-key/Astra unit and integration selection: 274 passed.
- Repository-wide `ty check` and `ruff check .`.
- Formatting checks on the cancellation implementation and race regression.
- Strict validation of this OpenSpec change.
- Proxy architecture, cancellation safety and timing-seam checks.

The main API-key specification validates strictly. The main Responses
specification still reports 34 pre-existing missing SHALL/MUST errors, with
identical error paths/messages before and after synchronization. Its three new
requirements add informational long-text hints only. Automatic archival stopped
on those baseline errors after writing the API-key additions. The Responses
additions were synchronized manually and both complete delta bodies were
confirmed present exactly once. Archival therefore uses `--skip-specs` after
synchronization, with change validation enabled; the main-spec failures are not
claimed fixed or green.

The full repository `local-ci` gate, including PostgreSQL, packaging, Rust,
Docker and Helm, is intentionally run by the coordinating task after the code
and synchronized specifications are committed. Archival records completed
scoped implementation verification, not a full-gate pass, cloud CI success,
review approval, live-provider validation, merge or deployment.
