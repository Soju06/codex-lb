## Why

Codex CLI can run remote compaction through `POST /backend-api/codex/responses/compact`.
When upstream returns `401 invalid_api_key` after a forced token refresh, the
current compact path surfaces that 401 to the client. Codex treats the
compaction as failed and may retry the same remote compact task repeatedly,
turning one invalidated account token into a noisy compact failure loop.

Compaction happens before any downstream response body is emitted, so the proxy
can safely move to another eligible account after proving the selected account
still cannot compact with a refreshed token.

## What Changes

- Keep the existing same-account forced refresh on the first compact 401.
- If the refreshed retry also returns 401, mark the selected account through the
  normal proxy error handling path, exclude it from this compact request, and
  try another eligible account.
- Do not classify raw compact HTTP 401 responses as generic same-contract
  transport retries.
- Add regression coverage for repeated compact 401 failover.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: compact auth failure handling and failover behavior.

## Impact

- **Code**: `app/core/clients/proxy.py`, `app/modules/proxy/service.py`
- **Tests**: `tests/integration/test_proxy_compact.py`
- **Behavior**: repeated invalidated-token 401s on one account no longer surface
  immediately to Codex compact callers when another eligible account can satisfy
  the request.
