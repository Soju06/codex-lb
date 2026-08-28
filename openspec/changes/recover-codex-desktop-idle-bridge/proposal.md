## Why

A production Codex Desktop request on the HTTP-to-WebSocket bridge remained pending for nearly an hour after its upstream `response.create` send produced neither `response.created` nor any matched `response.*` lifecycle event. Existing stuck-gate recovery runs only when another request later times out waiting for the gate, so a lone request can outlive the native client's 300-second parsed-event idle timeout. During the same wedge, the backend route classified the native Desktop request as an OpenAI SDK stream from its payload shape and emitted SSE comments that the Codex parser does not observe.

## What Changes

- Record the monotonic time of the current upstream `response.create` send.
- Proactively expire an eventless request that remains pre-`response.created` for the smaller of the existing stuck-gate threshold and 240 seconds, even when no second gate waiter exists and periodic keepalives are disabled.
- Fail the affected bridge session closed through existing terminal settlement and retirement paths, without transparent replay, account movement, or account-health penalties.
- Give verified native Codex identity parser-visible `codex.keepalive` frames even when payload-shape heuristics still require OpenAI-compatible event normalization; explicit SDK markers and public `/v1/responses` retain comment liveness.
- Add regressions for the no-waiter deadline, protected created/eventful requests, account-neutral retirement, and contrasting Desktop/SDK/public heartbeat contracts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: Add a proactive, fail-closed deadline for an eventless response-create gate owner without requiring another waiter.
- `responses-api-compat`: Require verified native Codex Desktop HTTP streams to receive parsed-event liveness frames without weakening SDK normalization.

## Impact

- Affected code: HTTP bridge request send timing, upstream-reader timeout/retirement, backend Responses client identity, and SSE keepalive selection.
- Affected surfaces: `POST /backend-api/codex/responses` and its server-side upstream WebSocket bridge.
- No new setting, dependency, database migration, public endpoint, retry circuit, durable coordinator, or OpenAI SDK contract change.
- The change is independent of PR #1394. It fixes the observed eventless/no-waiter wedge but intentionally does not add #1394's transparent replay, clean-close retry, or cross-replica cooldown behavior.
