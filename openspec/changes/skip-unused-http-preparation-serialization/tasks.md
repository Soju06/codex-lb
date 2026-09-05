## 1. Establish the regression

- [x] 1.1 Extend existing core HTTP tests with a representative large real-shaped payload and local origin; capture exact body identity and red evidence of unused owning-preparation serializations.
- [x] 1.2 Record the existing nearest coverage and demonstrate that the new check fails when the claimed production behavior is severed.

## 2. Implement the owning change

- [x] 2.1 Move full-body serialization behind its active transport/size/trace/native consumers; preserve exact payload construction, fallback mutations and HTTP wire serialization.
- [x] 2.2 Verify exact transmitted bytes, a meaningful serialization ablation and existing enabled-consumer/WS-budget cases; use no elapsed-time threshold or combinatorial matrix.

## 3. Validate and prepare delivery

- [x] 3.1 Run `uv run pytest tests/unit/test_proxy_utils.py tests/unit/test_codex_upstream_paths.py tests/unit/test_proxy_upstream_fingerprint.py tests/unit/test_native_egress.py tests/integration/test_proxy_responses.py -q`, extending the selection to include any new regression node outside the current names; run `make lint` and `make typecheck`.
- [x] 3.2 Run OpenSpec 1.11.0 strict validation for this change and `outbound-http-clients`, plus CI-equivalent `validate --specs`; record the unchanged 22 placeholder-purpose warnings from full strict validation.
- [x] 3.3 Refresh the exact-root GitNexus index/content witness, run `gitnexus detect-changes --scope all --repo /Users/dpearson/repos/codex-lb/.agents/worktrees/issue-2029-http-preparation`, inspect the scoped diff and commit one cohesive implementation change locally.
- [ ] 3.4 Supply this accepted branch head to the later combined integration build; run shared HTTP owner/timing interaction regressions and package one reviewable wheel without installing or restarting the user's service.
- [ ] 3.5 After combined implementation acceptance, verify the change, sync stable requirements/context and archive through the owning OpenSpec workflow.
