# Tasks: persist dashboard OAuth flow state

## 1. Schema

- [x] 1.1 Add `OAuthFlowState` ORM model (`oauth_flow_states`) to `app/db/models.py`.
- [x] 1.2 Add Alembic migration on the current head
      (`20260713_040000_add_account_refresh_claims`) with upgrade + downgrade,
      dialect-agnostic DDL.

## 2. Repository

- [x] 2.1 Add `OAuthFlowRepository` (`app/modules/oauth/repository.py`) with
      create / get-by-flow-id / get-by-state-token / latest / set-status /
      purge-expired methods.
- [x] 2.2 Encrypt the PKCE verifier at rest; expose a typed record dataclass.

## 6. Cross-replica hardening (review-driven)

- [x] 6.1 Make terminal status writes atomically monotonic (durable `success`
      is sticky) via a conditional SQL UPDATE, safe under two-session concurrency.
- [x] 6.2 Enforce the pending-flow TTL uniformly, including on the originating
      replica that still holds local state (prune expired local flows before
      trusting the cached verifier on callback/manual-callback).
- [x] 6.3 Coordinate device flows through a single atomic active slot
      (`oauth_device_flow_slots`): device `start` claims the slot with one
      conditional UPSERT (atomic replacement, exactly one current flow); the
      poller atomically consumes the slot before persisting tokens and aborts
      if superseded. Migration `20260716_000000_add_oauth_device_flow_slots`
      parented on the current single head.
- [x] 6.4 Route every entry point through the durable reconciliation gate so a
      durable terminal/expired always wins over local pending (status, complete,
      browser callback, manual callback, device).
- [x] 6.5 Converge the device-flow class on slot ownership: claim only while
      still the current local flow and under the store lock (a superseded
      same-replica start installs no stale slot pointer / no poller); ALL
      terminal writes (success and error) gated on holding the consumed slot (a
      loser writes nothing); the originating replica is the sole poller and a
      non-originating `/complete` reports durable status without spawning a
      second poller.
- [x] 6.6 Propagate rejected durable terminal-error writes: browser/manual
      callback error branches honor a durable `success` (report success, do not
      leave local `error`) when the monotonic guard rejects the error write for a
      single-use code already completed by a racing callback.

## 3. Service wiring

- [x] 3.1 Persist flow records at creation (browser + device).
- [x] 3.2 Write terminal status transitions to the DB.
- [x] 3.3 Make `oauth_status` DB-authoritative when a row exists.
- [x] 3.4 Hydrate the local store from the DB on manual-callback, browser
      callback, and device complete when the flow is missing locally.
- [x] 3.5 Allow injecting a distinct store per `OauthService` (two-replica tests).

## 4. Tests

- [x] 4.1 Two-replica simulation: start on store/session A, complete on B.
- [x] 4.2 Cross-replica status: originator sees success written by another replica.
- [x] 4.3 Migration upgrade/downgrade round-trip on SQLite.
- [x] 4.4 Existing oauth flow suite stays green.

## 5. Spec + validation

- [x] 5.1 Add delta requirement to `replica-operations`.
- [x] 5.2 `openspec validate persist-oauth-flow-state --strict` passes.
- [x] 5.3 `openspec validate --specs` stays green.
