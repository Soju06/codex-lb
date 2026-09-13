## 1. Credentials out of aiohttp repr surfaces

- [x] 1.1 Add `ResolvedProxyEndpoint.aiohttp_proxy_kwargs()` (credential-free
  `proxy` + latin1 `Proxy-Authorization` header) and use it for routed aiohttp
  requests and websocket connects; reserve `proxy_headers` from callers
- [x] 1.2 Fail closed for credentialed routes to non-TLS targets (whole
  pool, before dispatch, ahead of every transport, as a connect-phase
  transport error) and for usernames containing `:` at the resolver
- [x] 1.3 Pin byte-identical CONNECT header, credential-free `ConnectionKey`
  repr and `ClientHttpProxyError` text with a fake CONNECT proxy

## 2. Rendered log redaction backstop

- [x] 2.1 Add never-throwing `redact_rendered_log_text` with URL userinfo and
  Basic token patterns and cheap prechecks; fold both into `_redact_log_value`
- [x] 2.2 Apply to text, access, and JSON formatters (message, exception,
  extras incl. secret-keyed fields of any type and extra keys); route
  `warnings.warn` through logging at server start
- [x] 2.3 Regression tests: exact production line (text and JSON), `https`
  variant, `BasicAuth` repr, exception traceback, never-throws, precheck
  short-circuit, bootstrap token byte-identity, mid-line `Authorization:`,
  cyclic/deep/unprintable extras (never-raise, incl. randomized nesting),
  yarl-shaped userinfo with unencoded sub-delims (`'`), Python-repr
  secret-keyed mappings (`%r` and the JSON formatter's text fallbacks)

## 3. Verification

- [x] 3.1 Run focused unit tests, ruff, ty, proxy architecture check
- [x] 3.2 Run strict scoped OpenSpec validation
