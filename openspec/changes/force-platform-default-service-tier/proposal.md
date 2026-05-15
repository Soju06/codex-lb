## Why

OpenAI Platform requests should not consume or request priority tier from upstream, even when clients or API keys ask for priority service. The current behavior only downgrades the legacy `fast` alias, so explicit or enforced `priority` can still reach Platform upstreams.

## What Changes

- Force all OpenAI Platform Responses and compact upstream payloads to use `service_tier: "default"`.
- Preserve existing client-visible requested tier logging so operators can still see when a client or API key asked for `priority`.
- Keep ChatGPT-web behavior unchanged; this change only applies to `openai_platform` provider requests.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: Platform-routed Responses and compact requests must always forward `service_tier: "default"` upstream.
- `api-keys`: Enforced API key service tiers must still drive reservation/logging, but Platform upstream forwarding must use `default`.

## Impact

- Code: Platform forwarding in `app/core/openai/requests.py`, `app/modules/proxy/service.py`, `app/modules/proxy/api.py`, and `app/modules/proxy/provider_adapters.py`.
- Tests: Platform proxy integration tests and request/provider unit tests covering explicit and enforced priority tiers.
- Specs: Delta requirements for `responses-api-compat` and `api-keys`.
