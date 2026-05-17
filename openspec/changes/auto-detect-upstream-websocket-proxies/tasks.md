## 1. Proxy defaulting

- [x] 1.1 Auto-detect standard outbound proxy env vars for upstream websocket handshakes
- [x] 1.2 Preserve explicit `CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV=false` as a direct-connect override
- [x] 1.3 Add SOCKS runtime support for websocket handshakes

## 2. Coverage and docs

- [x] 2.1 Add regression coverage for env proxy precedence and `all_proxy` fallback
- [x] 2.2 Document the new websocket proxy default and override behavior

## 3. Verification

- [x] 3.1 Run targeted backend tests for websocket proxy resolution
- [ ] 3.2 Validate the OpenSpec delta locally
- [x] 3.3 Confirm a live OpenAI-compatible request succeeds with the provided proxy exports
