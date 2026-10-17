# Tasks

## 1. Shared classification

- [x] 1.1 `AffinityObservation.from_policy` classifies from the policy;
  add `AFFINITY_SOURCES` as the documented domain.
- [x] 1.2 Add `AffinityObservation.retaining_source` for the sites that
  deliberately carry a prior source across a routing adjustment.
- [x] 1.3 Delete the four ladders and the four `had_prompt_cache_key` locals;
  feed the shape trace from the observation.

## 2. Resolver-recorded cache-key provenance

- [x] 2.1 `_AffinityPolicy.prompt_cache_key_source`, set by
  `_sticky_key_for_responses_request` and `_sticky_key_for_compact_request`
  from `_resolve_prompt_cache_key`.

## 3. Verification

- [x] 3.1 Tests: one source per signal; all four transports agree for one
  policy; blank hint and post-resolution payload reads record `derived`;
  retained-source adjustment still reports the original signal.
- [x] 3.2 `ruff check` / `ruff format --check`, `check_proxy_architecture.py`,
  `ty check`, proxy unit suites, `openspec validate --strict`.
