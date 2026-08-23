## 1. Baseline and regression

- [x] 1.1 Confirm the pinned baseline line counts, `select_account()` span,
  current checker result, and absence of a superseding active
  `proxy-architecture` delta.
  - Live `upstream/main` remained `d4b00fd`; the pinned 2,612/3,187 source
    counts and passing checker evidence were reusable, `select_account()` spans
    478 lines, and no active delta supersedes the 2,600/3,021/527 contract.
- [x] 1.2 Replace fixture-only checker constants with a red regression proving
  that the checker follows the OpenSpec-owned threshold block and fails closed
  for invalid definitions while continuing unrelated checks.

## 2. Bind architecture checks to OpenSpec

- [x] 2.1 Sync the complete modified fitness-gate requirement to the main
  `proxy-architecture` spec and record the stable SSOT/failure-mode rationale in
  its context document.
- [x] 2.2 Implement one strict `tomllib` threshold loader and switch all six
  limit checks to the loaded values without retaining numeric Python copies.
- [x] 2.3 Preserve deterministic multi-failure output, dependent-check skipping,
  and the existing successful-check output contract.

## 3. Restore the ProxyService façade ratchet

- [x] 3.1 Move the API-key lease token-estimate helpers into
  `_service/api_key_usage.py` and re-export them from `service.py` without
  changing caller behavior or import compatibility.
- [x] 3.2 Add or retain focused characterization for default, bounded, and
  compatibility-facade lease estimates.

## 4. Restore the load-balancer module ratchet

- [x] 4.1 Add `_load_balancer/model_eligibility.py` and move the cohesive model
  catalog plus additional-quota eligibility helpers without changing their
  bodies or results.
- [x] 4.2 Preserve `load_balancer.py` data/helper re-exports and registry-factory
  monkeypatch seams through typed façade wrappers.
- [x] 4.3 Run focused model filtering, catalog-omission, additional-quota,
  sticky/unbound selection, persistence, and load-balancer integration tests.

## 5. Verification

- [x] 5.1 Confirm `service.py <= 2600`, `load_balancer.py <= 3021`, and
  `LoadBalancer.select_account() <= 527`, then run the real repository
  architecture checker and its full unit test file.
- [x] 5.2 Run strict scoped OpenSpec validation, affected main-spec validation,
  changed-file Ruff lint/format, focused `ty`, and `git diff --check`.
- [x] 5.3 Complete an independent review of the committed diff and resolve every
  actionable in-scope finding before publication.
