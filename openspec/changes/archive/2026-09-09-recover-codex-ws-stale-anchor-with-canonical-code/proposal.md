# Recover Codex WebSocket stale anchors with the canonical error code

## Summary

On the Codex-native `/backend-api/codex/responses` WebSocket route, when an ephemeral `previous_response_id` anchor goes stale, codex-lb emits a nonstandard terminal `response.failed` classifier (`codex_previous_response_stale`). No unmodified Codex client recognizes that code, so the turn ends and only a client restart recovers. Change the sanitized signal on this route to the canonical `previous_response_not_found` code, with the raw upstream envelope and the missing `resp_...` id stripped, because that is the code unmodified clients already act on to retry once with full context.

## Why

The requirement `Codex WebSocket stale-anchor failures remain recoverable by a full-context retry` already intends the client to recover on a stable classifier. But standard Codex clients recover by matching the canonical error *code*, not by reading a message: the reference pi / pi-ai transport is confirmed from source to retry only on `error.code == "previous_response_not_found"` and to ignore any other code. The official Codex client's recovery on the same canonical code is confirmed from its own source, not just assumed by analogy (see `design.md`'s Load-bearing assumption). Emitting a proxy-specific code silently disables that built-in recovery, turning a recoverable continuity loss into a turn-ending error that needs a manual restart. A client cannot be expected to learn a proxy-specific code, so the fix belongs on the surface that deviated from the canonical contract.

This route already recovers transparently, without any client involvement, when the client's own payload happens to be a self-contained full resend (see `design.md`'s Context). The rename bug only reaches the client in the residual case that mechanism cannot cover — a delta-only continuation, exactly the shape pi/pi-ai reported — where codex-lb has no independently-reconstructable history to replay with and a client-actionable signal is the only remaining option.

The ChatGPT backend also emits an equivalent stale-anchor envelope that omits
`param` and uses the generic `invalid_request_error` code with the message
`Invalid previous_response_id.`. That shape currently bypasses the continuity
classifier, so neither transparent replay nor the canonical client signal runs.

The live canary exposed a second HTTP-bridge failure after classification was
fixed: a verified full-history resend can receive an explicit stale-anchor
rejection, rebind to the same owner account, then hit an eventless transport
drop and open the hard-key retry circuit. Because the explicit stale-anchor
error proves that the rejected attempt did not run, codex-lb can avoid that
same-owner loop by using the already-verified account-neutral full-resend
projection immediately after the rejection.

The production replay then showed the complementary case: the incoming request
was prefix-verified and trim-safe, but its retained tool history was not safe to
migrate across accounts. That payload still has a safe recovery on the same
owner: the explicit stale-anchor rejection proves the anchored attempt did not
run, so codex-lb can drop only the rejected anchor and replay the retained full
request once without changing accounts.

The second production replay reached that same-owner branch but was rejected
before submit because the hard-key retry circuit persisted across container
replacement. A stale-anchor recovery changes the request from anchored to
verified unanchored full history, so that internally constructed one-shot
replay may bypass the old circuit without deleting shared circuit state.

