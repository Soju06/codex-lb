## 1. Backend

- [x] 1.1 Accept repeated `upstream` values on `GET /api/request-logs` and filter by `model_source_id` (`subscription_pool` = NULL), OR within the facet and AND with the other filters.
- [x] 1.2 Count upstream-filtered listings from raw rows and include the selection in the listing-count cache key.
- [x] 1.3 Return an `upstreams` facet from `GET /api/request-logs/options` that ignores its own selection, resolves labels from `model_sources` (id, name, kind only), and keeps deleted sources with their persisted kind.
- [x] 1.4 Use indexed probes for the unfiltered upstream facet.

## 2. Dashboard

- [x] 2.1 Add a URL-backed Upstream multi-select to the request-log filters, hidden when at most one upstream is observed and none is selected.
- [x] 2.2 Show a compact model-source badge on source-routed rows and the upstream ID and kind in request details.
- [x] 2.3 Localize new labels in `en`, `ko`, and `zh-CN`.

## 3. Verification

- [x] 3.1 Add backend tests for filtering, counts, cache isolation, facet shape, self-facet exemption, deleted sources, soft-deleted rows, and skip-scan parity.
- [x] 3.2 Add dashboard tests for URL persistence, reset, hidden single option, labels, badge and detail rendering, and older responses.
- [x] 3.3 Run lint, type checks, tests, and strict OpenSpec validation; capture before/after screenshots.
