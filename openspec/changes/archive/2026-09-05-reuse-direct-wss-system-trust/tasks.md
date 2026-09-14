## 1. Establish the regression

- [x] 1.1 Add a repeated-real-WSS-open red check, preserving trusted success/untrusted and wrong-host rejection; reuse existing plain-WS and cancellation coverage.
- [x] 1.2 Record the existing nearest coverage and demonstrate that the new check fails when the claimed production behavior is severed.

## 2. Implement the owning change

- [x] 2.1 Add the private system-default cache, normal lifecycle warm/reset, and conditional Python WSS TLS argument without changing aiohttp, routed or native policy.
- [x] 2.2 Prove cache/call-site sensitivity by disabling reuse; verify close/reinitialize and current verification policy without a trust-matrix expansion.

## 3. Validate and prepare delivery

- [x] 3.1 Run `uv run pytest tests/unit/test_http_client.py tests/unit/test_proxy_websocket_client.py tests/unit/test_websocket_upstream_transport_observability.py -q`, extending the selection to include any new regression node outside the current names; run `make lint` and `make typecheck`.
- [x] 3.2 Run OpenSpec 1.11.0 strict validation for `reuse-direct-wss-system-trust` and `outbound-http-clients`, CI-equivalent `validate --specs`, and full `validate --specs --strict`; record the 22 unchanged main-spec placeholder warnings from full strict validation.
- [x] 3.3 Refresh the exact-root GitNexus index/content witness, run `gitnexus detect-changes --scope all --repo /Users/dpearson/repos/codex-lb/.agents/worktrees/issue-2029-wss`, inspect the scoped diff and commit one cohesive implementation change locally.
- [x] 3.4 Supply the accepted WSS head to the combined integration and validate its lifecycle/trust interactions using the accepted combined suite and installed CLI witness. The final combined wheel and built-product launch remain phase-five delivery gates.

- [x] 3.5 Verify the accepted combined implementation, then sync stable requirements/context and archive only after implementation acceptance.
- [x] 3.6 Run `make package` for the scoped implementation.
