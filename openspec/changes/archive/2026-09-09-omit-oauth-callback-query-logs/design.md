## Context

`OAuthCallbackServer` binds a loopback-only `aiohttp` application for `/auth/callback`. Its `web.AppRunner` currently uses aiohttp's default access logger, whose request target includes the complete query string. OAuth providers place the short-lived authorization code and anti-CSRF state token in that query, so the generic access record crosses a secret-bearing boundary before the existing text or JSON formatter runs.

## Goals / Non-Goals

**Goals:**

- Keep authorization-code and state values out of every callback access-log sink.
- Preserve the real callback request route, handler result, and server lifecycle.
- Prove the boundary with a real loopback callback request.

**Non-Goals:**

- Changing global aiohttp, Uvicorn, or application logging policy.
- Redacting arbitrary URLs after they have entered a log record.
- Changing OAuth exchange, state validation, or browser response behavior.

## Decisions

### Disable access logging on the callback-only runner

Construct the callback runner with `access_log=None`. This prevents the secret-bearing raw request target from entering logging at all and is the narrowest control available at the boundary.

A callback-specific access logger that reconstructs method, fixed path, and status was rejected. It would preserve low-value loopback traffic metadata while adding a second formatter and a future route for query text to regress into logs. Global URL redaction was rejected because it broadens scope and still accepts secrets into the logging pipeline before redaction.

### Exercise the real server seam

The regression starts `OAuthCallbackServer` on an ephemeral loopback port, sends a callback containing distinct code and state sentinels, and asserts both successful handler behavior and absence from captured `aiohttp.access` records. Formatter-specific text and JSON behavior is covered by real-surface QA because the product fix must prevent record creation independent of output formatting.

## Risks / Trade-offs

- [Loss of one generic callback access record] -> Existing OAuth outcome handling remains authoritative; no operator contract requires raw loopback request access records.
