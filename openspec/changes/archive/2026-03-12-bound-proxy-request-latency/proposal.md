## Why

Streaming `/v1/responses` requests can currently wait far too long when the upstream Codex/Responses server is unstable. The request path combines a 300 second stream idle timeout with up to three attempts, plus separate connect and token-refresh timeouts. In practice one client request can spend more than five minutes waiting before it finally receives `response.failed`.

The proxy is also too defensive about generic upstream failures: a stalled or unavailable upstream can trigger repeated request attempts and increment account transient error state even when the problem is not account-specific.

## What Changes

- add a configurable request-scoped budget for streaming Responses requests
- reduce default connect / idle / token-refresh timeouts so unstable upstream paths fail fast
- retry only account-recoverable streaming failures instead of retrying generic upstream failures
- stop applying account error penalties for generic transient upstream failures such as stalled streams or upstream transport failures

## Impact

- Code: `app/core/config/settings.py`, `app/core/auth/refresh.py`, `app/core/clients/proxy.py`, `app/modules/proxy/service.py`
- Tests: `tests/unit/test_proxy_utils.py`, `tests/integration/test_proxy_api_extended.py` (and/or related proxy response coverage)
- Specs: `openspec/specs/responses-api-compat/spec.md`
