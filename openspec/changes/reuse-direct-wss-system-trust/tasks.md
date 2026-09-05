## 1. Establish the regression

- [ ] 1.1 Add a repeated-real-WSS-open red check, preserving trusted success/untrusted and wrong-host rejection; reuse existing plain-WS and cancellation coverage.
- [ ] 1.2 Record the existing nearest coverage and demonstrate that the new check fails when the claimed production behavior is severed.

## 2. Implement the owning change

- [ ] 2.1 Add the private system-default cache, normal lifecycle warm/reset, and conditional Python WSS TLS argument without changing aiohttp, routed or native policy.
- [ ] 2.2 Prove cache/call-site sensitivity by disabling reuse; verify close/reinitialize and current verification policy without a trust-matrix expansion.

## 3. Validate and prepare delivery

- [ ] 3.1 Run `uv run pytest tests/unit/test_http_client.py tests/unit/test_proxy_websocket_client.py tests/unit/test_websocket_upstream_transport_observability.py -q`, extending the selection to include any new regression node outside the current names; run `make lint` and `make typecheck`.
- [ ] 3.2 Run `npx --yes @fission-ai/openspec@1.11.0 validate reuse-direct-wss-system-trust --strict --no-interactive` and `npx --yes @fission-ai/openspec@1.11.0 validate --specs --strict --no-interactive`; verify then sync stable requirements/context and archive only after implementation acceptance.
- [ ] 3.3 Refresh the exact-root GitNexus index/content witness, run `gitnexus detect-changes --scope all --repo /Users/dpearson/repos/codex-lb/.agents/worktrees/issue-2029-wss`, inspect the scoped diff and commit one cohesive implementation change locally.
- [ ] 3.4 Supply this accepted branch head to the later combined integration build; run shared HTTP owner/timing interaction regressions and package one reviewable wheel without installing or restarting the user's service.
