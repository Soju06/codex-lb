## Context

See proposal.md. Production `e77106e5` has byte-identical `request_submit.py` to main and the relevant `LoadBalancer.acquire_account_lease` code is unchanged. At 10:42–10:59 UTC on 2026-10-02 two warm `gpt-6-luna` HTTP requests emitted 61 capacity retries. In the encompassing 20-minute window, 434 fresh selections succeeded on the same account. Each stalled hard session was eventually idle-evicted, followed by `bridge_continuity_persistence_failed` (502). Read-only database inspection found dashboard caps of 128 streams / 32 creates, no cap env overrides, one ring member, and startup defaults of 8 / 4. The reacquire helper passes routing tunables and fair-share threshold but no `concurrency_caps`; the balancer intentionally defaults a missing cap snapshot to startup configuration.

Health checks, database connectivity, and ring heartbeats work; they cannot validate this admission mismatch. Account quota exhaustion reduces usable pool size but does not explain the contradictory warm/fresh admission. Interrupted upstream streams and reader cancellation warnings are separate observed symptoms, not established causes of this stall. Runtime lease counts at the historical incident were not captured; reproduction must establish the defect without asserting a lease leak.

## Goals / Non-Goals

**Goals:** correct warm admission at the configured cap; one coherent settings snapshot; no settings waits under pending/runtime locks; cancellation-safe ownership unchanged; verified failure at the public HTTP Responses surface.

**Non-Goals:** increasing caps, changing account quota rules, masking ownership failures, changing idle eviction/retry policy, adding diagnostics settings, or bundling unrelated PRs into a deployment.

## Decisions

- Replace the existing partial reacquire tuple with a small immutable typed snapshot containing effective partitioned caps, routing tunables, and fair-share threshold. Resolve from one cached dashboard row for both keyed and unkeyed sessions, outside `pending_lock`.
- Pass the snapshot explicitly into the locked lease helper. Remove its settings-reading fallback rather than retaining a footgun that can repeat #1971. A held lease or closed session remains a no-op; a real reacquire requires a snapshot. Already-leased submits do not load settings.
- Use the existing effective-cap resolver, preserving dashboard-over-env precedence, null inheritance, zero/unlimited, and replica partitioning. Do not cache caps on the session or change balancer defaults for tools/tests.
- Preserve all lease acquisition, fair-share accounting, closed-session race cleanup, and failed-submit settlement logic. A genuine full cap still returns the existing local-cap envelope.
- Test with separate startup and dashboard values, actual balancer lease bookkeeping, and an upstream stub. Never mock away the reacquire/lease decision in the regression. Cover public canonical/native paths and keyed/unkeyed turns, lower/unlimited/inherited caps, partitioning, settings changes, and failure/cancellation cleanup.

## Risks / Trade-offs

- Unkeyed idle reacquires now depend on the settings cache, just like initial selection. Resolve before locking and preserve registered-waiter cleanup if the read fails or is cancelled.
- A dashboard update is observed under the existing cache freshness contract, not an instantaneous new mechanism. Existing leases are not revoked by lower limits.
- Historical 502s are the eventual symptom after stalled admission and eviction; correcting admission does not promise to eliminate unrelated ownership failures under genuine saturation.

## Migration Plan

No database migration. Keep the patch compatible with the verified deployed candidate; test it on the candidate source in isolation as well as main. Before any authorized rollout, build/review an immutable candidate, verify source/image lineage and focused traffic on an isolated instance, preserve the current image/compose rollback, drain active work, and confirm a reused HTTP bridge succeeds with at least eight other leases under the configured 128 cap. No live changes are part of investigation/implementation authorization.
