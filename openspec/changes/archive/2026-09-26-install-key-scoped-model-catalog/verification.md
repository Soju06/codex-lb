# Verification: install-key-scoped-model-catalog

## Completeness

All five tasks are complete. Both changed requirements in `api-key-dashboard` are synchronized to the main spec, with stable setup and recovery context. No migrations or new application settings are required.

## Correctness

- 42 installer/key-dashboard tests passed: Bash execution on both Unix variants; safe quoting, Unicode and paths; current-key authenticated catalog download; metadata preservation; rerun refresh; mixed and native-only transport policy; old-file backups/private modes; symlink/non-file/backup failures; invalid/empty catalog and missing selected model; redirect/key errors without credential output; inactive/expired key rejection; scoped custom aliases with global auth both enabled and disabled; exhausted inference limits do not block catalog refresh or create reservations.
- 71 existing `/v1/models` and native catalog tests passed in the broader catalog run. That run initially had one new test assertion failure because gzip middleware appends `Accept-Encoding` to `Vary`; the test now checks membership and the complete 42-test installer suite passes after correction.
- 16 frontend installer/key-dashboard tests passed. No component/layout changes were needed; English, Korean and Chinese prerequisite/recovery guidance now mentions the catalog and Python 3.
- PowerShell 7.4.6 on Linux parsed each full Windows script and executed its exact catalog program for success, invalid key, redirect, empty catalog and unavailable model. Actual Windows ACL creation/replacement and Windows PowerShell 5.1 execution were not tested on this Linux host; existing ACL logic is preserved and catalog files use the same protection.
- Ruff lint and formatting passed for all touched Python files; targeted `ty check` passed, including the proxy API and application routing. `git diff --check` passed.
- Strict OpenSpec validation passes for this change and the owning `api-key-dashboard` spec. Whole-repository spec validation still reports exactly the same 15 pre-existing failures; error details match the baseline.

## Coherence

The installer endpoint reuses the native catalog serializer and key/source filters, without duplicating serialization or reserving inference limits. A dedicated self-service catalog route is needed because ordinary proxy catalog routes intentionally ignore credentials when global proxy authentication is disabled. No ordinary proxy authentication behavior changed.

## Visual evidence

- `screenshots/before.png`: current Install layout with exact prior prerequisite/backup copy restored in the browser from HEAD; fixture-only credentials.
- `screenshots/after.png`: updated desktop guidance.
- `screenshots/after-mobile.png`: updated 390-pixel layout, checked for horizontal overflow and visually inspected.

## Assessment

No critical verification issues remain. Platform limitation: full Windows file/ACL operations await native Windows execution. Implementation and local verification are complete. Commit/push and deployment outcomes are tracked separately by the operator.

## Alias visibility recheck

Three additional API regressions pass: the administrator picker offers both public names when source rows explicitly declare an alias and an identity model, while installer/native and OpenAI-compatible catalogs expose only the names allowed by the API key. Allowing only the upstream name does not grant access to the alias; forwarding uses the mapping only after public-name policy and source selection.
