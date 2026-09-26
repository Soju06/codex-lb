## Context

See proposal.md. The existing source pool uses finish-time request logs and only detects previous_response_id. Overflow pin primitives have separate lifecycle contracts and remain untouched.

## Goals / Non-Goals

Goals: preserve source ownership of delivered state and prevent unsafe replay, including across replicas and database latency. Non-goals: changing subscription routing, global load coordination, or adding operator settings.

## Decisions

- Add a direct-source ownership table keyed by a SHA-256 digest of API-key identity, public model, reference kind and value. Store source ID, transport/credential fingerprint and expiry, never raw ciphertext. Atomic conditional upserts refresh the same owner and reject conflicting owners. Do not repurpose overflow pins or accounting logs: their retention, overwrite and timing semantics differ.
- Publish response and item references before their event frame is yielded. Publish all JSON result references before returning JSON. Bind externally supplied conversation/state after successful single-source completion. Each write owns its DB session and transaction; cancellation is deferred until commit and then propagated. No detached work or shared sessions.
- Resolve every known reference from shared storage; historical response IDs can fall back to request logs. Reject conflicting owners, unavailable/changed owners, and unowned multi-source state. Use the existing positive replay-safety classifier for requests without anchors. Single-source compatibility remains available for externally created state; successful use bootstraps durable ownership for later pooling.
- Keep state for 30 days after use, with bounded per-response reference tracking and indexed batch cleanup in the existing retention pass. Expired or missing evidence never authorizes guessing a pool credential.
- Disable aiohttp redirects only for Responses. Return a non-retryable 502 on 3xx, rather than attempting to infer whether a previous redirect hop executed a generation.

## Risks / Trade-offs

- Additional durable writes before state-bearing frames → batch references per frame and skip already recorded references. Inspect delta/content frames as well: synthesized deltas can expose a new item ID before the terminal envelope. Ordinary repeated deltas and heartbeats need no writes.
- Unknown pre-upgrade encrypted state → fail closed in pools; scope a client key to the known source to bootstrap, or send portable full context.
- Credential replacement invalidates source-owned state → retain fingerprints and require fresh portable context rather than silently crossing credentials.
- Output collision across providers → conditional claims fail closed before exposing the conflicting reference.

## Migration Plan

Add one migration on the current sole Alembic head; test downgrade/upgrade and schema drift. Existing response logs provide historical compatibility without inventing ownership for opaque state. Production uses shared PostgreSQL behind blue/green/amber HAProxy backends. The migration is additive and preserves the old tables. Roll out all replicas via the existing HA surge script before enabling multiple eligible sources for a model or relying on the new record-before-delivery guarantee; older binaries do not publish ownership or obey source pools. If multiple sources are already configured, scope client keys to their known original source during rollout. No production settings are changed by this implementation task. Downgrade removes only direct-source ownership records; application rollback restores the prior documented limitations.
