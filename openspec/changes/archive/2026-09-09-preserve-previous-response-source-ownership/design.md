## Context

Responses requests can be served either by a subscription account or by a configured OpenAI-compatible model source. Subscription continuity already records the account that emitted a response in request logs and resolves that evidence through `_resolve_websocket_previous_response_owner`. Model-source ownership is established independently by `select_responses_model_source`. The current PR instead classifies long hexadecimal `resp_...` identifiers as subscription-owned, but that wire shape is also valid for OpenAI-compatible sources.

The HTTP routes decide source selection before entering subscription streaming, while direct WebSocket requests use source guards because model sources are reachable only over HTTP. Both paths therefore need the same precedence rule before they commit to a transport.

## Goals / Non-Goals

**Goals:**

- Preserve a recorded subscription owner for hard prior-response continuity.
- Preserve configured model-source routing when no subscription owner is recorded, including for canonical OpenAI response IDs.
- Keep HTTP and direct WebSocket decisions aligned.
- Retain existing compaction and file-pin exclusions.

**Non-Goals:**

- Add a new response-ownership table or migration.
- Make model sources reachable over the direct WebSocket transport.
- Change stale-anchor recovery or account failover semantics after subscription routing has been chosen.

## Decisions

### Treat ownership evidence as authoritative, not identifier syntax

The shared synchronous source-route predicate will cover only structural subscription constraints: Codex compaction and account-pinned file references. It will not inspect `previous_response_id` syntax.

When an HTTP request has a viable model-source candidate and a prior response ID, the route will query the existing subscription continuity resolver. A recorded account owner vetoes the source candidate; a miss leaves the configured source candidate intact. This keeps the extra lookup off requests that cannot source-route.

Alternative considered: retain or broaden the regular expression. Rejected because provider-generated IDs are opaque and canonical OpenAI-compatible sources may use exactly the same shape.

### Resolve direct WebSocket ownership before applying the source fallback guard

The reuse guard will run after the existing prior-response owner resolution. The reuse and connect guards will bypass HTTP fallback only when prior-response continuity resolved to a subscription account (or another existing structural exclusion applies). Otherwise a configured source model continues to emit `model_source_requires_http_transport`, allowing the client to retry through HTTP.

Alternative considered: persist a separate source-response-ID index. Rejected for this change because the configured model source already identifies the only available source route; the missing decision is whether recorded subscription ownership must veto it.

### Preserve fail-closed lookup behavior

Ownership lookup failures continue to surface through the existing sanitized `ProxyResponseError` paths. They do not silently select a source. A lookup miss is not a failure: without account-owner evidence, the configured source remains authoritative.

## Risks / Trade-offs

- [A subscription response created outside this proxy has no local owner evidence] -> A configured model source remains selected; this avoids guessing an account and matches the explicit source configuration.
- [Moving the reuse guard changes its timing] -> Keep it before any upstream send and add direct WebSocket tests proving both subscription forwarding and source HTTP fallback.
- [HTTP and WebSocket logic could drift again] -> Both paths use the same model-source selector and the existing subscription-owner resolver; regression tests cover both transports and canonical response-ID shapes.

## Migration Plan

No data migration is required. Deploy as an application-only routing correction. Rollback is the prior code and spec revision.

## Open Questions

None.
