## Why

Codex CLI can run remote compaction through `POST /backend-api/codex/responses/compact`.
When upstream returns `401 invalid_api_key` after a forced token refresh, the
current compact path surfaces that 401 to the client. Codex treats the
compaction as failed and may retry the same remote compact task repeatedly,
turning one invalidated account token into a noisy compact failure loop.

Compaction happens before any downstream response body is emitted, so the proxy
can safely move to another eligible account after proving the selected account
still cannot compact with a refreshed token.

The same invalidated-token pattern exists on several other pre-response proxy
surfaces. Once a request has retried with a refreshed token and still gets a
401 before any downstream-visible output, the selected account is the bad local
state, not a client-visible final answer.

## What Changes

- Keep the existing same-account forced refresh on the first compact 401.
- If the refreshed retry also returns 401, mark the selected account through the
  normal proxy error handling path, exclude it from this compact request, and
  try another eligible account.
- Apply the same post-refresh 401 failover contract to pre-visible stream
  attempts, Codex thread goal requests, Codex control requests, transcription,
  file create/finalize calls, upstream websocket connect retries, and HTTP
  bridge session create/reconnect handshakes.
- Do not classify raw compact HTTP 401 responses as generic same-contract
  transport retries.
- Add regression coverage for repeated auth 401 failover on compact and the
  other affected proxy surfaces.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: account-local auth failure handling and failover
  behavior.

## Impact

- **Code**: `app/core/clients/proxy.py`, `app/modules/proxy/service.py`
- **Tests**: `tests/integration/test_proxy_compact.py`,
  `tests/integration/test_proxy_responses.py`,
  `tests/integration/test_proxy_api_extended.py`,
  `tests/integration/test_proxy_transcriptions.py`,
  `tests/integration/test_proxy_files.py`
- **Behavior**: repeated invalidated-token 401s on one account no longer surface
  immediately to pre-visible proxy callers when another eligible account can
  satisfy the request.
