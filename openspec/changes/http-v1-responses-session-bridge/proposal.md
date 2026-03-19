## Why

`/v1/responses` over HTTP was still behaving like a stateless per-request upstream client, which caused unstable prompt-cache locality and made `previous_response_id` fail across sequential HTTP calls even when account affinity was preserved.

## What Changes

- add a server-side upstream websocket session bridge for HTTP `/v1/responses`
- reuse one upstream websocket session per stable bridge key instead of creating a fresh upstream session per eligible HTTP request
- preserve external HTTP/SSE contracts and `transport = "http"` logging while using the bridge internally
- keep a global kill switch for emergency rollback while avoiding per-request surrogate fallback in normal bridged execution

## Impact

- improves HTTP `/v1/responses` session continuity and prompt-cache stability
- changes proxy runtime behavior for HTTP `/v1/responses`
- requires focused regression coverage around reconnects, derived prompt-cache keys, and sequential `previous_response_id` flows
