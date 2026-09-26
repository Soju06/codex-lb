## Why

A second review reproduced seven gaps in the custom Responses source pool: fresh collaboration requests are rejected, no-candidate routing bypasses ownership, historical/expired evidence can lose credential ownership, call IDs are unchecked, identical token updates break continuity, and request overrides change references after validation. These gaps affect the intended five-key setup and production's shared PostgreSQL backends.

## What Changes

- Preserve supported direct-source requests with collaboration namespaces and neutral generation controls while keeping unsafe replay prohibited.
- Resolve known source ownership even when no eligible source remains; validate the final forwarded reference set.
- Protect historical and expired response ownership against conflicting publications and credential changes.
- Track unresolved tool call references without making self-contained call/result history needlessly nonportable.
- Preserve continuity when the upstream token is unchanged.
- Turn the twelve reproduced cases into regression coverage and verify cross-backend PostgreSQL behavior, migration/retention safety, cancellation and accounting cleanup.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-source-routing`: complete source-pool admission compatibility and durable credential ownership across routing misses, history, expiry, tool references and overrides.

## Impact

Proxy source selection/dispatch, source ownership storage, source credential updates, historical request logs, retention and potentially an additive migration. Existing source tools and client API routes remain supported. No new operator setting, global concurrency coordinator, commit, push or production deployment is included. Application work begins only after this plan is ready; the requested implementation agent is `gpt-6-luna` at `max` effort, subject to actual model availability.
