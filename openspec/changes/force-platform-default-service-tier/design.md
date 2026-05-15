## Context

OpenAI Platform forwarding currently has provider-specific handling for the legacy `fast` service-tier alias: the request keeps canonical `priority` for local validation and logging, while the Platform upstream payload receives `default`. Explicit `priority` and API-key-enforced `priority` still reach Platform upstreams.

The code has several Platform forwarding paths:

- non-streaming Responses through `ProxyService.create_platform_response`
- streaming Responses through `ProxyService.stream_platform_response_events`
- compact Responses through `OpenAIPlatformProviderAdapter.compact_response`
- direct route handling in `app/modules/proxy/api.py`

## Goals / Non-Goals

**Goals:**

- Ensure every `openai_platform` upstream Responses and compact payload sends `service_tier: "default"`.
- Preserve local requested-tier observability and API key reservation behavior.
- Keep ChatGPT-web upstream behavior unchanged.

**Non-Goals:**

- Do not remove `priority` support from ChatGPT-web routes.
- Do not change dashboard API key CRUD normalization.
- Do not change service-tier pricing tables.

## Decisions

1. Add a request-level helper for Platform forwarding.

   `ResponsesRequest.platform_forwarded_service_tier()` and `ResponsesCompactRequest.platform_forwarded_service_tier()` will return `default` for Platform provider forwarding. This centralizes the policy where existing Platform call sites already look for provider-specific forwarded tier values.

   Alternative considered: strip `service_tier` from Platform payloads. Rejected because the requested contract is to send `default`, and existing logs/cost fallback code already uses the forwarded request tier when upstream omits an echo.

2. Keep local normalized request tier as-is.

   Client `fast` still normalizes to local `priority`, and enforced API key service tiers still set the request payload before reservations/logging. Only the Platform forwarded tier changes.

   Alternative considered: normalize all incoming `priority` to `default`. Rejected because it would also change ChatGPT-web behavior and API key reservation semantics.

## Risks / Trade-offs

- Platform fallback cost logs will record `service_tier: "default"` when upstream omits a tier, even if the client requested `priority`. This is intentional for billable Platform behavior; requested tier remains visible in `requested_service_tier`.
- Any existing operator expecting enforced API key priority to affect Platform upstream behavior will see standard-tier Platform requests instead. This matches the requested provider-specific ban on Platform priority use.