References: pi report [#1529](https://github.com/Soju06/codex-lb/issues/1529)
and the parameter-less ChatGPT backend report
[#1816](https://github.com/Soju06/codex-lb/issues/1816).

## What Changes

- On the Codex-native WebSocket route, stale-anchor continuity failures are surfaced with `error.code = "previous_response_not_found"`, sanitized to remove the raw upstream error envelope and the missing (stale) `previous_response_id`. This applies to both the mid-stream `response.failed` shape and the top-level wrapped `"type": "error"` shape used for connect-time failures (`_wrapped_websocket_error_event`); the latter had its own independent re-masking step that would otherwise have silently reverted the sanitized code back to `stream_incomplete` — see `design.md`'s Implementation guidance. On the `response.failed` shape, the current downstream response id is preserved for event correlation, as before; the top-level `"type": "error"` shape carries no response id field at all, sanitized or not, so there is nothing to preserve there.
- The nonstandard `codex_previous_response_stale` classifier is no longer used on this route.
- Public `/v1/responses` WebSocket clients keep the existing `stream_incomplete` masking; OpenAI-compatible clients do not expect the Codex continuity code.
- Sibling requirements that currently assert `stream_incomplete` masking for the Codex-native WebSocket route (`Codex WebSocket top-level previous-response errors are masked`, `Codex WebSocket wrapped errors follow official client shape`) are reconciled so their Codex-native scenarios use the canonical `previous_response_not_found` signal while their public `/v1` scenarios keep `stream_incomplete`. This delta modifies the two authoritative requirements; the owner drives the dependent wording per the centralized-continuity requirement.
- Add WebSocket-surface regression coverage asserting the client-visible code is `previous_response_not_found` with the stale `previous_response_id` and raw upstream envelope absent (the current response id may remain).
- Classify the exact `invalid_request_error` + `Invalid previous_response_id.`
  envelope as continuity loss when `param` is absent or names
  `previous_response_id`, without broadening recovery to unrelated invalid
  requests.
- When the HTTP bridge receives an explicit stale-anchor rejection for a
  verified, file-free, account-neutral full resend, remove the stale anchor,
  exclude the rejecting owner account, and perform the existing bounded replay
  on a fresh account-neutral bridge. Delta-only and unverified requests retain
  the current fail-closed behavior; verified file/account-bound history may
  replay only on the same owner.
- When the same explicit rejection occurs for a prefix-verified full resend
  that is not account-neutral (for example retained account-bound tool/file
  history), remove the stale anchor and replay the retained full request once
  on the same owner. Do not carry the rejected `previous_response_id` into the
  replacement socket.
- Allow only the internally marked, verified one-shot unanchored replay to pass
  an older hard-key retry circuit. Do not delete local or durable circuit state;
  transport-only failures and ordinary reconnects continue to observe it.
- Require an attached durable operation identity plus live session/owner fence
  before either stale-anchor replay may remove the anchor. If that fence or its
  spool-reset operation is unavailable, retain the anchor and fail closed.
- Do not let the verified replay's successful completion clear the pre-existing
  hard-key circuit; a later ordinary successful request may settle it normally.
- Treat an UNKNOWN recovery journal owned by an inactive durable session as an
  ambiguous prior dispatch: fail closed instead of claiming it, removing the
  anchor, or migrating accounts.
- Permit stale-anchor replacement only when the current request has not already
  used an eventless replay, regardless of whether the request carries a proven
  full resend.
- Keep explicit stale-anchor replacement independent from the ambiguous-
  transport recovery journal. Deterministic terminal settlement may consume
  that journal, while replacement ownership remains fenced by the durable
  operation identity and mandatory spool reset.
- Keep retry-circuit preservation independent from quarantine cleanup: a
  verified successful response retains the circuit but still clears quarantine.
- Reject present blank/whitespace `param` values instead of treating them as an
  absent parameter, and accept an exact stored pending-tool manifest as the
  same safe-context proof already used by durable full-resend classification.
- Preserve blank/whitespace `param` values through every shared WebSocket and
  HTTP-bridge extraction path so they cannot be rewritten into a canonical
  stale-anchor error later in the pipeline.
- Forbid clean-close and other transport retries of the internally verified
  replacement; its single dispatch exhausts the replay budget.
- Capture the hard-key retry-circuit generation when stale-anchor recovery is
  authorized and bypass only that generation. A newer local or durable failure
  appearing before submit must suppress the replacement.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `responses-api-compat`: Redefines the sanitized signal for Codex-native WebSocket stale-anchor failures as the canonical `previous_response_not_found` code, and scopes the "never leak raw upstream errors" masking to the raw envelope and the missing response id rather than to the bare code.

## Non-Goals

- No change to `upstream_unavailable` (owner-account-unavailable) or suppressed-duplicate `stream_incomplete` signaling; those share the same delivery problem but are a follow-up.
- No account migration based only on an eventless transport close; without an
  explicit stale-anchor rejection, upstream acceptance remains ambiguous.
- No conversion of an operation-journal transport recovery into an unanchored
  replay unless the current failure is an explicit stale-anchor rejection.
- No change to public `/v1/responses` masking (`stream_incomplete` is retained).
- No client-side change; the fix targets the deviating proxy surface only.
- No cross-account replay for uploaded files or payloads that fail the existing
  durable full-resend proof.
- No anchor removal for delta-only or prefix-unverified payloads.
