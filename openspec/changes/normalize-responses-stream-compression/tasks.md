## 1. Regression coverage

- [x] 1.1 Add a failing unit test proving streamed requests normalize inbound `Accept-Encoding` before native and fallback dispatch.

## 2. Implementation

- [x] 2.1 Normalize streamed request headers once in `CodexClient.request_with_route_metadata`.
- [x] 2.2 Keep non-streaming request compression behavior unchanged.

## 3. Verification

- [x] 3.1 Run the focused Codex client tests.
- [x] 3.2 Run the relevant native egress and proxy stream tests (native helper execution is unavailable on Windows and is validated in the Linux image).
- [x] 3.3 Run strict OpenSpec validation.
- [ ] 3.4 Build and deploy the image through the detached rollback-safe deployment flow.
