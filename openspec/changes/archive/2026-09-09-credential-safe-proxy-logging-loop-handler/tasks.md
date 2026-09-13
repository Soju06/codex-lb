## 1. Loop exception handler

- [x] 1.1 Add idempotent `install_redacting_loop_exception_handler` that
  redacts context reprs and delegates to the previous/default handler
- [x] 1.2 Install once at lifespan start; test byte-identical output for
  secret-free contexts, exploding reprs, chained handlers and unretrieved
  task exceptions under asyncio and uvloop

## 2. Dashboard colon-username hardening

- [x] 2.1 Reject `:` in usernames at endpoint creation with
  `invalid_proxy_username`
- [x] 2.2 Report resolver rejections from the endpoint test route as a failed
  probe instead of an unhandled 500

## 3. Direct websocket InvalidProxy hardening

- [x] 3.1 Use the fixed credential-safe message for `InvalidProxy` under
  every policy; log only the URL-free reason
- [x] 3.2 Update the Responses `InvalidProxy` test and the realtime spec

## 4. Verification

- [x] 4.1 Run focused unit and integration tests, ruff, ty, proxy
  architecture check
- [x] 4.2 Run strict scoped OpenSpec validation
