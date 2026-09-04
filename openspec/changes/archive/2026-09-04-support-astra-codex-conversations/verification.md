# Astra conversation protocol verification

## Completeness and coherence

All five scoped requirements are implemented, covered by local regressions and synchronized to the main specs. Async continuity, steering ownership and inherited configuration policy form one Responses lifecycle change. Catalog and pricing additions are separate.

## Local checks

- All 92 tests in the eight new test_astra_* unit/integration files passed.
- 326 existing tests passed in test_request_policy, test_proxy_api_responses_contract, test_openai_compat_features and test_model_source_routing.
- Ruff check and format check passed for all 22 changed/new Python files.
- Ty passed for all 14 changed/new app files.
- check_proxy_architecture.py, check_cancellation_safety.py and git diff --check passed.
- openspec validate archive/2026-09-04-support-astra-codex-conversations --type change --strict --json --no-interactive passed.

The test invocations used `-B -m pytest -p no:cacheprovider`, the respective files above and `-q`. These results were obtained on the independent protocol branch without the catalog/pricing patch. Counts describe separate, non-overlapping selections.

## Requirement coverage

- Async function/custom calls survive intervening turns; typed actual outputs consume matching pending calls, while synchronous calls retain interruption repair. Durable manifests unable to represent async state are not authoritative.
- Steering tests cover automatic/explicit successors, multiple accepted steers, early required outputs, unrelated queued creates, errors before acceptance, anonymous error correlation, wrong lanes and limits.
- Admission, account lease, heartbeat and usage ownership are exercised through partial failures and cancellation. Disconnects do not replay accepted steering.
- Request policies cover allowed/enforced updates, inherited response/conversation anchors, proxy-injected anchors, repeated Ultra preparation and final wire normalization.
- Source routing tests preserve source-specific contracts and require subscription validation after selection. Explicit compaction stays on Responses; automatic and standalone compaction with updates are rejected.

## Scope and limits

Verified on 2026-09-04 against baseline dd28d7dff94cdd4919067c1986fd9606b9bbc6b9 using deterministic local fixtures and an existing Python virtual environment. No provider call or production change is part of this verification. Account availability and subscription acceptance remain unverified.

Strict global spec validation reports 48 passing specs and 10 failing specs, with 28 errors. A temporary extraction of the baseline produces exactly the same error tuples (spec, level, path, message). The scoped archived change passes strict validation. Existing global failures are not repaired by this feature.

The complete local-ci gate and current-head GitHub CI/review gates remain pending before a review-ready publication. Local test completion does not establish deployment readiness.
