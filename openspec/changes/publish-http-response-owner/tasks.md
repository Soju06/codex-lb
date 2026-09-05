## 1. Establish the regression

- [x] 1.1 Add actual HTTP-route red coverage at response.created before terminal and at terminal/EOF with detached persistence pending. Use real Uvicorn sockets and unchanged selection/service/persistence paths; ASGITransport buffering cannot prove the early delivery boundary.
- [x] 1.2 Record the existing nearest coverage and demonstrate that the new check fails when the claimed production behavior is severed.

## 2. Implement the owning change

- [x] 2.1 Publish the authoritative upstream lifecycle ID through the existing bounded owner-cache method before its downstream event, preserving account/API-key/session scope. Keep local producer provenance in the existing parsed SSE carrier, including unmarked local errors, without changing wire bytes or retry markers.
- [x] 2.2 Confirm publication-disabled sensitivity, durable miss fallback and genuinely unknown-owner behavior; extend the nearest scoped-owner/cancellation tests only for uncovered behavior.

## 3. Validate and prepare delivery

- [x] 3.1 Run `uv run pytest tests/integration/test_proxy_responses.py tests/unit/test_proxy_utils.py -k "owner or previous_response or cancellation or persistence" -q`, extending the selection to include any new regression node outside the current names; run `make lint` and `make typecheck`.
- [x] 3.2 Run `npx --yes @fission-ai/openspec@1.11.0 validate publish-http-response-owner --strict --no-interactive` and `npx --yes @fission-ai/openspec@1.11.0 validate --specs --strict --no-interactive`; record the unchanged full-strict baseline warnings separately.
- [x] 3.3 Refresh the exact-root GitNexus index/content witness, run `gitnexus detect-changes --scope all --repo /Users/dpearson/repos/codex-lb/.agents/worktrees/issue-2029-http-owner`, inspect the scoped diff and commit one cohesive implementation change locally.
- [ ] 3.4 Supply this accepted branch head to the later combined integration build; run shared HTTP owner/timing interaction regressions and package one reviewable wheel without installing or restarting the user's service.

- [ ] 3.5 After combined acceptance, verify this change, sync stable requirements/context and archive through the owning OpenSpec workflow.
