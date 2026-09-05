## 1. Establish the regression

- [ ] 1.1 Add controlled-time HTTP regression coverage for first event, created and first content at distinct offsets, retaining null/zero semantics and the existing queue anchor. Obtain red on omitted fields.
- [ ] 1.2 Record the existing nearest coverage and demonstrate that the new check fails when the claimed production behavior is severed.

## 2. Implement the owning change

- [ ] 2.1 Record the two existing nullable phase values at upstream observation and pass them to the existing request-log owner, preserving first-event and later verbatim/lazy paths.
- [ ] 2.2 Prove timing-path and fast-path sensitivity; check persisted fields and existing Prometheus export without adding labels or schema.

## 3. Validate and prepare delivery

- [ ] 3.1 Run `uv run pytest tests/integration/test_proxy_responses.py tests/unit/test_proxy_utils.py tests/unit/test_metrics.py -k "latency or timing or ttft or first_token or verbatim or phase" -q`, extending the selection to include any new regression node outside the current names; run `make lint` and `make typecheck`.
- [ ] 3.2 Run `npx --yes @fission-ai/openspec@1.11.0 validate observe-http-upstream-latency --strict --no-interactive` and `npx --yes @fission-ai/openspec@1.11.0 validate --specs --strict --no-interactive`; verify then sync stable requirements/context and archive only after implementation acceptance.
- [ ] 3.3 Refresh the exact-root GitNexus index/content witness, run `gitnexus detect-changes --scope all --repo /Users/dpearson/repos/codex-lb/.agents/worktrees/issue-2029-http-latency`, inspect the scoped diff and commit one cohesive implementation change locally.
- [ ] 3.4 Supply this accepted branch head to the later combined integration build; run shared HTTP owner/timing interaction regressions and package one reviewable wheel without installing or restarting the user's service.
