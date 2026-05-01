## 1. Spec

- [x] 1.1 Add peer fallback runtime requirements for local pre-output proxy failures
- [x] 1.2 Define loop-prevention, transport scope, and process-down non-goals
- [x] 1.3 Validate OpenSpec changes

## 2. Tests

- [x] 2.1 Add HTTP proxy coverage for fallback before downstream-visible output
- [x] 2.2 Add SSE proxy coverage for fallback before the first downstream event or body byte
- [x] 2.3 Add coverage proving fallback is not attempted after response headers, body bytes, SSE events, or websocket messages are sent downstream
- [x] 2.4 Add coverage proving peer-forwarded requests cannot fallback again or loop between peers
- [x] 2.5 Add coverage for disabled or unconfigured peer fallback preserving current local failure behavior
- [x] 2.6 Add coverage documenting websocket peer fallback as deferred or disabled unless explicitly implemented

## 3. Implementation

- [x] 3.1 Add opt-in peer fallback configuration with bounded peer attempts and request timeouts
- [x] 3.2 Track whether downstream-visible output has started for HTTP and SSE proxy flows
- [x] 3.3 Forward eligible pre-output HTTP and SSE failures to a configured peer while preserving client auth, request method, path, body, and streaming semantics
- [x] 3.4 Add loop-prevention markers for peer fallback attempts and fail closed on repeated peer fallback markers
- [x] 3.5 Emit observability fields for fallback eligibility, selected peer, peer outcome, and no-fallback reasons
- [x] 3.6 Keep websocket peer fallback disabled or explicitly gated behind separate validation
