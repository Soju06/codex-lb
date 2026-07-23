# Change: Extend created-only WebSocket replay budget

## Why

Direct Responses WebSocket upstreams can close repeatedly after `response.created`
but before any visible output or numeric sequence-bearing frame. Treating this as
a single replay opportunity still exposes transient created-only EOFs to Codex
clients even though no downstream-visible generation has started.

## What changes

- Allow a bounded series of transparent direct-WebSocket replays for
  sequence-free, created-only upstream closes.
- Keep the existing no-replay boundary after any finite integer
  `sequence_number` is sent downstream.
- Preserve the same cleanup contract for response-create admission, account
  leases, API-key reservations, heartbeats, and request logs.

## Impact

- Affected code: direct Responses WebSocket replay eligibility and cleanup.
- Affected tests: direct `/backend-api/codex/responses` WebSocket retry
  regressions.
- Compatibility: clients see fewer transient transport failures before visible
  output, while post-exposure replay remains forbidden.
