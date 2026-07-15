# Design: persist dashboard OAuth flow state

## Problem

`OAuthStateStore` (`app/modules/oauth/service.py`) holds all OAuth flow state in
a process-local dict `_flows` (plus a `state_token -> flow_id` index). Three
externally reachable paths must find that state:

1. **Browser callback** (`GET /auth/callback` on the localhost callback server)
   — needs the PKCE `code_verifier` for the `state` token in the redirect.
2. **Manual callback** (`POST /api/oauth/manual-callback`) — the documented
   remote-access path where the operator pastes the callback URL into the
   dashboard; it flows through the load balancer to any replica.
3. **Status / complete polling** (`GET /api/oauth/status`,
   `POST /api/oauth/complete`) — polled through the load balancer.

Behind a load balancer any of these can hit a replica that never ran
`start_oauth`, so the in-memory lookup misses and the flow fails.

## Mechanism

Introduce a durable `oauth_flow_states` table and an `OAuthFlowRepository`. The
DB becomes the source of truth for the persistable flow record; the in-process
`OAuthStateStore` is retained only for runtime handles that cannot be
serialized — the localhost `OAuthCallbackServer` and the in-process device
`poll_task` — and as a same-process fast path.

- **Create**: `_start_browser_flow` / `_start_device_flow` persist the record
  (verifier encrypted) after remembering it locally.
- **Status writes**: `_set_success` / `_set_error` and the device
  not-initialized error path write the terminal status + `finished_at` to the DB.
- **Cross-replica reads**: `oauth_status(flow_id)` reads the authoritative
  status from the DB when a row exists (fixing the stale-`pending` bug on the
  originating replica); `manual_callback` / `_handle_callback` load the flow by
  `state` token from the DB and hydrate the local store when it is missing;
  device `complete_oauth` hydrates by `flow_id` and starts a local poll task.
- **Encryption at rest**: the PKCE `code_verifier` is stored via
  `TokenEncryptor` (same Fernet key already used for account tokens); no
  plaintext verifier is written to the DB.

The verifier existing transiently in the in-RAM `OAuthState` while also being
persisted (encrypted) is a runtime handle over the single durable copy, not a
second persistent representation.

## TTL / cleanup

Pending browser flows carry a 15-minute TTL (`expires_at`); device flows carry
the upstream `expires_in_seconds`. On read, an expired pending flow is treated
as absent. On write (flow creation) the repository opportunistically deletes
pending rows past `expires_at` and bounds retained terminal rows to the newest
`_MAX_RETAINED_TERMINAL_OAUTH_FLOWS`. This mirrors the previous in-memory prune
semantics and needs no new scheduler; a leader-gated periodic purge can be added
later if row volume ever warrants it.

## SQLite vs PostgreSQL

- The table uses portable column types (`String`, `Text`, `LargeBinary`,
  `DateTime`, `Integer`) and no dialect-specific DDL, so the single migration
  runs identically on both backends.
- On **PostgreSQL** (the only supported multi-replica backend) the shared table
  is what lets a callback/poll on replica B complete a flow started on replica
  A.
- On **SQLite** (single-process, per the replica-operations contract) the table
  still works and simply persists flow state across the one process; it also
  makes the flow survive a mid-flow worker restart. Timestamps are stored as
  naive UTC via `utcnow()`, matching every other table.

## Rejected alternatives

- **Sticky sessions / IP hash at the LB**: fragile, operator-dependent, and does
  not survive replica restarts mid-flow; the project already coordinates all
  cross-replica state through the shared DB.
- **Broadcast the verifier over the cache-invalidation bus**: the bus is a
  best-effort cache signal, not durable storage; a callback arriving before the
  signal propagates would still fail.
- **Full rewrite removing the in-process store**: the callback server and device
  poll task are inherently process-local (a socket and an asyncio task); they
  cannot be persisted, so a hybrid (durable record + local runtime) is required
  regardless.
