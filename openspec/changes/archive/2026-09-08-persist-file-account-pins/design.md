## Context

`ProxyService` records upstream-issued file ownership in a process-local dictionary. File finalize and Responses input-file routing already consume ownership through `_pin_file_account`, `_resolve_file_account`, and `_lookup_file_pin`, but another replica cannot observe that state. The application database and `SessionLocal` are already the shared coordination substrate.

## Goals / Non-Goals

**Goals:**

- Make live file ownership visible to every replica.
- Preserve the existing 30-minute expiry and opaque unknown-file compatibility.
- Keep account-owner routing fail-closed and the change behind existing service boundaries.
- Support the repository's PostgreSQL production path and SQLite test/development path.

**Non-Goals:**

- Change upload/finalize API payloads, routing precedence, or retry policy.
- Add operator configuration or background cleanup infrastructure.
- Backfill pins that existed only in memory before migration.

## Decisions

1. Add a `file_account_pins` table keyed by `file_id`, with an account identifier and absolute UTC expiry. A dedicated repository owns idempotent ownership claims and live lookup. A live claim is immutable across accounts; same-owner claims renew it, while an expired ID can be claimed again. This is smaller and more explicit than overloading sticky-session namespaces.
2. Use one short-lived `SessionLocal` session per pin/read operation. `ProxyService` is process-scoped, so retaining a request-scoped session would be unsafe; the established durable bridge/ring pattern already uses a session factory.
3. Do not cache durable owner decisions in process memory. `_pin_file_account` writes through the repository, while every `_resolve_file_account` call reads the shared database. Multi-file resolution uses one database query so all referenced IDs are classified from the same repository operation. An authenticated inter-replica forwarding value only corroborates the receiver's fresh database result; it cannot replace that read.
4. Evaluate expiry inside each database statement. PostgreSQL claim, reclaim, and live lookup use `clock_timestamp()`, and every successful claim performs an owner-guarded expiry refresh in the same transaction. The refresh gives a full post-wait TTL even when a new-row insert blocked behind an uncommitted unique contender that later rolled back. PostgreSQL cleanup uses the DB-authoritative, stable `statement_timestamp()` cutoff so the expiry index remains usable. SQLite uses its statement-native UTC clock with the same fractional width as stored `DateTime` values and the same guarded post-claim refresh. All expiry decisions therefore stay in the database clock domain without replica-clock skew or exact-expiry ambiguity.
5. Translate persistence failures at the ownership boundary into the stable fail-closed proxy error. Run finalize lookup and post-upstream pin persistence inside the existing file request-log lifecycle, and keep Responses lookup errors inside the existing startup-error lifecycle, so failures neither leak API-key reservations nor record a failed request as successful.
6. Keep exactly one owner for a Responses usage reservation across API startup, direct or compact service settlement, and HTTP-bridge forwarding. Within one replica, the API layer owns cleanup through the durable file-owner lookup and any following preflight outside the service settlement guard. The direct stream service signals when it enters its settlement-guarded `try/finally`; the local HTTP-bridge service signals only after a successful request submit installs the request-state finalizer; and compact service settlement signals after its one cancellation-safe settlement attempt. From those exact boundaries the service finalizer or settlement attempt owns cleanup even if no upstream event has arrived. For an authenticated cross-replica forward carrying the origin reservation, the receiver delays its successful HTTP 200 until its own service has reached one of those settlement-owned boundaries. The 200 response is the receiver's cleanup-handoff acknowledgement. The origin records dispatch only after local payload and header construction and immediately before entering the request transport. A definitive non-200 response leaves cleanup at the origin. If dispatch occurred but no HTTP status can be observed, the origin must not actively release or replay because the receiver may already own settlement; the receiver finalizer or the existing stale-reservation reaper resolves the ambiguity. The reaper releases reservations whose `updated_at` is older than six hours, or whose `created_at` is older than 24 hours even if a heartbeat keeps refreshing `updated_at` (`#1600`). A `DISPATCH_AMBIGUOUS` reservation can therefore hold quota until the 24-hour hard ceiling plus one hourly janitor loop, but a premature origin release cannot discard receiver-recorded usage. A receiver-side owner-revalidation failure or cancellation before dispatch or after a definitive non-200 propagates with origin cleanup intact. An initial client-facing SSE heartbeat or any other frame does not transfer ownership. Cancellation or owner-lookup failure while cleanup remains at the API layer schedules one tracked release, including when a bounded startup probe has handed pending preflight work to the response body; the origin cancels and awaits that pending startup task before releasing. Each cleanup owner makes one cancellation-safe attempt and, if that persistence write fails, schedules one follow-up release instead of abandoning the reservation. A cleanup-database failure must not mask the original `file_owner_unavailable` error or cancellation.
   Once compact settlement has transferred ownership, later receiver-side output validation or a `usage_settlement_failed` raise cannot safely turn the response back into a non-200 rejection. The receiver therefore preserves HTTP 200 and emits a terminal `response.failed` SSE event with the stable error code; the origin keeps the handoff and does not release or replay. Cancellation after owner-forward transport begins, including during response-header wait, is dispatch-ambiguous. Definitive connector failures stay not-dispatched.

## Risks / Trade-offs

- [Every hard ownership decision adds a database read] → use one indexed lookup for one file and one batched indexed lookup for multi-file requests; correctness across replicas takes precedence over process-local locality.
- [A database outage can make file create/finalize unavailable] → fail closed because silently selecting another account can disclose or corrupt account-scoped operations.
- [An idle installation can retain expired rows until the next upload] → the rows are inert and every new claim performs indexed opportunistic cleanup.
- [Migration overlaps another branch] → base the revision on the current single head and report any later head conflict rather than editing another track.

## Migration Plan

Upgrade creates the empty ownership table and index; new uploads populate it immediately. Downgrade drops only the new table. Existing in-memory pins cannot be backfilled.

The behavior change is not safe under an ordinary mixed-version rolling rollout: a legacy replica cannot read pins written by a new replica, and a new replica cannot read a legacy replica's process-local pins. Operators must migrate the database first, stop legacy replicas from accepting new file registrations, drain the legacy upload/finalize window for up to the 30-minute pin TTL (or explicitly accept retrying those in-flight uploads), and then cut all file-serving replicas over without mixed-version file traffic. Deployment automation is intentionally outside this code change.

## Open Questions

None.
