# Model-source Responses WebSocket

In Settings → Model Sources, enable **Responses WebSocket** only for an endpoint verified to implement native Responses WebSocket. The option starts off and requires Responses plus Streaming. HTTP SSE support alone is insufficient.

Clients connect to `/v1/responses` or `/backend-api/codex/responses` and send sequential `response.create` messages. A connection stays bound to one source credential, model and revision. Each turn revalidates key policy and settles usage separately. Changing source identity or switching between subscription and source routing requires reconnecting. HTTP and WebSocket continuation use the same durable ownership evidence.

HTTP-only sources keep the `model_source_requires_http_transport` error. Explicit global HTTP policy remains authoritative. Mixed-capability pools keep conservative catalog preferences. There is no SSE-to-WebSocket conversion.

Before enabling a provider, check endpoint/auth compatibility, two-turn reuse, tools, error envelopes, cancellation, quota settlement and client fallback in staging. Long responses may defer pong handling; first-frame, stream-idle and turn budgets still bound stalled work. Pending pong futures do not accumulate.

Roll out compatible code and additive migrations to every replica before enabling a source. Disable its capability and reconnect clients before reverting to an older runtime.

Requirements: [model-source-websocket](../openspec/specs/model-source-websocket/spec.md) and [model-source-routing](../openspec/specs/model-source-routing/spec.md).
