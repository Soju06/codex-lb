# publish-http-response-owner context

Purpose and implementation boundary are in the [proposal](proposal.md) and [design](design.md); the delta is the normative contract. This is one independently reviewable part of issue #2029, based on pinned main `aec4d7b7f66128ece52c09398c546fddea260d94`.

The controlled investigation used the real application with temporary SQLite, two synthetic Pro accounts and a local scripted upstream. It measured local transport/serialization/persistence work, not real model inference or WAN delays. Healthy retained WebSocket calls stayed on one upstream connection. Historical multi-second/minute waits remain unattributed; no blanket speedup is promised.

A concrete acceptance example is the regression in task1.1: replace only the external nondeterministic boundary or observe the owning seam, then exercise unchanged production behavior. Do not substitute a fake selection, persistence or rendering path for the layer named by the test. No additional configuration, operator migration or live rollout is part of this change.

Related independent changes are `prevent-ssl-starvation-false-reclaim`, `reuse-direct-wss-system-trust`, `publish-http-response-owner`, `observe-http-upstream-latency`, and `skip-unused-http-preparation-serialization`. The architectural narrative is owned by `proxy-runtime-observability/context.md` in the timing scope. A later local integration build combines accepted heads and produces one wheel; it is not another feature scope.

Local failure provenance belongs to this change. An oversized first SSE frame and a generic parser failure can produce `response.failed` with the request ID even when no accepted upstream event exists. Their error codes are outside the transport-marker set. The internal `ParsedSseBlock.is_local` flag prevents these IDs from becoming owners without changing serialized errors or retry policy. HTTP timing consumes the same producer distinction through a normal merge of this owner branch.
