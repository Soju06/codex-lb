## Context

Responses requests can be served either by a subscription account or by a configured OpenAI-compatible model source. Subscription continuity already records the account that emitted a response in request logs and resolves that evidence through `_resolve_websocket_previous_response_owner`. Model-source ownership is established independently by `select_responses_model_source`. The current PR instead classifies long hexadecimal `resp_...` identifiers as subscription-owned, but that wire shape is also valid for OpenAI-compatible sources.

The HTTP routes decide source selection before entering subscription streaming, while direct WebSocket requests use source guards because model sources are reachable only over HTTP. Both paths therefore need the same precedence rule before they commit to a transport.

## Goals / Non-Goals

**Goals:**

- Preserve a recorded subscription owner for hard prior-response continuity.
- Preserve configured model-source routing when the source catalog confirms source ownership and no subscription owner is recorded, including for canonical OpenAI response IDs.
- Preserve the compatibility fallback for a known subscription-model owner miss only when exactly one eligible subscription account remains after API-key assignment scoping; fail closed when the count is zero or ambiguous.
- Keep HTTP and direct WebSocket decisions aligned.
- Retain strict file/account ownership and the subscription-only compaction boundary, while applying the same sole-candidate fallback to compact HTTP selection.
- Settle compact API-key reservations before owner-miss diagnostics, health writes, or exit.

**Non-Goals:**

- Add a new response-ownership table or migration.
- Make model sources reachable over the direct WebSocket transport.
- Change stale-anchor recovery or account failover semantics after subscription routing has been chosen.

## Decisions

### Treat ownership evidence as authoritative, not identifier syntax

The shared synchronous source-route predicate will cover only structural subscription constraints: Codex compaction and account-pinned file references. It will not inspect `previous_response_id` syntax.

When an HTTP request has a viable model-source candidate and a prior response ID, the route will query the existing subscription continuity resolver. A recorded account owner vetoes the source candidate. If the source catalog confirms source ownership and the lookup misses, the source candidate remains authoritative. For a known subscription model, a miss instead counts eligible subscription candidates after API-key assignment scoping: one candidate uses the existing compatibility fallback, while zero or multiple candidates fail closed. Compact uses this same cardinality rule within its subscription-only selection path. This keeps the extra lookup off requests that cannot source-route.

Alternative considered: retain or broaden the regular expression. Rejected because provider-generated IDs are opaque and canonical OpenAI-compatible sources may use exactly the same shape.

### Resolve direct WebSocket ownership before applying the source fallback guard

The reuse guard will run after the existing prior-response owner resolution. The reuse and connect guards will bypass HTTP fallback only when prior-response continuity resolved to a subscription account (or another existing structural exclusion applies). Otherwise a configured source model continues to emit `model_source_requires_http_transport`, allowing the client to retry through HTTP.

Alternative considered: persist a separate source-response-ID index. Rejected for this change because the configured model source already identifies the only available source route; the missing decision is whether recorded subscription ownership must veto it.

### Use marker shape for compatibility fallback

`turn_*` and `http_turn_*` values are proxy-shaped first-turn placeholders,
but their exact issuance history can disappear during HTTP bridge eviction,
process restart, or replica handoff. When no recorded previous-response owner
or file pin requires a specific account, a matching marker therefore follows
the existing sole-candidate compatibility path even when its alias is not
registered locally. Exact process-local issuance provenance is not a routing
precondition. Registered markers still resolve to their recorded owner;
non-synthetic values and physically present blank headers remain hard client
continuity input, and file ownership remains strict.

Alternative considered: require an exact handshake marker or a retained
process-local provenance bit before allowing fallback. Rejected because that
turns normal reconnect, eviction, and replica-boundary marker echoes into
false owner failures without adding an account-ownership proof. The
sole-candidate bound and independent hard-owner checks preserve the safety
boundary.

### Keep marker provenance additive to the v2 owner-forward signature

The existing `x-codex-bridge-signature-v2` field set is already a rolling-upgrade
wire contract. Adding the synthesized marker to that digest would make updated
origins incompatible with pre-marker owners whenever a forward also carries a
file-owner proof, because those owners reject an invalid tools-bound signature
before their legacy fallback. The marker is therefore authenticated by the
additive `x-codex-bridge-synthesized-turn-state-signature` header while the
existing v2 shape remains unchanged. Updated owners require both signatures
when a marker is present; pre-marker owners ignore the additive header and
continue to verify the original v2 digest.

### Preserve fail-closed lookup behavior

An owner miss and source-catalog unavailability are distinct states. Known subscription-model misses fail closed unless the sole-candidate compatibility fallback applies; source-owned HTTP requests remain source-routed. Source-catalog unavailability preserves the existing direct WebSocket subscription fallback. Owner errors use the sanitized `previous_response_owner_unavailable` contract. Compact settles any API-key reservation before emitting diagnostics or leaving the owner-miss path; a confirmed fail-safe release preserves the original owner error, while an unconfirmed settlement failure propagates.

### Restore the security retry exhaustion contract

The WebSocket selector excluded the authorized-pool exhaustion code from the
missing-pool handler. Connection failover could also defer that result, while
authentication replay cleared the original security error before reselection.
Preserve that error across authentication replay and route exhausted security
retries through the existing warning and terminal-error handler. Signal that
the terminal error was sent so the connect loop does not send a second one.

This restores the existing security retry contract without adding fallback
policy. An account-model rejection still returns its original 400 when no
replacement is available. Hard ownership, response creation, and visible
output still prevent unsafe account replay. Tests use both downstream
WebSocket routes and complete a later request on the same socket to verify
that the response-create gate was released.

## Risks / Trade-offs

- [A subscription response created outside this proxy has no local owner evidence] -> A source-owned HTTP request remains source-routed; a known subscription-model request uses the one-candidate compatibility fallback and otherwise fails closed, avoiding an account guess.
- [A client can present a marker-shaped turn state] -> Shape compatibility is limited to the existing sole-candidate owner-miss path; blank or non-synthetic input, file ownership, and a resolved previous-response owner still fail closed or remain pinned.
- [Moving the reuse guard changes its timing] -> Keep it before any upstream send and add direct WebSocket tests proving both subscription forwarding and source HTTP fallback.
- [HTTP and WebSocket logic could drift again] -> Both paths use the same model-source selector and the existing subscription-owner resolver; regression tests cover both transports and canonical response-ID shapes.

## Migration Plan

No data migration is required. Deploy as an application-only routing correction. Rollback is the prior code and spec revision.

## Open Questions

None.
